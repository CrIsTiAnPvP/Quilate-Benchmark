"""Histórico de ejecuciones y detección de deriva.

`--compare` responde a «¿ha servido lo que acabo de aplicar?». El histórico
responde a la otra pregunta, la que un equipo acaba haciéndose: **¿voy a peor?**
Eso no se ve en dos puntos, se ve en la serie, y solo significa algo si el
criterio para llamarlo tendencia es más exigente que trazar una recta entre dos
medidas cualesquiera.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from quilate.benchmark import (REFERENCE, REFERENCE_DATE, REFERENCE_ORIGIN,
                               REFERENCE_STALE_MONTHS, reference_age_months,
                               reference_is_stale)
from quilate import history
from quilate.history import (DERIVA_MINIMA_PCT, MAX_ENTRADAS, MINIMO_PARA_TENDENCIA,
                             _resumen, append, deriva, history_path, load, report, serie)
from datetime import date


def ejecucion(overall=100.0, boot=None, temp=None, **extra) -> dict:
    payload = {
        "meta": {"generated_at": "2026-07-01T10:00:00", "version": "2.4.0"},
        "scores": {"overall": overall,
                   "components": {"cpu_single": overall, "disk": overall / 2}},
        "findings": [{"id": "x"}],
        "metrics": {},
        "boot": {"seconds": boot},
        "dispersion": {"disk_read": {"spread_pct": 4.0}},
        "ambient_load": {"antes": {"cpu_pct": 3.0}},
    }
    if temp is not None:
        payload["metrics"]["cpu_temp_load"] = {"value": temp}
    payload.update(extra)
    return payload


class Resumen(unittest.TestCase):
    def test_guarda_solo_cifras(self):
        entrada = _resumen(ejecucion(overall=120.0, boot=31.0, temp=68.0))
        self.assertEqual(entrada["overall"], 120.0)
        self.assertEqual(entrada["boot_seconds"], 31.0)
        self.assertEqual(entrada["cpu_temp"], 68.0)
        self.assertEqual(entrada["max_spread_pct"], 4.0)
        self.assertEqual(entrada["busy_pct"], 3.0)

    def test_no_arrastra_datos_personales(self):
        # El histórico se acumula para siempre: aquí no puede acabar ni una ruta
        # ni un nombre de programa. Para eso está el JSON completo de cada vez.
        payload = ejecucion()
        payload["system"] = {"hostname": "PC-DE-ALGUIEN", "user": "alguien"}
        payload["top_processes"] = [{"name": "loquesea.exe", "rss": 1}]
        payload["startup_items"] = [{"name": "programa", "location": "C:\\Users\\x"}]
        volcado = json.dumps(_resumen(payload)).lower()
        for prohibido in ("pc-de-alguien", "alguien", "loquesea", "c:\\\\users"):
            self.assertNotIn(prohibido, volcado, f"se ha colado «{prohibido}»")

    def test_campos_ausentes_no_revientan(self):
        self.assertIn("at", _resumen({}))


class FicheroDelHistorico(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ruta = Path(self.dir.name) / "historico.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_ida_y_vuelta(self):
        append(ejecucion(overall=90.0), self.ruta)
        append(ejecucion(overall=95.0), self.ruta)
        entradas = load(self.ruta)
        self.assertEqual([e["overall"] for e in entradas], [90.0, 95.0])

    def test_una_linea_corrupta_no_invalida_el_resto(self):
        # Un corte a media escritura no puede tirar el histórico entero.
        append(ejecucion(overall=90.0), self.ruta)
        with self.ruta.open("a", encoding="utf-8") as fh:
            fh.write("{esto no es json\n")
        append(ejecucion(overall=95.0), self.ruta)
        self.assertEqual([e["overall"] for e in load(self.ruta)], [90.0, 95.0])

    def test_fichero_inexistente_no_es_un_error(self):
        self.assertEqual(load(Path(self.dir.name) / "no_existe.jsonl"), [])

    def test_se_recorta_al_maximo(self):
        for i in range(MAX_ENTRADAS + 20):
            append(ejecucion(overall=float(i)), self.ruta)
        entradas = load(self.ruta)
        self.assertEqual(len(entradas), MAX_ENTRADAS)
        # Se conservan las últimas, no las primeras.
        self.assertEqual(entradas[-1]["overall"], float(MAX_ENTRADAS + 19))

    def test_no_poder_escribir_no_tumba_nada(self):
        # El análisis ya está hecho: que el histórico falle no puede perderlo.
        imposible = Path(self.dir.name) / "no" / "existe" / "\0" / "h.jsonl"
        self.assertIsNone(append(ejecucion(), imposible))


class UbicacionDelFichero(unittest.TestCase):
    """La base sale del entorno, y el proceso elevado hereda el del que no lo está."""

    VARIABLE = "LOCALAPPDATA" if history.IS_WINDOWS else "XDG_DATA_HOME"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.entorno = dict(os.environ)
        self.admin_original = history.is_admin
        history.is_admin = lambda: False
        # Donde tiene que acabar el fichero cuando la base no es de fiar.
        raiz = Path.home() if history.IS_WINDOWS else Path.home() / ".local" / "share"
        self.respaldo = raiz / "Quilate" / "historico.jsonl"

    def tearDown(self):
        history.is_admin = self.admin_original
        os.environ.clear()
        os.environ.update(self.entorno)
        self.dir.cleanup()

    def _con(self, valor: str | None) -> Path:
        if valor is None:
            os.environ.pop(self.VARIABLE, None)
        else:
            os.environ[self.VARIABLE] = valor
        return history_path()

    def test_una_base_normal_se_respeta(self):
        self.assertEqual(self._con(self.dir.name),
                         Path(self.dir.name) / "Quilate" / "historico.jsonl")

    def test_una_base_relativa_no_vale(self):
        # Relativa quiere decir «donde el proceso esté trabajando ahora».
        self.assertEqual(self._con(os.path.join("datos", "quilate")), self.respaldo)

    def test_una_base_que_no_existe_no_vale(self):
        self.assertEqual(self._con(str(Path(self.dir.name) / "no" / "existe")),
                         self.respaldo)

    def test_una_base_vacia_no_vale(self):
        self.assertEqual(self._con(""), self.respaldo)

    def test_sin_la_variable_se_usa_el_perfil(self):
        self.assertEqual(self._con(None), self.respaldo)

    def test_con_privilegios_solo_se_escribe_dentro_del_perfil(self):
        # Escribir como Administrador donde diga una variable de entorno que el
        # usuario controla es cómo se crean directorios en zonas protegidas.
        # La raíz del volumen existe y es absoluta —pasa las dos primeras
        # comprobaciones— pero no cuelga del perfil de nadie.
        history.is_admin = lambda: True
        self.assertEqual(self._con(Path.home().anchor), self.respaldo)

    def test_sin_privilegios_esa_misma_base_se_acepta(self):
        # La restricción extra es por la elevación, no por desconfiar del
        # usuario: sin privilegios no puede escribir donde no le dejen igualmente.
        raiz = Path.home().anchor
        self.assertEqual(self._con(raiz), Path(raiz) / "Quilate" / "historico.jsonl")

    def test_con_privilegios_una_base_del_perfil_sigue_valiendo(self):
        # La comprobación es «dentro del perfil», no «solo el perfil»: en un
        # equipo normal LOCALAPPDATA es una subcarpeta y tiene que seguir yendo ahí.
        try:
            Path(self.dir.name).resolve().relative_to(Path.home().resolve())
        except ValueError:
            self.skipTest("el directorio temporal de este sistema no cuelga del perfil")
        history.is_admin = lambda: True
        self.assertEqual(self._con(self.dir.name),
                         Path(self.dir.name) / "Quilate" / "historico.jsonl")


class Deriva(unittest.TestCase):
    def _entradas(self, valores, clave="overall"):
        return [{"at": f"2026-01-{i + 1:02d}T10:00:00", clave: v}
                for i, v in enumerate(valores)]

    def test_con_pocas_medidas_no_se_habla_de_tendencia(self):
        # Con dos puntos siempre se puede trazar una recta.
        self.assertIsNone(deriva(self._entradas([100, 50]), "overall"))
        pocas = self._entradas([100] * (MINIMO_PARA_TENDENCIA - 1))
        self.assertIsNone(deriva(pocas, "overall"))

    def test_serie_estable(self):
        d = deriva(self._entradas([100, 101, 99, 100, 102, 98]), "overall")
        self.assertEqual(d["direction"], "estable")

    def test_degradacion(self):
        d = deriva(self._entradas([100, 98, 95, 88, 80, 72]), "overall")
        self.assertEqual(d["direction"], "degradación")
        self.assertLess(d["change_pct"], 0)

    def test_mejora(self):
        d = deriva(self._entradas([70, 75, 80, 95, 100, 105]), "overall")
        self.assertEqual(d["direction"], "mejora")
        self.assertGreater(d["change_pct"], 0)

    def test_un_valor_raro_no_decide_solo(self):
        # Una ejecución hecha con el equipo ocupado no puede declarar que el
        # equipo se degrada: por eso se comparan bloques y no extremos.
        d = deriva(self._entradas([100, 100, 100, 100, 100, 20]), "overall")
        self.assertEqual(d["direction"], "estable")

    def test_en_arranque_subir_es_empeorar(self):
        d = deriva(self._entradas([20, 21, 22, 40, 45, 50], "boot_seconds"),
                   "boot_seconds")
        self.assertEqual(d["direction"], "degradación")
        # El signo se devuelve ya corregido: negativo = peor, suba o baje.
        self.assertLess(d["change_pct"], 0)
        self.assertTrue(d["lower_is_better"])

    def test_en_temperatura_bajar_es_mejorar(self):
        d = deriva(self._entradas([85, 84, 86, 65, 63, 62], "cpu_temp"), "cpu_temp")
        self.assertEqual(d["direction"], "mejora")
        self.assertGreater(d["change_pct"], 0)

    def test_el_umbral_separa_ruido_de_deriva(self):
        justo = 100 * (1 - (DERIVA_MINIMA_PCT - 1) / 100)
        d = deriva(self._entradas([100, 100, 100, justo, justo, justo]), "overall")
        self.assertEqual(d["direction"], "estable")

    def test_serie_sin_esa_clave(self):
        self.assertEqual(serie(self._entradas([1, 2, 3]), "gpu"), [])
        self.assertIsNone(deriva(self._entradas([1, 2, 3, 4]), "gpu"))


class Informe(unittest.TestCase):
    def test_historico_vacio(self):
        with tempfile.TemporaryDirectory() as d:
            r = report(path=Path(d) / "no_existe.jsonl")
        self.assertEqual(r["runs"], 0)
        self.assertFalse(r["enough"])
        self.assertEqual(r["series"], [])

    def test_detecta_lo_que_va_a_peor(self):
        entradas = [{"at": f"2026-01-{i + 1:02d}T10:00:00", "overall": v}
                    for i, v in enumerate([120, 118, 119, 90, 88, 85])]
        r = report(entradas)
        self.assertTrue(r["enough"])
        self.assertEqual([d["key"] for d in r["degrading"]], ["overall"])


class EscalaDeReferencia(unittest.TestCase):
    """Una escala sin fecha no envejece: se pudre en silencio."""

    def test_la_fecha_tiene_el_formato_esperado(self):
        año, mes = REFERENCE_DATE.split("-")
        self.assertEqual(len(año), 4)
        self.assertTrue(1 <= int(mes) <= 12)

    def test_recien_fijada_no_esta_caducada(self):
        año, mes = (int(x) for x in REFERENCE_DATE.split("-"))
        self.assertFalse(reference_is_stale(date(año, mes, 15)))
        self.assertEqual(reference_age_months(date(año, mes, 15)), 0)

    def test_caduca_al_pasar_el_plazo(self):
        año, mes = (int(x) for x in REFERENCE_DATE.split("-"))
        futuro = date(año + (mes + REFERENCE_STALE_MONTHS - 1) // 12,
                      (mes + REFERENCE_STALE_MONTHS - 1) % 12 + 1, 1)
        self.assertTrue(reference_is_stale(futuro))

    def test_la_escala_de_hoy_sigue_vigente(self):
        # Si esto falla es que toca revisar las cifras, no el test.
        self.assertFalse(reference_is_stale(),
                         f"la escala de {REFERENCE_DATE} lleva "
                         f"{reference_age_months()} meses sin revisarse")

    def test_las_cifras_de_gpu_declaran_de_donde_salen(self):
        for clave in ("gpu_gflops", "gpu_vram_gbs", "gpu_pcie_gbs"):
            self.assertIn(clave, REFERENCE)
            self.assertIn(clave, REFERENCE_ORIGIN, "una cifra sin procedencia "
                                                   "no se puede discutir")


if __name__ == "__main__":
    unittest.main()
