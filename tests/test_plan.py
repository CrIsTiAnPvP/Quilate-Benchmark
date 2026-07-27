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
from quilate.export.plan_export import PLAN_ACTIONS, _ps_str, export_plan
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
