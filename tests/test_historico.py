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


class NadaQueNoSeaUnaCifra(unittest.TestCase):
    """Lista negra sobre la forma, no sobre los valores.

    `test_no_arrastra_datos_personales` comprueba que no se cuelan los cuatro
    textos que planta. Eso cubre lo que ya se sabe que hay, pero no lo que
    alguien añada mañana: un campo nuevo en `_resumen` que traiga la ruta del
    disco o el nombre de la GPU pasaría sin que nadie se enterase, porque nadie
    se acordaría de añadirlo a esa lista.

    Aquí se afirma la propiedad en vez de los ejemplos: en el histórico —que se
    acumula para siempre y sin caducidad— no puede haber nada que no sea una
    cifra, una bandera o una fecha. Un campo nuevo que no lo sea falla aquí el
    día que se escribe.
    """

    #: Los dos únicos campos de texto, y qué son: una marca de tiempo ISO y la
    #: versión de Quilate. Ninguno de los dos sale del equipo del usuario.
    TEXTO_PERMITIDO = {"at", "version"}

    def sucio(self) -> dict:
        """Un payload con de todo lo que no debe llegar al histórico."""
        payload = ejecucion(overall=120.0, boot=31.0, temp=68.0)
        payload["system"] = {"hostname": "PC-DE-ALGUIEN", "user": "alguien",
                             "cpu_name": "AMD Ryzen 9 5900X",
                             "system_drive": "C:\\"}
        payload["top_processes"] = [{"name": "loquesea.exe", "rss": 1, "pid": 42}]
        payload["startup_items"] = [{"name": "programa", "location": "C:\\Users\\x",
                                     "command": "C:\\Program Files\\x.exe"}]
        payload["storage_scan"] = {"items": [{"path": "/home/alguien/peli.mkv"}]}
        payload["notes"] = ["El disco D:\\ de alguien está lleno"]
        return payload

    def cadenas(self, valor, ruta="raíz") -> list[tuple[str, str]]:
        """Todas las cadenas del payload serializado, con dónde estaba cada una.

        Recorre claves además de valores: una clave puede traer tanto texto del
        sistema como un valor, y en JSON las dos cosas acaban en el fichero.
        """
        if isinstance(valor, dict):
            encontradas = []
            for clave, sub in valor.items():
                encontradas += self.cadenas(clave, f"{ruta}(clave)")
                encontradas += self.cadenas(sub, f"{ruta}.{clave}")
            return encontradas
        if isinstance(valor, (list, tuple)):
            return [c for i, sub in enumerate(valor)
                    for c in self.cadenas(sub, f"{ruta}[{i}]")]
        return [(ruta, valor)] if isinstance(valor, str) else []

    def test_solo_hay_cifras_banderas_y_dos_fechas(self):
        for clave, valor in _resumen(self.sucio()).items():
            with self.subTest(campo=clave):
                if clave in self.TEXTO_PERMITIDO:
                    self.assertIsInstance(valor, str)
                else:
                    self.assertIsInstance(
                        valor, (int, float, bool),
                        f"«{clave}» no es una cifra: si es un campo nuevo que "
                        f"trae texto del equipo, no puede ir al histórico")

    def test_ninguna_cadena_parece_una_ruta(self):
        # Ni separador de Windows ni de POSIX. Es lo que delata una ruta sin
        # tener que saber qué campo la traía.
        for ruta, texto in self.cadenas(_resumen(self.sucio())):
            with self.subTest(campo=ruta):
                self.assertNotIn("\\", texto)
                self.assertNotIn("/", texto)

    def test_ninguna_clave_sale_de_las_secciones_prohibidas(self):
        payload = self.sucio()
        prohibidas = {clave
                      for seccion in ("top_processes", "startup_items")
                      for item in payload[seccion]
                      for clave in item}
        self.assertTrue(prohibidas, "el payload de prueba no planta nada")
        self.assertEqual(set(_resumen(payload)) & prohibidas, set())

    def test_ningun_valor_del_inventario_se_copia(self):
        payload = self.sucio()
        volcado = json.dumps(_resumen(payload), ensure_ascii=False).lower()
        for campo, valor in payload["system"].items():
            with self.subTest(campo=campo):
                self.assertNotIn(str(valor).lower(), volcado)

    def test_la_garantia_vale_sobre_el_fichero_escrito(self):
        # No sobre el dict en memoria: lo que se acumula para siempre es lo que
        # acaba en disco, y es ahí donde hay que mirarlo.
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "historico.jsonl"
            append(self.sucio(), ruta)
            texto = ruta.read_text(encoding="utf-8")
        self.assertTrue(texto.strip(), "no se ha escrito nada: el test no prueba nada")
        for ruta_campo, cadena in self.cadenas(json.loads(texto)):
            with self.subTest(campo=ruta_campo):
                self.assertNotIn("\\", cadena)
                self.assertNotIn("/", cadena)


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

    def test_una_fecha_que_no_es_texto_tampoco(self):
        # El fichero es «un fichero de texto que el usuario puede leer, copiar o
        # borrar»: editarlo a mano es un uso previsto, y un `at` numérico dejaba
        # `--history` inservible para siempre con un TypeError al ordenar.
        with self.ruta.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": 20260101, "overall": 50.0}) + "\n")
        append(ejecucion(overall=95.0), self.ruta)
        self.assertEqual([e["overall"] for e in load(self.ruta)], [95.0])

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


class PrivacidadDelHistorico(unittest.TestCase):
    """El histórico se acumula para siempre: aquí no puede acabar nada personal.

    `_resumen()` escribe solo cifras y fechas, y eso está bien hoy. Pero es la
    clase de garantía que se rompe añadiendo un campo con buena intención: basta
    con que alguien meta `hostname` para «poder distinguir los equipos» y lo que
    era un fichero de números pasa a ser un registro de a quién pertenece.

    El payload se construye con el `build_payload()` de verdad y no a mano, para
    que un campo nuevo quede cubierto por este test el día que se añada, sin que
    nadie tenga que acordarse de venir aquí.
    """

    def _payload(self) -> dict:
        # El andamiaje de test_paridad planta cadenas testigo únicas en cada
        # fuente de datos: si una aparece en el histórico, solo puede venir de
        # donde se plantó.
        from tests.test_paridad import T, _auditoria, _benchmark, _sistema
        from quilate.export.json_export import build_payload
        from quilate.projection import project_improvement
        si, bench = _sistema(), _benchmark()
        auditor = _auditoria(si, bench)
        return T, build_payload(si, bench, auditor,
                                project_improvement(bench, auditor.findings))

    # Los testigos que identifican a alguien o dicen qué tiene instalado. Del
    # resto —el motivo por el que no hay GPU, el título de un hallazgo— no va
    # esta prueba: no son datos personales.
    PERSONALES = ("host", "cpu", "proceso", "ambiente", "inicio", "adaptador", "fichero")

    def test_ningun_testigo_llega_al_historico(self):
        testigos, payload = self._payload()
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "historico.jsonl"
            self.assertIsNotNone(append(payload, ruta))
            linea = ruta.read_text(encoding="utf-8")

        completo = json.dumps(payload, default=str)
        for etiqueta in self.PERSONALES:
            valor = testigos[etiqueta]
            # Que el payload lo trae de verdad: sin esto el test pasaría en
            # verde aunque el andamiaje se hubiera quedado vacío.
            self.assertIn(valor, completo,
                          f"el testigo «{etiqueta}» no está ni en el payload: "
                          f"este test no comprueba nada")
            self.assertNotIn(valor, linea,
                             f"se ha colado «{etiqueta}» ({valor}) en el histórico")

    def test_no_se_escribe_ninguna_ruta(self):
        _, payload = self._payload()
        linea = json.dumps(_resumen(payload), ensure_ascii=False)
        for pista in ("C:\\", "C:/", "\\Users", "/home/", ".exe", ".dmp"):
            self.assertNotIn(pista, linea, f"parece una ruta: «{pista}»")

    def test_solo_cifras_fechas_y_banderas(self):
        # La garantía en su forma más fuerte: nada de lo que se escribe puede
        # ser texto libre, salvo la fecha y la versión, que son las dos claves
        # que el histórico necesita para ordenarse y para saber con qué se midió.
        _, payload = self._payload()
        entrada = _resumen(payload)
        for clave, valor in entrada.items():
            with self.subTest(clave=clave):
                if clave in ("at", "version"):
                    self.assertIsInstance(valor, str)
                    continue
                self.assertIsInstance(valor, (int, float, bool),
                                      f"«{clave}» no es una cifra: {valor!r}")

    def test_las_claves_estan_declaradas(self):
        # Un campo nuevo tiene que pasar por aquí a propósito, no colarse.
        _, payload = self._payload()
        permitidas = {"at", "version", "overall", "findings", "quick",
                      "cpu_single", "cpu_multi", "memory", "disk", "gpu",
                      "boot_seconds", "cpu_temp", "max_spread_pct", "busy_pct"}
        nuevas = set(_resumen(payload)) - permitidas
        self.assertEqual(nuevas, set(),
                         "hay claves nuevas en el histórico: compruébalas una a una "
                         "y añádelas aquí si de verdad no llevan nada personal")


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
