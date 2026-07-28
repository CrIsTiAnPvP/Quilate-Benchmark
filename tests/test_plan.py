"""El plan de optimización tiene que poder deshacerse y poder ensayarse.

Un script que cambia ajustes del sistema sin guardar los anteriores es un
billete de ida. Y el valor «por defecto» de Windows no sirve como vuelta: el que
hay que restaurar es el que tenía ese equipo, que puede no ser el de fábrica.

Estas pruebas fijan tres cosas: que todo bloque que toca un ajuste sepa
deshacerse, que el texto que viene del sistema no pueda romper el script, y que
el modo simulación exista y no dependa de tener permisos.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from quilate.audit import Auditor, Finding
from quilate.components import build_component_cards
from quilate.export.plan_export import (PLAN_ACTIONS, _comentario, _plan_component_summary,
                                        _plan_large_files, _plan_system_summary, _ps_str,
                                        export_plan)
from quilate.storage_scan import ScanResult
from quilate.sysinfo import SystemInfo

# Los dos únicos bloques que no cambian ningún ajuste: uno enseña una lista y
# el otro reordena ficheros. Declarados aquí para que añadir un tercero sin
# reversión sea una decisión y no un descuido.
SIN_AJUSTE = {"startup_bloat", "defrag_hdd"}


def hallazgo(id_: str) -> Finding:
    return Finding(id=id_, title=f"Título de «{id_}»", severity="medium",
                   category="fluidez", component="system", detail="d",
                   gain=0.1, gain_note="n", effort="bajo", risk="bajo", steps=["paso"])


def generar(ids: list[str]) -> tuple[str, int]:
    auditor = Auditor(SystemInfo(), None)
    auditor.findings = [hallazgo(i) for i in ids]
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "plan.ps1"
        n = export_plan(ruta, SystemInfo(), None, auditor)
        return ruta.read_text(encoding="utf-8-sig"), n


class CatalogoDeAcciones(unittest.TestCase):
    def test_todas_declaran_descripcion_codigo_y_reversion(self):
        for clave, accion in PLAN_ACTIONS.items():
            with self.subTest(clave=clave):
                self.assertEqual(len(accion), 3, "falta declarar cómo deshacerlo")

    def test_todo_lo_que_cambia_un_ajuste_sabe_deshacerse(self):
        sin_reversion = {k for k, (_, _, cap) in PLAN_ACTIONS.items() if not cap}
        self.assertEqual(sin_reversion, SIN_AJUSTE,
                         "hay un bloque que cambia algo y no sabe volver atrás")

    def test_la_reversion_lee_el_estado_no_lo_supone(self):
        # Todas las capturas consultan el sistema antes de escribir nada.
        lectores = ("DeshacerValor", "Get-", "powercfg /getactivescheme",
                    "fsutil behavior query", "chkntfs")
        for clave, (_, _, captura) in PLAN_ACTIONS.items():
            if not captura:
                continue
            with self.subTest(clave=clave):
                self.assertTrue(any(l in captura for l in lectores),
                                f"{clave} no consulta el valor actual")


class Escapado(unittest.TestCase):
    def test_comillas_y_dolares(self):
        self.assertEqual(_ps_str('di "hola" $var'), 'di `"hola`" `$var')

    def test_las_comillas_invertidas_van_primero(self):
        # Si se escaparan al final, se duplicarían las que introducen los otros
        # reemplazos y el texto saldría con barras de más.
        self.assertEqual(_ps_str("a`b"), "a``b")

    def test_un_titulo_hostil_no_parte_el_script(self):
        auditor = Auditor(SystemInfo(), None)
        malo = hallazgo("sysmain")
        malo.title = 'Servicio "X" cuesta $100'
        auditor.findings = [malo]
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "plan.ps1"
            export_plan(ruta, SystemInfo(), None, auditor)
            texto = ruta.read_text(encoding="utf-8-sig")
        self.assertIn('`"X`"', texto)
        self.assertIn("`$100", texto)


#: Lo que un disco malicioso intentaría colar. Va con CRLF delante y detrás
#: porque el ataque no es el texto, es el salto de línea: cierra el comentario y
#: convierte lo que sigue en una orden que se ejecuta como Administrador.
CARGA = r"Remove-Item C:\ -Recurse"
HOSTIL = "SanDisk Ultra\r\n" + CARGA + "\r\n#"


class LineasDeComentario(unittest.TestCase):
    """Ninguna cadena del sistema puede salirse de su línea de comentario.

    El título de `smart_warn`, `disk_wear` y `disk_hot` lleva el FriendlyName
    que devuelve `Get-PhysicalDisk`, que sale del IDENTIFY DEVICE del disco: lo
    escribe el firmware del dispositivo, no el usuario ni Windows. Y el fichero
    que se genera aquí es el que la herramienta le dice al usuario que ejecute
    como Administrador.
    """

    def _si_hostil(self) -> SystemInfo:
        si = SystemInfo()
        si.hostname = HOSTIL
        si.cpu_name = HOSTIL
        si.system_drive_media = HOSTIL
        si.gpus = [{"name": HOSTIL, "driver": HOSTIL}]
        return si

    def _scan_hostil(self) -> ScanResult:
        return ScanResult(
            min_size=1024, total_large=2048, reclaimable=0,
            files=[{"size": 2048, "category": HOSTIL, "age_days": 3, "path": HOSTIL}],
            special=[{"name": HOSTIL, "size": 1024, "note": HOSTIL}])

    def _todo_comentario(self, texto: str, bloque: str):
        for numero, linea in enumerate(texto.splitlines(), 1):
            if not linea.strip():
                continue
            self.assertTrue(linea.lstrip().startswith("#"),
                            f"{bloque}, línea {numero}: se ha salido del comentario\n"
                            f"  {linea!r}")

    def test_el_resumen_del_equipo_es_comentario_entero(self):
        self._todo_comentario(_plan_system_summary(self._si_hostil(), None),
                              "resumen del equipo")

    def test_el_resumen_por_componente_es_comentario_entero(self):
        si = self._si_hostil()
        auditor = Auditor(si, None)
        malo = hallazgo("smart_warn")
        malo.title = HOSTIL
        auditor.findings = [malo]
        texto = _plan_component_summary(build_component_cards(si, None, auditor))
        self._todo_comentario(texto, "resumen por componente")

    def test_el_listado_de_archivos_grandes_es_comentario_entero(self):
        self._todo_comentario(_plan_large_files(self._scan_hostil()), "archivos grandes")

    def test_las_acciones_manuales_son_comentario_entero(self):
        auditor = Auditor(SystemInfo(), None)
        malo = hallazgo("ram_low")           # sin acción automatizable
        malo.title, malo.steps = HOSTIL, [HOSTIL]
        auditor.findings = [malo]
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "plan.ps1"
            export_plan(ruta, SystemInfo(), None, auditor)
            texto = ruta.read_text(encoding="utf-8-sig")
        # Desde la línea siguiente al encabezado hasta donde vuelve la plantilla.
        seccion = texto.split("NO SE PUEDEN AUTOMATIZAR")[1].split("\n", 1)[1]
        seccion = seccion.split("BLOQUE FINAL")[0]
        self._todo_comentario(seccion, "acciones manuales")

    def test_la_orden_inyectada_nunca_empieza_una_linea(self):
        # La criba sobre el fichero completo: da igual por qué campo entre, no
        # puede acabar en posición de orden.
        si = self._si_hostil()
        auditor = Auditor(si, None)
        malo = hallazgo("sysmain")           # sí tiene bloque automatizado
        malo.title, malo.steps = HOSTIL, [HOSTIL]
        auditor.findings = [malo, hallazgo("ram_low")]
        auditor.scan = self._scan_hostil()
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "plan.ps1"
            export_plan(ruta, si, None, auditor)
            texto = ruta.read_text(encoding="utf-8-sig")
        self.assertIn(CARGA, texto, "el texto hostil ni siquiera ha llegado al fichero: "
                                    "sin eso este test no comprueba nada")
        for numero, linea in enumerate(texto.splitlines(), 1):
            self.assertFalse(linea.lstrip().startswith(CARGA),
                             f"línea {numero}: la orden inyectada ha quedado suelta")


class Comentario(unittest.TestCase):
    def test_colapsa_saltos_de_linea_y_nulos(self):
        self.assertEqual(_comentario("a\r\nb\x00c"), "a b c")

    def test_varios_saltos_seguidos_valen_por_uno(self):
        self.assertEqual(_comentario("a\n\n\n\nb"), "a b")

    def test_recorta_lo_muy_largo(self):
        self.assertEqual(len(_comentario("x" * 5000)), 300)

    def test_acepta_lo_que_no_es_texto(self):
        # `f['size']` y compañía llegan como números desde el rastreo.
        self.assertEqual(_comentario(42), "42")


class ScriptGenerado(unittest.TestCase):
    def setUp(self):
        self.texto, self.n = generar(["sysmain", "game_dvr"])

    def test_admite_simulacion(self):
        self.assertIn("param([switch]$WhatIf)", self.texto)
        self.assertIn("[simulacion]", self.texto)

    def test_la_simulacion_no_exige_administrador(self):
        # Ensayar el plan no cambia nada; pedir permisos para mirar sobra y
        # empujaría a saltarse el ensayo.
        self.assertIn("if (-not $WhatIf -and -not (", self.texto)

    def test_trae_las_piezas_de_la_reversion(self):
        for pieza in ("function Registrar", "function DeshacerValor", "$RollbackPath"):
            self.assertIn(pieza, self.texto)

    def test_todos_los_bloques_pasan_por_el_mismo_sitio(self):
        # Un `if (Confirmar ...)` suelto sería un bloque que ni simula ni anota
        # cómo deshacerse.
        self.assertNotIn("if (Confirmar", self.texto)
        self.assertGreaterEqual(self.texto.count("Bloque -Titulo"), self.n)

    def test_el_bloque_reversible_declara_su_deshacer(self):
        bloque = self.texto.split("Desactivar SysMain")[2]
        self.assertIn("-Deshacer", bloque.split("-Accion")[0])

    def test_las_llaves_cuadran(self):
        # Comprobación tosca pero eficaz contra una plantilla rota: un `{` de
        # más deja el script sin ejecutar y el fallo aparece en el equipo del
        # usuario, no aquí.
        self.assertEqual(self.texto.count("{"), self.texto.count("}"))

    def test_se_escribe_con_bom(self):
        # Sin marca, PowerShell 5.1 lee el .ps1 como ANSI y los títulos con
        # acentos llegan rotos.
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "plan.ps1"
            auditor = Auditor(SystemInfo(), None)
            auditor.findings = [hallazgo("sysmain")]
            export_plan(ruta, SystemInfo(), None, auditor)
            self.assertTrue(ruta.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_remite_a_la_comparacion(self):
        # Medir antes y después es parte del procedimiento, no un extra.
        self.assertIn("--compare", self.texto)


class HallazgosSinAutomatizar(unittest.TestCase):
    def test_no_cuentan_como_bloques(self):
        texto, n = generar(["ram_low"])          # no tiene acción automatizable
        self.assertEqual(n, 0)
        self.assertIn("NO SE PUEDEN AUTOMATIZAR", texto)


if __name__ == "__main__":
    unittest.main()
