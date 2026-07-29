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
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from quilate.benchmark import PY_ADJUST
from quilate import cli
from quilate.cli import _motivo, _run_comparison
from quilate.console import C
from quilate.compare import (MARGEN_DESCONOCIDO_PCT, RunLoadError, _ajuste_python,
                             calibracion, comparabilidad, comparar_hallazgos,
                             comparar_pruebas, compare_runs, load_run)


def ejecucion(**cambios) -> dict:
    base = {
        "meta": {"version": "2.2.0", "generated_at": "2026-07-27T10:00:00", "quick": False},
        "system": {"hostname": "PC", "cpu_name": "CPU X", "ram_total": 16,
                   "python_version": "3.10.11"},
        # Con qué escala se dieron las notas: `build_payload` lo guarda desde la
        # v2.2 justo para poder decidir si dos ejecuciones son conmensurables.
        "reference_meta": {"date": "2026-07", "machine": "banco", "stale": False},
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

    def test_un_margen_que_no_es_un_diccionario(self):
        run = ejecucion()
        run["dispersion"]["disk_read"] = "roto"
        self._comparar(run)          # antes: AttributeError en «roto».get

    def test_un_spread_que_no_es_numero(self):
        run = ejecucion()
        run["dispersion"]["disk_read"]["spread_pct"] = "mucho"
        self._comparar(run)          # antes: ValueError en float("mucho")

    def test_una_dispersion_que_no_es_un_diccionario(self):
        run = ejecucion()
        run["dispersion"] = ["disk_read"]
        self._comparar(run)

    def test_el_margen_ilegible_se_supone_amplio(self):
        # Descartarlo devuelve el umbral al supuesto conservador, que es más
        # ancho: una diferencia pequeña pasa a ser «dentro del margen», nunca al
        # revés. Descartar un margen no puede acabar declarando mejoras.
        run = ejecucion()
        run["dispersion"]["disk_read"] = "roto"
        fila = next(f for f in self._comparar(run)["tests"] if f["key"] == "disk_read")
        self.assertEqual(fila["threshold"], MARGEN_DESCONOCIDO_PCT)
        self.assertFalse(fila["margin_known"])

    def test_un_margen_bueno_del_mismo_fichero_sobrevive(self):
        run = ejecucion()
        run["dispersion"]["cpu_single"] = "roto"
        self.assertEqual(set(self._cargar(run)["dispersion"]), {"disk_read"})

    def test_un_spread_ausente_se_sigue_leyendo_como_cero(self):
        # Comportamiento de siempre: ausente o cero ya era «sin margen». La
        # criba no puede aprovechar para cambiarlo de paso.
        run = ejecucion()
        del run["dispersion"]["disk_read"]["spread_pct"]
        self.assertEqual(set(self._cargar(run)["dispersion"]), {"disk_read"})

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

    def test_un_margen_ilegible_no_impide_comparar(self):
        # El reparto: `dispersion` es auxiliar y tiene un supuesto para su
        # ausencia, así que se descarta y la comparación se hace igual.
        roto = ejecucion()
        roto["dispersion"]["disk_read"] = "roto"
        codigo, salida = self._comparar(roto, ejecucion())
        self.assertEqual(codigo, 0)
        self.assertNotIn("No se puede comparar", salida)

    def test_una_puntuacion_de_componente_ilegible_se_dice(self):
        # La otra mitad del reparto: las notas son el objeto de la comparación.
        # Descartarlas en silencio sería contestar a medias sin avisar.
        roto = ejecucion()
        roto["scores"]["components"]["disk"] = "bien"
        codigo, salida = self._comparar(roto, ejecucion())
        self.assertEqual(codigo, 2)
        self.assertIn("le faltan datos", salida)

    def test_una_proyeccion_ilegible_se_dice(self):
        roto = ejecucion()
        roto["projection"] = {"projected_overall": "más", "current_overall": 100.0}
        codigo, salida = self._comparar(roto, ejecucion())
        self.assertEqual(codigo, 2)
        self.assertIn("le faltan datos", salida)

    def test_un_anidado_que_no_es_un_objeto_tampoco_saca_traza(self):
        # La red de última instancia, que es lo que faltaba: el módulo indexa
        # dos y tres niveles y no hay saneador para cada rincón del esquema.
        roto = ejecucion()
        roto["coverage"]["unverified"] = ["algo"]
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


class MensajesParaQuienNoPrograma(unittest.TestCase):
    """Lo que falla se dice en castellano, no con el nombre de la excepción.

    El resto del informe se ha escrito con ese criterio —los `detail` de cada
    hallazgo explican el porqué y no solo el qué— así que no hay motivo para que
    la única palabra en inglés y en CamelCase de toda la ejecución sea la que
    aparece justo cuando algo se ha torcido.
    """

    def test_los_fallos_habituales_estan_traducidos(self):
        esperado = {
            PermissionError(13, "denegado"): "permisos insuficientes",
            FileNotFoundError(2, "no existe"): "la ruta no existe",
            NotADirectoryError(20, "no es carpeta"): "la ruta no es una carpeta",
            TimeoutError(): "ha tardado demasiado",
            MemoryError(): "no hay memoria suficiente",
        }
        for excepcion, texto in esperado.items():
            with self.subTest(excepcion=type(excepcion).__name__):
                self.assertEqual(_motivo(excepcion), texto)

    def test_las_subclases_ganan_al_generico(self):
        # `PermissionError` es subclase de `OSError`: si el orden del recorrido
        # se invirtiera, todos los fallos de permisos dirían lo genérico.
        self.assertNotEqual(_motivo(PermissionError()), _motivo(OSError()))

    def test_lo_no_previsto_tambien_se_dice_en_castellano(self):
        motivo = _motivo(ValueError("algo raro"))
        self.assertNotIn("ValueError", motivo)
        self.assertNotIn("Error", motivo)

    def test_nunca_se_cuela_el_nombre_de_la_clase(self):
        for excepcion in (PermissionError(), OSError(), RuntimeError(),
                          ZeroDivisionError(), KeyError("x")):
            with self.subTest(excepcion=type(excepcion).__name__):
                self.assertNotIn(type(excepcion).__name__, _motivo(excepcion))


class NoPoderEscribirElInforme(unittest.TestCase):
    """Tras intentarlo en dos sitios, el usuario merece saber cuáles."""

    def _fallar(self, excepcion) -> str:
        original = cli.export_html
        cli.export_html = lambda *a, **k: (_ for _ in ()).throw(excepcion)
        C.disable()
        salida = io.StringIO()
        try:
            with redirect_stdout(salida):
                resultado = cli._write_export("html", None, None, None, {})
        finally:
            cli.export_html = original
        self.assertIsNone(resultado)
        return salida.getvalue()

    def test_nombra_las_ubicaciones_que_ha_probado(self):
        texto = self._fallar(PermissionError(13, "Acceso denegado"))
        self.assertIn(str(Path.cwd()), texto)
        self.assertIn(str(Path.home()), texto)

    def test_dice_el_motivo_en_castellano(self):
        texto = self._fallar(PermissionError(13, "Acceso denegado"))
        self.assertIn("permisos insuficientes", texto)
        self.assertNotIn("PermissionError", texto)

    def test_ofrece_la_salida_concreta(self):
        # No basta con decir que no se pudo: hay que decir qué hacer.
        texto = self._fallar(OSError(28, "No queda espacio"))
        self.assertIn("--html", texto)
        self.assertIn("quilate_informe.html", texto)


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


class Comparabilidad(unittest.TestCase):
    """No basta con restar: hay que decidir si la resta significa algo.

    Es el criterio que el módulo ya aplicaba al margen de cada prueba, llevado a
    la ejecución entera. «Distinto equipo» era solo una de las formas de que dos
    medidas no sean restables, y ni siquiera la más frecuente: lo normal es
    comparar un equipo consigo mismo después de haber tocado algo.
    """

    def _motivos(self, antes: dict, despues: dict) -> dict[str, dict]:
        return {m["key"]: m for m in comparabilidad(antes, despues)}

    def test_dos_ejecuciones_iguales_no_tienen_pegas(self):
        self.assertEqual(comparabilidad(ejecucion(), ejecucion()), [])
        self.assertTrue(compare_runs(ejecucion(), ejecucion())["meta"]["comparable"])

    # ------------------------------------------------------------ hardware ---
    def test_cpu_distinta_se_avisa(self):
        otro = ejecucion(system={"hostname": "PC", "cpu_name": "OTRA", "ram_total": 16})
        motivos = self._motivos(ejecucion(), otro)
        self.assertIn("CPU", motivos["hardware"]["text"])
        self.assertEqual(motivos["hardware"]["severity"], "alta")

    def test_un_campo_ausente_no_cuenta_como_diferencia(self):
        # Un JSON antiguo sin el campo no debe hacer parecer que es otro equipo.
        viejo = ejecucion(system={"hostname": "PC"})
        self.assertNotIn("hardware", self._motivos(ejecucion(), viejo))

    # --------------------------------------------------------------- quick ---
    def test_quick_contra_completo(self):
        rapida = ejecucion()
        rapida["meta"] = dict(rapida["meta"], quick=True)
        motivos = self._motivos(rapida, ejecucion())
        self.assertEqual(motivos["quick"]["severity"], "alta")
        self.assertIn("--quick", motivos["quick"]["text"])

    def test_dos_quick_si_son_comparables(self):
        rapida = ejecucion()
        rapida["meta"] = dict(rapida["meta"], quick=True)
        self.assertNotIn("quick", self._motivos(rapida, rapida))

    # ---------------------------------------------------------- referencia ---
    def test_escala_de_referencia_distinta(self):
        viejo = ejecucion()
        viejo["reference_meta"] = {"date": "2024-01"}
        motivos = self._motivos(viejo, ejecucion())
        self.assertEqual(motivos["reference"]["severity"], "alta")
        self.assertIn("2024-01", motivos["reference"]["text"])

    def test_la_misma_escala_no_se_avisa_aunque_cambie_la_version(self):
        # Cambiar de versión sin tocar la escala no invalida nada.
        viejo = ejecucion()
        viejo["meta"] = dict(viejo["meta"], version="2.4.0")
        motivos = self._motivos(viejo, ejecucion())
        self.assertNotIn("reference", motivos)
        self.assertNotIn("version", motivos)

    def test_sin_reference_meta_la_version_es_lo_unico_que_queda(self):
        # Las ejecuciones anteriores a la v2.2 no anotan con qué escala puntuaron.
        viejo = ejecucion()
        viejo["meta"] = dict(viejo["meta"], version="2.1.0")
        del viejo["reference_meta"]
        motivos = self._motivos(viejo, ejecucion())
        self.assertEqual(motivos["version"]["severity"], "media")
        self.assertIn("2.1.0", motivos["version"]["text"])

    # -------------------------------------------------------------- python ---
    def test_dos_interpretes_con_distinto_ajuste(self):
        viejo = ejecucion()
        viejo["system"] = dict(viejo["system"], python_version="3.10.11")
        nuevo = ejecucion()
        nuevo["system"] = dict(nuevo["system"], python_version="3.13.0")
        motivos = self._motivos(viejo, nuevo)
        self.assertEqual(motivos["python"]["severity"], "alta")
        self.assertIn("1.35", motivos["python"]["text"])

    def test_dos_interpretes_del_mismo_lado_del_umbral(self):
        # 3.11 y 3.13 se corrigen igual: cambiar de uno a otro no sesga nada.
        for antes_v, despues_v in (("3.11.0", "3.13.0"), ("3.9.7", "3.10.11")):
            with self.subTest(versiones=(antes_v, despues_v)):
                a, d = ejecucion(), ejecucion()
                a["system"] = dict(a["system"], python_version=antes_v)
                d["system"] = dict(d["system"], python_version=despues_v)
                self.assertNotIn("python", self._motivos(a, d))

    def test_una_version_de_python_ilegible_no_inventa_un_aviso(self):
        for valor in (None, "", "vete a saber", "3", 3.11):
            with self.subTest(valor=valor):
                raro = ejecucion()
                raro["system"] = dict(raro["system"], python_version=valor)
                self.assertNotIn("python", self._motivos(raro, ejecucion()))

    def test_el_umbral_es_el_de_PY_ADJUST(self):
        # Si alguien mueve PY_ADJUST, este test lo obliga a mover también esto.
        self.assertEqual(_ajuste_python(f"{sys.version_info[0]}.{sys.version_info[1]}.0"),
                         PY_ADJUST)

    # ----------------------------------------------------------- cobertura ---
    def test_no_gpu_contra_completo(self):
        completo = ejecucion()
        completo["scores"] = {"overall": 100.0, "components": {"disk": 100.0, "gpu": 80.0}}
        motivos = self._motivos(ejecucion(), completo)
        self.assertEqual(motivos["coverage"]["severity"], "media")
        self.assertIn("gpu", motivos["coverage"]["text"])

    # ------------------------------------------------------------ conjunto ---
    def test_solo_lo_grave_invalida_la_resta(self):
        # Un aviso «media» se enseña, pero la comparación sigue teniendo sentido.
        completo = ejecucion()
        completo["scores"] = {"overall": 100.0, "components": {"disk": 100.0, "gpu": 80.0}}
        cmp = compare_runs(ejecucion(), completo)
        self.assertTrue(cmp["meta"]["comparable"])
        self.assertEqual(len(cmp["meta"]["comparability"]), 1)

    def test_el_caso_que_no_avisaba_de_nada(self):
        # Verificado en el informe: quick v2.1.0 contra completa v2.6.0 devolvía
        # same_machine=True y cero avisos.
        viejo = ejecucion()
        viejo["meta"] = {"version": "2.1.0", "generated_at": "2026-01-01T10:00:00",
                         "quick": True}
        viejo["system"] = dict(viejo["system"], python_version="3.10.11")
        del viejo["reference_meta"]
        nuevo = ejecucion()
        nuevo["system"] = dict(nuevo["system"], python_version="3.13.0")
        cmp = compare_runs(viejo, nuevo)
        self.assertFalse(cmp["meta"]["comparable"])
        self.assertEqual({m["key"] for m in cmp["meta"]["comparability"]},
                         {"quick", "version", "python"})


class InformeCompleto(unittest.TestCase):
    def test_estructura_del_resultado(self):
        cmp = compare_runs(ejecucion(), ejecucion())
        for clave in ("meta", "overall", "tests", "components", "findings",
                      "coverage", "calibration", "ambient"):
            self.assertIn(clave, cmp)
        self.assertTrue(cmp["meta"]["comparable"])
        self.assertEqual(cmp["overall"]["delta_pct"], 0.0)

    def test_no_revienta_con_json_minimos(self):
        # Un export antiguo no trae dispersión, cobertura ni carga ambiente.
        minimo = {"meta": {}, "scores": {"overall": 90.0, "components": {}}}
        cmp = compare_runs(minimo, minimo)
        self.assertEqual(cmp["tests"], [])
        self.assertIsNone(cmp["calibration"])


if __name__ == "__main__":
    unittest.main()
