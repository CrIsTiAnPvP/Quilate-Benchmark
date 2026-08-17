"""La comprobación de si hay una versión más nueva publicada.

Tres cosas que este módulo promete y que aquí se fijan, en orden de importancia:

- **No puede estropear una ejecución.** El análisis ya está hecho cuando se
  pregunta. Todo error se traga y se devuelve un estado utilizable.
- **No pregunta en cada ejecución.** La respuesta vale un día, y el fallo
  también se guarda: sin eso un equipo sin conexión pagaría el timeout entero en
  cada arranque, para siempre.
- **`--no-net` corta la consulta.** `comprobar` no puede llegar a la red sin que
  quien llama lo autorice, y por eso el parámetro no tiene valor por defecto.

La comparación de versiones se prueba aparte y con saña porque es donde está el
fallo silencioso que importa: en orden alfabético `"2.10.0" > "2.9.0"` es falso,
y esa comparación callaría justo la versión que había que anunciar.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from quilate import update_check
from quilate.const import APP_VERSION
from quilate.update_check import (VIGENCIA, comprobar, consultar, hay_novedad,
                                  linea_de_aviso, version_tuple)

AHORA = datetime(2026, 8, 18, 12, 0, 0)


class ComparacionDeVersiones(unittest.TestCase):
    def test_lee_las_dos_formas_de_nombrar_una_version(self):
        # GitHub etiqueta `v2.7.0`; `const.py` declara `2.7.0`.
        self.assertEqual(version_tuple("v2.7.0"), (2, 7, 0))
        self.assertEqual(version_tuple("2.7.0"), (2, 7, 0))

    def test_lo_que_no_es_una_version_da_tupla_vacia(self):
        # `()` es menor que cualquier otra tupla, así que una etiqueta rara nunca
        # se puede leer como «hay algo más nuevo». Es la respuesta prudente.
        for basura in ("", None, "latest", "no-es-una-version"):
            with self.subTest(basura=basura):
                self.assertEqual(version_tuple(basura), ())
                self.assertFalse(hay_novedad(basura, "2.7.0"))

    def test_no_compara_como_texto(self):
        # El fallo que este módulo existe para no tener: en orden alfabético
        # "2.10.0" < "2.9.0", y la versión que había que anunciar se callaría.
        self.assertTrue(hay_novedad("2.10.0", "2.9.0"))
        self.assertFalse(hay_novedad("2.9.0", "2.10.0"))

    def test_la_misma_version_no_es_novedad(self):
        self.assertFalse(hay_novedad("2.8.0", "2.8.0"))

    def test_una_version_anterior_no_es_novedad(self):
        self.assertFalse(hay_novedad("2.7.0", "2.8.0"))

    def test_por_defecto_compara_con_la_instalada(self):
        self.assertFalse(hay_novedad(APP_VERSION))


class Consulta(unittest.TestCase):
    """La única función del módulo que abre una conexión."""

    def responder(self, cuerpo: bytes, status: int = 200):
        respuesta = mock.MagicMock()
        respuesta.read.return_value = cuerpo
        respuesta.__enter__.return_value = respuesta
        respuesta.status = status
        return mock.patch("urllib.request.urlopen", return_value=respuesta)

    def test_devuelve_la_etiqueta_sin_la_v(self):
        with self.responder(json.dumps({"tag_name": "v2.9.0"}).encode()):
            self.assertEqual(consultar(), ("2.9.0", None))

    def test_acepta_name_si_no_hay_tag_name(self):
        with self.responder(json.dumps({"name": "2.9.0"}).encode()):
            self.assertEqual(consultar()[0], "2.9.0")

    def test_un_error_http_no_lanza(self):
        fallo = urllib.error.HTTPError("u", 403, "Forbidden", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=fallo):
            version, error = consultar()
        self.assertIsNone(version)
        self.assertIn("403", error)

    def test_sin_red_no_lanza(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("sin DNS")):
            version, error = consultar()
        self.assertIsNone(version)
        self.assertIn("no se ha podido contactar", error)

    def test_un_timeout_no_lanza(self):
        # El timeout del socket no siempre llega envuelto en URLError.
        with mock.patch("urllib.request.urlopen", side_effect=OSError("timed out")):
            self.assertIsNone(consultar()[0])

    def test_una_respuesta_que_no_es_json_no_lanza(self):
        with self.responder(b"<html>vaya</html>"):
            version, error = consultar()
        self.assertIsNone(version)
        self.assertIn("no se ha podido leer", error)

    def test_un_json_que_no_es_un_objeto_no_lanza(self):
        with self.responder(b"[1, 2, 3]"):
            self.assertIsNone(consultar()[0])

    def test_una_etiqueta_ilegible_no_cuenta_como_novedad(self):
        with self.responder(json.dumps({"tag_name": "ultima"}).encode()):
            version, error = consultar()
        self.assertIsNone(version)
        self.assertIn("no declara una versión legible", error)

    def test_no_manda_la_version_instalada(self):
        # Para saber si hay algo más nuevo no hace falta decirle a nadie qué se
        # tiene. Es una GET sin cuerpo, sin parámetros y sin autenticación.
        with self.responder(json.dumps({"tag_name": "v2.9.0"}).encode()) as urlopen:
            consultar()
        peticion = urlopen.call_args.args[0]
        self.assertEqual(peticion.get_method(), "GET")
        self.assertIsNone(peticion.data)
        self.assertNotIn(APP_VERSION, peticion.full_url)
        self.assertNotIn(APP_VERSION, json.dumps(dict(peticion.headers)))

    def test_se_identifica_y_no_se_disfraza(self):
        with self.responder(json.dumps({"tag_name": "v2.9.0"}).encode()) as urlopen:
            consultar()
        agente = dict(urlopen.call_args.args[0].headers)["User-agent"]
        self.assertIn("Quilate", agente)
        self.assertNotIn("Mozilla", agente)

    def test_recorta_la_respuesta(self):
        # Un JSON de release ronda los 3 KB. No hay razón para leer más de lo que
        # cabe en la memoria de un .exe de siete megas.
        with self.responder(json.dumps({"tag_name": "v2.9.0"}).encode()) as urlopen:
            consultar()
        respuesta = urlopen.return_value
        self.assertEqual(respuesta.read.call_args.args[0], 64 * 1024)


class _ConCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name) / "version_check.json"

    def escribir(self, **campos):
        self.cache.write_text(json.dumps(campos), encoding="utf-8")

    def leer(self) -> dict:
        return json.loads(self.cache.read_text(encoding="utf-8"))


class Permiso(_ConCache):
    def test_sin_permiso_no_abre_ninguna_conexion(self):
        # La parte de `--no-net` que sí sigue significando lo que decía.
        consulta = mock.Mock()
        estado = comprobar(False, self.cache, AHORA, consulta)
        consulta.assert_not_called()
        self.assertIn("sin permiso", estado["error"])
        self.assertIsNone(estado["source"])

    def test_sin_permiso_sigue_leyendo_la_cache_fresca(self):
        # Una respuesta que ya está en el disco no cuesta ninguna conexión, y
        # callarla sería esconder un dato que ya se tiene.
        self.escribir(checked_at=AHORA.isoformat(), latest="2.9.0", error=None)
        consulta = mock.Mock()
        estado = comprobar(False, self.cache, AHORA + timedelta(hours=1), consulta)
        consulta.assert_not_called()
        self.assertEqual(estado["latest"], "2.9.0")
        self.assertEqual(estado["source"], "caché")

    def test_sin_permiso_la_cache_caducada_se_da_como_lo_que_es(self):
        self.escribir(checked_at=AHORA.isoformat(), latest="2.9.0")
        estado = comprobar(False, self.cache, AHORA + timedelta(days=3), mock.Mock())
        self.assertEqual(estado["latest"], "2.9.0")
        self.assertEqual(estado["source"], "caché caducada")
        self.assertTrue(estado["outdated"])

    def test_con_permiso_pregunta(self):
        consulta = mock.Mock(return_value=("2.9.0", None))
        estado = comprobar(True, self.cache, AHORA, consulta)
        consulta.assert_called_once()
        self.assertEqual(estado["source"], "red")
        self.assertEqual(estado["latest"], "2.9.0")


class Cache(_ConCache):
    def test_no_pregunta_dos_veces_en_24h(self):
        consulta = mock.Mock(return_value=("2.9.0", None))
        comprobar(True, self.cache, AHORA, consulta)
        comprobar(True, self.cache, AHORA + VIGENCIA - timedelta(minutes=1), consulta)
        self.assertEqual(consulta.call_count, 1)

    def test_pasadas_24h_vuelve_a_preguntar(self):
        consulta = mock.Mock(return_value=("2.9.0", None))
        comprobar(True, self.cache, AHORA, consulta)
        comprobar(True, self.cache, AHORA + VIGENCIA + timedelta(minutes=1), consulta)
        self.assertEqual(consulta.call_count, 2)

    def test_el_fallo_tambien_se_guarda(self):
        # Sin esto, un equipo sin conexión paga los tres segundos del timeout en
        # cada arranque, para siempre. Con esto, lo intenta una vez al día.
        consulta = mock.Mock(return_value=(None, "sin red"))
        comprobar(True, self.cache, AHORA, consulta)
        self.assertIsNone(self.leer()["latest"])
        self.assertEqual(self.leer()["error"], "sin red")
        comprobar(True, self.cache, AHORA + timedelta(hours=1), consulta)
        self.assertEqual(consulta.call_count, 1)

    def test_una_fecha_en_el_futuro_cuenta_como_caducada(self):
        # Un reloj que se adelantó una vez dejaría la caché válida durante años.
        self.escribir(checked_at=(AHORA + timedelta(days=400)).isoformat(),
                      latest="2.9.0")
        consulta = mock.Mock(return_value=("2.9.0", None))
        comprobar(True, self.cache, AHORA, consulta)
        consulta.assert_called_once()

    def test_una_cache_corrupta_se_trata_como_si_no_hubiera(self):
        self.cache.write_text("{ a medias", encoding="utf-8")
        consulta = mock.Mock(return_value=("2.9.0", None))
        estado = comprobar(True, self.cache, AHORA, consulta)
        self.assertEqual(estado["latest"], "2.9.0")

    def test_una_marca_que_no_es_texto_no_revienta(self):
        for marca in (12345, None, "el martes"):
            with self.subTest(marca=marca):
                self.escribir(checked_at=marca, latest="2.9.0")
                consulta = mock.Mock(return_value=("2.9.0", None))
                comprobar(True, self.cache, AHORA, consulta)
                consulta.assert_called_once()

    def test_no_poder_escribir_la_cache_no_revienta(self):
        consulta = mock.Mock(return_value=("2.9.0", None))
        with mock.patch.object(Path, "write_text", side_effect=OSError):
            estado = comprobar(True, self.cache, AHORA, consulta)
        self.assertEqual(estado["latest"], "2.9.0")

    def test_la_cache_va_al_lado_del_historico(self):
        self.assertEqual(update_check.cache_path().name, "version_check.json")
        self.assertEqual(update_check.cache_path().parent.name, "Quilate")


class NoPuedeEstropearUnaEjecucion(_ConCache):
    def test_una_consulta_que_falla_devuelve_un_estado_utilizable(self):
        estado = comprobar(True, self.cache, AHORA,
                           mock.Mock(return_value=(None, "sin red")))
        self.assertEqual(estado["current"], APP_VERSION)
        self.assertIsNone(estado["latest"])
        self.assertFalse(estado["outdated"])
        self.assertIsNone(linea_de_aviso(estado))

    def test_devuelve_siempre_las_mismas_claves(self):
        claves = {"current", "latest", "outdated", "checked_at", "source",
                  "error", "url"}
        for permitido, consulta in ((False, mock.Mock()),
                                    (True, mock.Mock(return_value=("2.9.0", None))),
                                    (True, mock.Mock(return_value=(None, "sin red")))):
            with self.subTest(permitido=permitido):
                self.assertEqual(set(comprobar(permitido, self.cache, AHORA,
                                               consulta)), claves)


class LineaDeAviso(unittest.TestCase):
    def test_sin_novedad_no_escribe_nada(self):
        # El informe ya es largo: ni siquiera un «estás al día».
        self.assertIsNone(linea_de_aviso({"outdated": False, "latest": "2.7.0"}))
        self.assertIsNone(linea_de_aviso({"outdated": True, "latest": None}))
        self.assertIsNone(linea_de_aviso({}))

    def test_dice_las_dos_versiones_y_donde_bajarla(self):
        linea = linea_de_aviso({"outdated": True, "latest": "2.9.0",
                                "current": "2.8.0", "url": "https://ejemplo/r"})
        self.assertIn("2.9.0", linea)
        self.assertIn("2.8.0", linea)
        self.assertIn("https://ejemplo/r", linea)

    def test_dice_cuando_el_dato_es_de_ayer(self):
        # Que el informe pueda decir la verdad sobre su propia frescura.
        linea = linea_de_aviso({"outdated": True, "latest": "2.9.0",
                                "current": "2.8.0", "url": "u",
                                "source": "caché caducada"})
        self.assertIn("no se ha consultado ahora", linea)


if __name__ == "__main__":
    unittest.main()
