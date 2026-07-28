"""La categoría `seguridad` y su sitio en las tres salidas.

Un hallazgo de seguridad no aporta rendimiento: cifrar el disco no acelera nada.
Emitirlo con ganancia lo metería en `project_improvement` y en `category_gain`,
y el informe acabaría diciendo «cifrar el disco te dará un +8% de fluidez», que
es falso. El patrón correcto ya existía en `smart_warn`: `gain=0.0` y una nota
que explica que no es una optimización.

Pero con `gain=0` el filtro del plan de acción —que es `gain > 0` en las tres
salidas— los descartaba antes incluso de ordenarlos, así que no aparecían en la
sección que la gente lee. De ahí el bloque propio: por delante del plan, porque
un disco sin cifrar importa más que ocho puntos de fluidez, y fuera de él,
porque el plan promete un retorno que estos hallazgos no dan.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path

from quilate.audit import SEGURIDAD, Auditor, Finding, SinDato, security_findings
from quilate.export.html_export import export_html
from quilate.export.plan_export import export_plan
from quilate.projection import project_improvement, priority_rank
from quilate.sysinfo import SystemInfo


def riesgo(id_: str, severity: str = "high", title: str = "") -> Finding:
    """Un hallazgo de seguridad como debe ser: sin ganancia que prometer."""
    return Finding(
        id=id_, title=title or f"Riesgo de «{id_}»", severity=severity,
        category=SEGURIDAD, component="system",
        detail="Detalle del riesgo plantado por el test.",
        gain=0.0, gain_note="no es una optimización: es un riesgo",
        effort="bajo", risk="nulo", steps=["Primer paso", "Segundo paso"])


def mejora(id_: str = "power_plan") -> Finding:
    return Finding(id=id_, title=f"Mejora de «{id_}»", severity="medium",
                   category="fluidez", component="cpu_multi", detail="d",
                   gain=0.10, gain_note="n", effort="bajo", risk="nulo", steps=["paso"])


def auditor_con(*findings: Finding) -> Auditor:
    a = Auditor(SystemInfo(), None)
    a.findings = list(findings)
    return a


class NoContaminaLaProyeccion(unittest.TestCase):
    """Lo que el informe advierte antes de implementar nada de esto."""

    def test_no_aparece_en_el_margen_por_area(self):
        proyeccion = project_improvement(None, [riesgo("sin_cifrado"), mejora()])
        self.assertNotIn(SEGURIDAD, proyeccion["category_gain"])
        self.assertIn("fluidez", proyeccion["category_gain"])

    def test_no_suma_ganancia_a_ningun_componente(self):
        solo_riesgos = project_improvement(None, [riesgo("a"), riesgo("b")])
        self.assertEqual(solo_riesgos["component_gain"], {})
        self.assertEqual(solo_riesgos["system_gain"], 0.0)


class ElOrden(unittest.TestCase):
    def test_de_mas_grave_a_menos(self):
        desordenados = [riesgo("c", "low"), riesgo("a", "critical"), riesgo("b", "high")]
        self.assertEqual([f.id for f in security_findings(desordenados)], ["a", "b", "c"])

    def test_solo_recoge_los_de_seguridad(self):
        self.assertEqual([f.id for f in security_findings([riesgo("x"), mejora()])], ["x"])

    def test_no_pasan_por_el_orden_del_plan(self):
        # `priority_rank` ordena por retorno dividido por esfuerzo. Con gain=0
        # todos empatarían a cero, que es justo por lo que no se usa aquí.
        self.assertEqual(priority_rank(riesgo("a", "critical"))[0],
                         priority_rank(riesgo("b", "low"))[0])


class EnLaConsola(unittest.TestCase):
    def _informe(self, auditor: Auditor) -> str:
        from quilate.report import print_report
        from quilate.console import C
        C.disable()
        salida = io.StringIO()
        with redirect_stdout(salida):
            print_report(SystemInfo(), None, auditor,
                         project_improvement(None, auditor.findings))
        return salida.getvalue()

    def test_el_bloque_sale_antes_del_plan(self):
        texto = self._informe(auditor_con(riesgo("sin_cifrado"), mejora()))
        self.assertIn("SEGURIDAD", texto.upper())
        self.assertLess(texto.upper().index("SEGURIDAD"),
                        texto.upper().index("PLAN DE ACCIÓN"),
                        "el riesgo aparece después del plan de optimización")

    def test_dice_que_no_acelera_nada(self):
        texto = self._informe(auditor_con(riesgo("sin_cifrado")))
        self.assertIn("no acelera", texto)

    def test_trae_titulo_y_pasos(self):
        texto = self._informe(auditor_con(riesgo("sin_cifrado")))
        self.assertIn("Riesgo de «sin_cifrado»", texto)
        self.assertIn("Primer paso", texto)

    def test_sin_riesgos_no_hay_bloque(self):
        texto = self._informe(auditor_con(mejora()))
        self.assertNotIn("son riesgos", texto)

    def test_no_se_cuela_en_el_plan_de_accion(self):
        texto = self._informe(auditor_con(riesgo("sin_cifrado"), mejora()))
        # Solo la sección del plan: el veredicto de más abajo también habla de
        # porcentajes y no es lo que se está comprobando aquí.
        plan = texto.upper().split("PLAN DE ACCIÓN")[1].split("VEREDICTO")[0]
        self.assertNotIn("SIN_CIFRADO", plan)
        self.assertNotIn("+0%", plan, "una fila con +0% se lee como un error")


class EnElHtml(unittest.TestCase):
    def _html(self, auditor: Auditor) -> str:
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "informe.html"
            export_html(destino, SystemInfo(), None, auditor,
                        project_improvement(None, auditor.findings))
            return destino.read_text(encoding="utf-8")

    def test_hay_seccion_de_seguridad(self):
        html = self._html(auditor_con(riesgo("sin_cifrado"), mejora()))
        self.assertIn('id="s-sin_cifrado"', html)
        self.assertIn("Seguridad", html)

    def test_va_antes_que_el_plan(self):
        # Por el `id` de la sección, no por su título: el título aparece antes en
        # el índice de navegación, que es otra cosa y va en otro orden.
        html = self._html(auditor_con(riesgo("sin_cifrado"), mejora()))
        self.assertLess(html.index('id="seguridad"'), html.index('id="plan"'))

    def test_sin_riesgos_no_se_pinta(self):
        self.assertNotIn('id="s-', self._html(auditor_con(mejora())))

    def test_la_severidad_critica_se_ve(self):
        html = self._html(auditor_con(riesgo("av_off", "critical")))
        self.assertIn('class="badge b-critical"', html)


class EnElPlanPowerShell(unittest.TestCase):
    def _ps1(self, auditor: Auditor) -> str:
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "plan.ps1"
            export_plan(ruta, SystemInfo(), None, auditor)
            return ruta.read_text(encoding="utf-8-sig")

    # La plantilla ya trae un «BLOQUE 0: RED DE SEGURIDAD», así que buscar
    # «SEGURIDAD» a secas encuentra ese y no este. El encabezado propio es único.
    MARCA = "SEGURIDAD - REVISALO"

    def test_los_riesgos_van_como_comentario(self):
        texto = self._ps1(auditor_con(riesgo("sin_cifrado"), mejora("sysmain")))
        # Desde la línea siguiente al encabezado: partir por la marca deja
        # media línea suelta, que no es lo que se está comprobando.
        seccion = texto.split(self.MARCA)[1].split("\n", 1)[1].split("--- BLOQUE 1")[0]
        for linea in seccion.splitlines():
            if linea.strip():
                self.assertTrue(linea.lstrip().startswith("#"), linea)
        self.assertIn("Riesgo de «sin_cifrado»", texto)

    def test_no_se_automatiza_ninguno(self):
        # Activar BitLocker sin haber guardado la clave de recuperación no lo
        # puede decidir un script.
        texto = self._ps1(auditor_con(riesgo("sin_cifrado")))
        self.assertNotIn('Bloque -Titulo "Riesgo', texto)

    def test_van_antes_del_primer_bloque_automatizado(self):
        texto = self._ps1(auditor_con(riesgo("sin_cifrado"), mejora("sysmain")))
        self.assertLess(texto.index(self.MARCA), texto.index("--- BLOQUE 1"))


class BiosAntigua(unittest.TestCase):
    """El dato ya se recogía y nadie lo auditaba: `si.bios_date`.

    Es la comprobación más barata de todas las de seguridad, y la única que no
    necesita preguntarle nada al sistema.
    """

    def _auditar(self, bios_date):
        si = SystemInfo()
        si.bios_date = bios_date
        a = Auditor(si, None)
        return a, a.check_bios_age()

    def _hace(self, años: float) -> str:
        return (date.today() - timedelta(days=round(años * 365.25))).strftime("%Y-%m-%d")

    def test_una_bios_reciente_no_es_un_hallazgo(self):
        a, resumen = self._auditar(self._hace(1))
        self.assertEqual(a.findings, [])
        self.assertIn(str(date.today().year - 1), resumen)

    def test_a_partir_de_tres_años_avisa(self):
        a, _ = self._auditar(self._hace(4))
        self.assertEqual([f.id for f in a.findings], ["bios_vieja"])
        self.assertEqual(a.findings[0].severity, "low")

    def test_muy_antigua_sube_de_severidad(self):
        a, _ = self._auditar(self._hace(7))
        self.assertEqual(a.findings[0].severity, "medium")

    def test_el_hallazgo_no_promete_velocidad(self):
        a, _ = self._auditar(self._hace(7))
        f = a.findings[0]
        self.assertEqual(f.category, SEGURIDAD)
        self.assertEqual(f.gain, 0.0)
        self.assertIn("no es una optimización", f.gain_note)

    def test_no_dice_de_que_cve_se_trata(self):
        # Cruzarlo con CVE concretas exigiría una base de datos externa y
        # rompería el «no envía nada a ninguna parte».
        a, _ = self._auditar(self._hace(7))
        self.assertNotIn("CVE", a.findings[0].detail)

    def test_sin_fecha_no_se_opina(self):
        with self.assertRaises(SinDato):
            self._auditar(None)

    def test_una_fecha_ilegible_tampoco(self):
        # Un SMBIOS que devuelva basura no puede acusar a nadie de nada.
        with self.assertRaises(SinDato):
            self._auditar("vete a saber")

    def test_el_año_del_titulo_es_el_de_la_bios(self):
        fecha = self._hace(6)
        a, _ = self._auditar(fecha)
        self.assertIn(fecha[:4], a.findings[0].title)


if __name__ == "__main__":
    unittest.main()
