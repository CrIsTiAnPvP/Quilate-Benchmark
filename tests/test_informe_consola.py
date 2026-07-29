"""El informe por consola: lo que ve todo el mundo, y lo que casi nadie probaba.

`print_report` es la salida por defecto —la que sale sin pedir ningún fichero—
y hasta ahora solo la cubría `test_seguridad`, y solo su bloque de seguridad.
El resto —la ficha por componente, los hallazgos, la proyección, el plan, lo
que no se ha podido comprobar y el veredicto— no lo miraba nadie.

Lo que se fija aquí no es la maquetación sino el orden y las advertencias, que
es lo que hace que el informe se lea bien: la seguridad por delante del plan
porque no promete retorno, «sin comprobar» por delante del veredicto porque
marca hasta dónde llega lo que se puede afirmar, y el veredicto priorizando el
disco moribundo sobre cualquier ajuste.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from quilate.audit import SEGURIDAD, Auditor, Finding
from quilate.console import C
from quilate.projection import project_improvement
from quilate.report import build_verdict, print_report
from quilate.sysinfo import SystemInfo


def hallazgo(id_="power_plan", gain=0.10, severity="medium", category="fluidez",
             **campos) -> Finding:
    base = dict(id=id_, title=f"Hallazgo «{id_}»", severity=severity,
                category=category, component="cpu_multi", detail="Detalle del hallazgo.",
                gain=gain, gain_note="fluidez general", effort="bajo", risk="nulo",
                steps=["Primer paso", "Segundo paso"])
    base.update(campos)
    return Finding(**base)


def riesgo(id_="smb1_activo", severity="high") -> Finding:
    return hallazgo(id_, gain=0.0, severity=severity, category=SEGURIDAD,
                    component="system", gain_note="no es una optimización")


def informe(*findings: Finding, notas=(), sin_comprobar=(), no_aplican=()) -> str:
    si = SystemInfo()
    auditor = Auditor(si, None)
    auditor.findings = list(findings)
    auditor.checks_run = 24
    auditor.notes = list(notas)
    auditor.unverified = list(sin_comprobar)
    auditor.not_applicable = list(no_aplican)
    C.disable()
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        print_report(si, None, auditor, project_improvement(None, auditor.findings))
    return buffer.getvalue()


def orden(texto: str, *titulos: str) -> list[int]:
    return [texto.index(t) for t in titulos]


class ElInformeSaleEntero(unittest.TestCase):
    def test_estan_todas_las_secciones(self):
        texto = informe(hallazgo())
        for bloque in ("INVENTARIO DEL EQUIPO", "FICHA POR COMPONENTE",
                       "HALLAZGOS DE LA AUDITORÍA", "PROYECCIÓN DE MEJORA",
                       "PLAN DE ACCIÓN PRIORIZADO", "VEREDICTO"):
            with self.subTest(bloque=bloque):
                self.assertIn(bloque, texto)

    def test_sin_hallazgos_no_se_finge_un_problema(self):
        texto = informe()
        self.assertIn("No se han detectado problemas", texto)
        self.assertIn("Nada que priorizar", texto)

    def test_sin_benchmark_no_se_inventan_notas(self):
        # `--no-bench` es un modo legítimo: el informe tiene que salir sin
        # puntuación en vez de con ceros, que se leerían como un equipo pésimo.
        self.assertNotIn("PUNTUACIÓN GLOBAL", informe(hallazgo()))


class ElOrdenDeLasSecciones(unittest.TestCase):
    """El orden no es estético: es lo que evita que se lea al revés."""

    def test_la_seguridad_va_antes_que_el_plan(self):
        # El plan ordena por retorno dividido por esfuerzo y lo dice. Un riesgo
        # no da retorno, así que dentro del plan tendría que enseñar un «+0%»
        # que se lee como un error, y detrás quedaría por debajo de ocho puntos
        # de fluidez.
        texto = informe(riesgo(), hallazgo())
        seguridad, plan = orden(texto, "▌ SEGURIDAD", "▌ PLAN DE ACCIÓN")
        self.assertLess(seguridad, plan)

    def test_lo_no_comprobado_va_antes_del_veredicto(self):
        # Es la advertencia de hasta dónde llega lo que el informe puede
        # afirmar: después del veredicto no la lee nadie.
        texto = informe(hallazgo(), sin_comprobar=[("Chip TPM", "requiere administrador")])
        sin, veredicto = orden(texto, "▌ SIN COMPROBAR", "▌ VEREDICTO")
        self.assertLess(sin, veredicto)

    def test_el_veredicto_es_lo_ultimo(self):
        texto = informe(hallazgo())
        self.assertLess(texto.index("▌ VEREDICTO"), len(texto))
        self.assertNotIn("▌", texto[texto.index("▌ VEREDICTO") + 1:])


class LosHallazgos(unittest.TestCase):
    def test_se_ordenan_por_gravedad(self):
        texto = informe(hallazgo("leve", severity="low"),
                        hallazgo("grave", severity="critical"))
        self.assertLess(texto.index("«grave»"), texto.index("«leve»"))

    def test_se_dice_cuantos_y_de_que_gravedad(self):
        texto = informe(hallazgo("a", severity="high"), hallazgo("b", severity="high"))
        self.assertIn("24 comprobaciones · 2 hallazgos", texto)
        self.assertIn("ALTO: 2", texto)

    def test_los_pasos_salen_con_el_hallazgo(self):
        texto = informe(hallazgo())
        self.assertIn("Cómo solucionarlo", texto)
        self.assertIn("Primer paso", texto)

    def test_un_hallazgo_sin_ganancia_no_promete_ninguna(self):
        self.assertNotIn("Mejora estimada", informe(riesgo()))


class ElBloqueDeSeguridad(unittest.TestCase):
    def test_dice_que_no_acelera_nada(self):
        texto = informe(riesgo())
        self.assertIn("no acelera el equipo", texto)
        self.assertIn("no hay mejora que proyectar", texto)

    def test_no_aparece_si_no_hay_riesgos(self):
        self.assertNotIn("▌ SEGURIDAD", informe(hallazgo()))

    def test_los_riesgos_no_entran_en_el_plan(self):
        texto = informe(riesgo())
        self.assertIn("Nada que priorizar", texto)


class LoQueNoSePudoComprobar(unittest.TestCase):
    def test_no_significa_correcto(self):
        texto = informe(sin_comprobar=[("Chip TPM", "requiere administrador")])
        self.assertIn("no significan «correcto»", texto.replace("No ", "no "))
        self.assertIn("Chip TPM", texto)
        self.assertIn("requiere administrador", texto)

    def test_lo_que_no_aplica_va_aparte_de_lo_que_falta(self):
        # Un TRIM en disco mecánico no es un dato que falte: es una pregunta que
        # no procede. Contarlos juntos exageraría lo que quedó sin mirar.
        texto = informe(sin_comprobar=[("Chip TPM", "requiere administrador")],
                        no_aplican=[("TRIM en SSD", "el disco es mecánico")])
        self.assertIn("No aplican a este equipo", texto)
        self.assertIn("TRIM en SSD", texto)

    def test_sin_pendientes_no_se_saca_la_seccion(self):
        self.assertNotIn("▌ SIN COMPROBAR", informe(hallazgo()))

    def test_las_notas_del_auditor_se_enseñan(self):
        self.assertIn("Una nota suelta", informe(notas=["Una nota suelta"]))


class ElVeredicto(unittest.TestCase):
    """Lo primero que hay que decir cuando hay algo más urgente que optimizar."""

    def _veredicto(self, *findings: Finding) -> tuple[str, list[str]]:
        si = SystemInfo()
        auditor = Auditor(si, None)
        auditor.findings = list(findings)
        return build_verdict(si, None, auditor,
                             project_improvement(None, auditor.findings))

    def test_un_disco_degradado_manda_sobre_todo_lo_demas(self):
        texto, _ = self._veredicto(hallazgo("smart_warn", gain=0.0), hallazgo())
        self.assertIn("copia de seguridad", texto)
        self.assertIn("Antes de cualquier optimización", texto)

    def test_un_hdd_de_sistema_es_lo_siguiente(self):
        texto, extra = self._veredicto(hallazgo("hdd_system", gain=0.85))
        self.assertIn("cuello de botella es físico", texto)
        self.assertIn("SSD", " ".join(extra))

    def test_el_disco_pesa_mas_que_lo_termico(self):
        # Los dos a la vez: el disco moribundo se dice primero, porque de nada
        # sirve limpiar un disipador si el disco se va a llevar los datos.
        texto, _ = self._veredicto(hallazgo("smart_warn", gain=0.0),
                                   hallazgo("thermal_critical", gain=0.30))
        self.assertIn("copia de seguridad", texto)

    def test_lo_termico_va_antes_que_el_software(self):
        texto, extra = self._veredicto(hallazgo("thermal_critical", gain=0.30))
        self.assertIn("térmicos", texto)
        self.assertIn("refrigeración", " ".join(extra))

    def test_un_equipo_sano_no_recibe_una_regañina(self):
        texto, _ = self._veredicto()
        self.assertNotIn("copia de seguridad", texto)
        self.assertNotIn("cuello de botella", texto)


if __name__ == "__main__":
    unittest.main()
