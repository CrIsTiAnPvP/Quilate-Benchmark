"""Comparar dos ejecuciones sin confundir una mejora con ruido.

Quilate promete «+19% tras optimizar» y hasta ahora nadie contrastaba esa
promesa. Comparar dos JSON es fácil; lo que no es trivial es decidir cuándo la
diferencia significa algo. Estas pruebas fijan ese criterio: una diferencia por
debajo del margen combinado de las dos medidas se declara ruido, aunque tenga
buen aspecto.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from quilate.cli import _run_comparison
from quilate.compare import (MARGEN_DESCONOCIDO_PCT, RunLoadError, calibracion,
                             comparar_hallazgos, comparar_pruebas, compare_runs,
                             load_run, mismo_equipo)


def ejecucion(**cambios) -> dict:
    base = {
        "meta": {"version": "2.2.0", "generated_at": "2026-07-27T10:00:00"},
        "system": {"hostname": "PC", "cpu_name": "CPU X", "ram_total": 16},
        "scores": {"overall": 100.0, "components": {"disk": 100.0}},
        "benchmark": {"disk_read": {"name": "Disco · lectura", "unit": "MB/s",
                                    "raw": 1000.0, "score": 100.0}},
        "dispersion": {"disk_read": {"label": "Lectura", "runs": 3, "median": 1000.0,
                                     "spread_pct": 4.0, "stable": True}},
        "findings": [],
        "projection": {},
        "coverage": {"checks_conclusive": 20, "checks_total": 24, "unverified": []},
        "ambient_load": {},
    }
    base.update(cambios)
    return base


def con_lectura(mbs: float, spread: float = 4.0) -> dict:
    run = ejecucion()
    run["benchmark"]["disk_read"]["raw"] = mbs
    run["dispersion"]["disk_read"]["spread_pct"] = spread
    return run


class CargaDeFicheros(unittest.TestCase):
    def test_fichero_inexistente(self):
        with self.assertRaises(RunLoadError):
            load_run(Path("no_existe_12345.json"))

    def test_json_invalido(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "roto.json"
            p.write_text("{esto no es json", encoding="utf-8")
            with self.assertRaises(RunLoadError):
                load_run(p)

    def test_json_valido_pero_de_otra_cosa(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "otro.json"
            p.write_text(json.dumps({"cualquier": "cosa"}), encoding="utf-8")
            with self.assertRaises(RunLoadError) as ctx:
                load_run(p)
            self.assertIn("Quilate", str(ctx.exception))

    def test_ida_y_vuelta(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ok.json"
            p.write_text(json.dumps(ejecucion()), encoding="utf-8")
            self.assertEqual(load_run(p)["scores"]["overall"], 100.0)


class FicherosIncompletos(unittest.TestCase):
    """Un JSON al que le faltan datos no puede acabar en una traza de Python.

    `--compare` es el único camino que acepta ficheros de fuera, y esos ficheros
    se truncan por un corte de luz, se editan a mano —cosa que el proyecto da
    por buena en el histórico— y los generan versiones con otro esquema. El mimo
    de `history.load()`, que se salta las líneas corruptas, faltaba aquí.
    """

    def _cargar(self, run: dict) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "run.json"
            p.write_text(json.dumps(run), encoding="utf-8")
            return load_run(p)

    def _comparar(self, roto: dict) -> dict:
        return compare_runs(self._cargar(roto), self._cargar(ejecucion()))

    def test_una_prueba_sin_raw(self):
        run = ejecucion()
        del run["benchmark"]["disk_read"]["raw"]
        self._comparar(run)          # antes: KeyError: 'raw'

    def test_una_prueba_con_raw_nulo(self):
        run = ejecucion()
        run["benchmark"]["disk_read"]["raw"] = None
        self._comparar(run)          # antes: TypeError en float(None)

    def test_una_prueba_con_raw_que_no_es_numero(self):
        run = ejecucion()
        run["benchmark"]["disk_read"]["raw"] = "mil"
        self._comparar(run)

    def test_una_entrada_de_benchmark_que_no_es_un_diccionario(self):
        run = ejecucion()
        run["benchmark"]["disk_read"] = "basura"
        self._comparar(run)

    def test_un_benchmark_que_no_es_un_diccionario(self):
        run = ejecucion()
        run["benchmark"] = ["disk_read"]
        self._comparar(run)

    def test_la_prueba_descartada_no_se_compara_a_medias(self):
        # Descartarla y luego enseñar una fila con «before: None» sería peor que
        # el fallo: parecería que la prueba no se ejecutó.
        run = ejecucion()
        run["benchmark"]["disk_read"]["raw"] = None
        self.assertEqual(self._cargar(run)["benchmark"], {})

    def test_lo_que_esta_bien_sigue_pasando(self):
        # La criba no puede llevarse por delante las pruebas buenas del mismo
        # fichero: solo se descarta la que no tiene cifra.
        run = ejecucion()
        run["benchmark"]["cpu_single"] = {"name": "CPU", "unit": "pts", "raw": None}
        cargado = self._cargar(run)
        self.assertEqual(set(cargado["benchmark"]), {"disk_read"})
        self.assertEqual(cargado["benchmark"]["disk_read"]["raw"], 1000.0)

    def test_un_hallazgo_sin_id(self):
        run = ejecucion()
        run["findings"] = [{"title": "algo sin identificar"}]
        self._comparar(run)          # antes: KeyError: 'id'

    def test_los_hallazgos_con_id_se_siguen_casando(self):
        antes, despues = ejecucion(), ejecucion()
        antes["findings"] = [{"title": "sin id"}, {"id": "trim_off", "title": "TRIM"}]
        despues["findings"] = [{"id": "trim_off", "title": "TRIM"},
                               {"id": "fs_dirty", "title": "Sucio"}]
        hallazgos = comparar_hallazgos(antes, despues)
        self.assertEqual([f["id"] for f in hallazgos["persisten"]], ["trim_off"])
        self.assertEqual([f["id"] for f in hallazgos["nuevos"]], ["fs_dirty"])
        self.assertEqual(hallazgos["resueltos"], [])


class ElMensajeQueVeElUsuario(unittest.TestCase):
    """Lo que `--compare` hace cuando el fichero no da para comparar.

    El mensaje cuidado ya existía dos líneas más abajo del `except`; el problema
    era que solo lo veía quien pasaba un fichero que no era de Quilate.
    """

    def _comparar(self, antes: dict, despues: dict) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as d:
            rutas = []
            for nombre, run in (("antes.json", antes), ("despues.json", despues)):
                p = Path(d) / nombre
                p.write_text(json.dumps(run), encoding="utf-8")
                rutas.append(str(p))
            salida = io.StringIO()
            with redirect_stdout(salida):
                codigo = _run_comparison(rutas)
        return codigo, salida.getvalue()

    def test_dos_ficheros_buenos_se_comparan(self):
        codigo, salida = self._comparar(ejecucion(), ejecucion())
        self.assertEqual(codigo, 0)
        self.assertNotIn("No se puede comparar", salida)

    def test_un_dato_de_otro_tipo_no_saca_una_traza(self):
        roto = ejecucion()
        roto["scores"]["overall"] = "bastante"
        codigo, salida = self._comparar(roto, ejecucion())
        self.assertEqual(codigo, 2)
        self.assertIn("le faltan datos", salida)

    def test_un_fichero_que_no_es_de_quilate(self):
        # El mensaje de siempre no puede haberse perdido por el camino.
        codigo, salida = self._comparar({"cualquier": "cosa"}, ejecucion())
        self.assertEqual(codigo, 2)
        self.assertIn("no parece un export de Quilate", salida)

    def test_siempre_se_explica_como_generarlos(self):
        _, salida = self._comparar({"cualquier": "cosa"}, ejecucion())
        self.assertIn("--json", salida)


class ElMargenDecide(unittest.TestCase):
    def _veredicto(self, antes_mbs, despues_mbs, spread=4.0):
        filas = comparar_pruebas(con_lectura(antes_mbs, spread),
                                 con_lectura(despues_mbs, spread))
        return filas[0]

    def test_una_subida_pequena_es_ruido(self):
        # +3% con dos medidas que bailan un 4% cada una: umbral 4%.
        self.assertEqual(self._veredicto(1000, 1030)["verdict"], "dentro del margen")

    def test_una_subida_grande_si_es_mejora(self):
        self.assertEqual(self._veredicto(1000, 1400)["verdict"], "mejora")

    def test_una_bajada_grande_es_empeorar(self):
        self.assertEqual(self._veredicto(1000, 600)["verdict"], "empeora")

    def test_con_medidas_inestables_hace_falta_mas_diferencia(self):
        # La misma subida del 20% se declara ruido si las medidas bailaban un 60%.
        self.assertEqual(self._veredicto(1000, 1200, spread=4)["verdict"], "mejora")
        self.assertEqual(self._veredicto(1000, 1200, spread=60)["verdict"],
                         "dentro del margen")

    def test_el_umbral_es_la_media_de_los_dos_recorridos(self):
        fila = self._veredicto(1000, 1000, spread=10)
        self.assertAlmostEqual(fila["threshold"], 10.0)

    def test_sin_dispersion_se_supone_un_margen_amplio(self):
        antes, despues = con_lectura(1000), con_lectura(1080)
        antes["dispersion"] = {}
        fila = comparar_pruebas(antes, despues)[0]
        self.assertFalse(fila["margin_known"])
        self.assertGreaterEqual(fila["threshold"], MARGEN_DESCONOCIDO_PCT / 2)
        # +8% no puede darse por bueno cuando no se sabe cuánto bailaba la medida.
        self.assertEqual(fila["verdict"], "dentro del margen")

    def test_prueba_presente_en_una_sola_ejecucion(self):
        antes = con_lectura(1000)
        despues = con_lectura(1000)
        del despues["benchmark"]["disk_read"]
        despues["benchmark"]["disk_write"] = {"name": "Disco · escritura",
                                              "unit": "MB/s", "raw": 500.0, "score": 50.0}
        veredictos = {f["key"]: f["verdict"] for f in comparar_pruebas(antes, despues)}
        self.assertEqual(veredictos["disk_read"], "solo en una de las dos")
        self.assertEqual(veredictos["disk_write"], "solo en una de las dos")


class Hallazgos(unittest.TestCase):
    def test_resueltos_nuevos_y_persistentes(self):
        antes = ejecucion(findings=[{"id": "a", "title": "A"}, {"id": "b", "title": "B"}])
        despues = ejecucion(findings=[{"id": "b", "title": "B"}, {"id": "c", "title": "C"}])
        h = comparar_hallazgos(antes, despues)
        self.assertEqual([f["id"] for f in h["resueltos"]], ["a"])
        self.assertEqual([f["id"] for f in h["persisten"]], ["b"])
        self.assertEqual([f["id"] for f in h["nuevos"]], ["c"])


class Calibracion(unittest.TestCase):
    """Lo que se prometió frente a lo que pasó."""

    def _cal(self, partida, proyectado, logrado):
        antes = ejecucion(scores={"overall": partida, "components": {}},
                          projection={"current_overall": partida,
                                      "projected_overall": proyectado})
        despues = ejecucion(scores={"overall": logrado, "components": {}})
        return calibracion(antes, despues)

    def test_mejora_clavada(self):
        cal = self._cal(100, 120, 120)
        self.assertAlmostEqual(cal["realised"], 1.0)
        self.assertAlmostEqual(cal["predicted_gain_pct"], 20.0)
        self.assertAlmostEqual(cal["achieved_gain_pct"], 20.0)

    def test_mejora_a_medias(self):
        self.assertAlmostEqual(self._cal(100, 120, 110)["realised"], 0.5)

    def test_empeorar_da_negativo(self):
        self.assertLess(self._cal(100, 120, 90)["realised"], 0)

    def test_sin_mejora_proyectada_no_se_divide_entre_cero(self):
        self.assertIsNone(self._cal(100, 100, 110)["realised"])

    def test_sin_proyeccion_no_hay_calibracion(self):
        self.assertIsNone(calibracion(ejecucion(), ejecucion()))


class EquipoDistinto(unittest.TestCase):
    def test_mismo_equipo(self):
        coincide, difs = mismo_equipo(ejecucion(), ejecucion())
        self.assertTrue(coincide)
        self.assertEqual(difs, [])

    def test_cpu_distinta_se_avisa(self):
        otro = ejecucion(system={"hostname": "PC", "cpu_name": "OTRA", "ram_total": 16})
        coincide, difs = mismo_equipo(ejecucion(), otro)
        self.assertFalse(coincide)
        self.assertIn("CPU", difs[0])

    def test_un_campo_ausente_no_cuenta_como_diferencia(self):
        # Un JSON antiguo sin el campo no debe hacer parecer que es otro equipo.
        viejo = ejecucion(system={"hostname": "PC"})
        self.assertTrue(mismo_equipo(ejecucion(), viejo)[0])


class InformeCompleto(unittest.TestCase):
    def test_estructura_del_resultado(self):
        cmp = compare_runs(ejecucion(), ejecucion())
        for clave in ("meta", "overall", "tests", "components", "findings",
                      "coverage", "calibration", "ambient"):
            self.assertIn(clave, cmp)
        self.assertTrue(cmp["meta"]["same_machine"])
        self.assertEqual(cmp["overall"]["delta_pct"], 0.0)

    def test_no_revienta_con_json_minimos(self):
        # Un export antiguo no trae dispersión, cobertura ni carga ambiente.
        minimo = {"meta": {}, "scores": {"overall": 90.0, "components": {}}}
        cmp = compare_runs(minimo, minimo)
        self.assertEqual(cmp["tests"], [])
        self.assertIsNone(cmp["calibration"])


if __name__ == "__main__":
    unittest.main()
