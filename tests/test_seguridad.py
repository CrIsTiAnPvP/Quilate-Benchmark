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

from quilate import audit
from quilate.audit import (SEGURIDAD, Auditor, Finding, NoAplica, SinDato,
                           _estado_antivirus, security_findings)
from quilate.platform_utils import PSResult
from quilate.export.html_export import export_html
from quilate.export.plan_export import export_plan
from quilate.projection import project_improvement, priority_rank
from quilate.sysinfo import SystemInfo
from tests.support import patched


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


class ProteccionEnTiempoReal(unittest.TestCase):
    """`check_antivirus` leía `productState` y no lo decodificaba.

    El reparto del entero (0xAABBCC) no está documentado por Microsoft, así que
    lo que no se reconozca no se interpreta. Y la trampa que hay que esquivar:
    cuando se instala un antivirus de terceros, Defender se apaga solo, y esa es
    la configuración correcta.
    """

    # Valores reales del Centro de seguridad.
    ACTIVO_AL_DIA = 0x061100
    ACTIVO_CADUCADO = 0x061110
    APAGADO = 0x060100

    def _auditar(self, productos: list[dict]):
        a = Auditor(SystemInfo(), None)
        with patched(audit, wmi=PSResult(productos)):
            a.check_antivirus()
        return a

    def _ids(self, a: Auditor) -> list[str]:
        return [f.id for f in a.findings]

    def test_defender_solo_y_bien(self):
        a = self._auditar([{"displayName": "Windows Defender",
                            "productState": self.ACTIVO_AL_DIA}])
        self.assertEqual(a.findings, [])

    def test_ninguno_vigilando(self):
        a = self._auditar([{"displayName": "Windows Defender",
                            "productState": self.APAGADO}])
        self.assertEqual(self._ids(a), ["av_tiempo_real_off"])
        f = a.findings[0]
        self.assertEqual(f.severity, "critical")
        self.assertEqual(f.category, SEGURIDAD)
        self.assertEqual(f.gain, 0.0)

    def test_defender_apagado_por_un_antivirus_de_terceros_es_correcto(self):
        # El caso más normal de todos. Avisar aquí sería un crítico falso en
        # cualquier equipo con antivirus de pago.
        a = self._auditar([
            {"displayName": "Windows Defender", "productState": self.APAGADO},
            {"displayName": "Kaspersky", "productState": self.ACTIVO_AL_DIA},
        ])
        self.assertNotIn("av_tiempo_real_off", self._ids(a))
        self.assertNotIn("av_desactualizado", self._ids(a))

    def test_firmas_caducadas_con_el_motor_activo(self):
        a = self._auditar([{"displayName": "Windows Defender",
                            "productState": self.ACTIVO_CADUCADO}])
        self.assertEqual(self._ids(a), ["av_desactualizado"])
        self.assertEqual(a.findings[0].severity, "high")

    def test_firmas_caducadas_de_un_motor_apagado_no_importan(self):
        # A nadie le importan las firmas de un antivirus que no está vigilando.
        a = self._auditar([
            {"displayName": "Norton", "productState": 0x060110},
            {"displayName": "Windows Defender", "productState": self.ACTIVO_AL_DIA},
        ])
        self.assertEqual(a.findings, [])

    def test_un_entero_que_no_se_reconoce_no_se_interpreta(self):
        for valor in (None, "0x061100", True, 0x06FF00, 0x0611FF):
            with self.subTest(valor=valor):
                a = self._auditar([{"displayName": "Raro", "productState": valor}])
                self.assertEqual(a.findings, [], f"ha opinado sobre {valor!r}")

    def test_el_solapamiento_de_antivirus_sigue_funcionando(self):
        # El hallazgo que ya existía no puede haberse roto por el camino, y ese
        # sí es de fluidez y sí tiene ganancia.
        a = self._auditar([
            {"displayName": "Norton", "productState": self.ACTIVO_AL_DIA},
            {"displayName": "Kaspersky", "productState": self.ACTIVO_AL_DIA},
        ])
        self.assertIn("av_stack", self._ids(a))
        stack = next(f for f in a.findings if f.id == "av_stack")
        self.assertEqual(stack.category, "fluidez")
        self.assertGreater(stack.gain, 0)

    def test_sin_respuesta_del_centro_de_seguridad(self):
        a = Auditor(SystemInfo(), None)
        with patched(audit, wmi=PSResult((), ok=False, error="acceso denegado")):
            with self.assertRaises(SinDato):
                a.check_antivirus()


class CifradoDelDisco(unittest.TestCase):
    """BitLocker, y sobre todo cuándo NO hay que opinar.

    En Windows Home el cmdlet no existe: eso es `NoAplica`, una pregunta que no
    procede hacer, no un dato que falte. Sin privilegios sí existe pero rechaza
    contestar, y eso sí es `SinDato`. Confundirlos haría que la mitad de los
    equipos aparecieran como pendientes de revisar algo que no tienen.
    """

    def _auditar(self, filas, drive="C:"):
        si = SystemInfo()
        si.system_drive = drive
        a = Auditor(si, None)
        with patched(audit, wmi=filas):
            return a, a.check_disk_encryption()

    def volumen(self, mount="C:", proteccion=1, estado=1) -> dict:
        return {"disponible": True, "MountPoint": mount,
                "VolumeStatus": estado, "ProtectionStatus": proteccion}

    def test_disco_cifrado(self):
        a, resumen = self._auditar(PSResult([self.volumen()]))
        self.assertEqual(a.findings, [])
        self.assertIn("cifrado", resumen)

    def test_disco_sin_cifrar(self):
        a, resumen = self._auditar(PSResult([self.volumen(proteccion=0, estado=0)]))
        self.assertEqual([f.id for f in a.findings], ["sin_cifrado"])
        f = a.findings[0]
        self.assertEqual((f.severity, f.category, f.gain), ("high", SEGURIDAD, 0.0))

    def test_avisa_de_guardar_la_clave_antes_de_nada(self):
        # Activar BitLocker sin guardar la clave de recuperación es la forma
        # más rápida de perder todos los datos por intentar protegerlos.
        a, _ = self._auditar(PSResult([self.volumen(proteccion=0)]))
        pasos = " ".join(a.findings[0].steps).upper()
        self.assertIn("CLAVE DE RECUPERACIÓN", pasos)

    def test_en_windows_home_no_aplica(self):
        # El cmdlet no existe. No es un dato que falte: es una pregunta que no
        # procede, y no puede contar como comprobación pendiente.
        with self.assertRaises(NoAplica):
            self._auditar(PSResult([{"disponible": False}]))

    def test_sin_privilegios_es_sin_dato(self):
        with self.assertRaises(SinDato):
            self._auditar(PSResult((), ok=False, error="Acceso denegado"))

    def test_solo_mira_el_volumen_de_sistema(self):
        # Un disco de datos sin cifrar no es lo mismo que el del sistema.
        a, _ = self._auditar(PSResult([self.volumen("C:", proteccion=1),
                                       self.volumen("D:", proteccion=0)]))
        self.assertEqual(a.findings, [])

    def test_si_no_esta_el_de_sistema_no_se_opina(self):
        with self.assertRaises(SinDato):
            self._auditar(PSResult([self.volumen("D:", proteccion=0)]))

    def test_acepta_los_estados_por_nombre(self):
        # Según la versión de PowerShell, ConvertTo-Json serializa la
        # enumeración como entero o como texto.
        a, _ = self._auditar(PSResult([self.volumen(proteccion="On", estado="FullyEncrypted")]))
        self.assertEqual(a.findings, [])
        b, _ = self._auditar(PSResult([self.volumen(proteccion="Off", estado="FullyDecrypted")]))
        self.assertEqual([f.id for f in b.findings], ["sin_cifrado"])

    def test_un_estado_desconocido_no_acusa_a_nadie(self):
        # Decirle a alguien que su disco está desprotegido cuando sí lo está es
        # como se consigue que deje de leer el informe.
        with self.assertRaises(SinDato):
            self._auditar(PSResult([self.volumen(proteccion="Vete a saber", estado=None)]))


class Decodificador(unittest.TestCase):
    def test_los_dos_bytes_que_importan(self):
        self.assertEqual(_estado_antivirus(0x061100), (True, True))
        self.assertEqual(_estado_antivirus(0x061110), (True, False))
        self.assertEqual(_estado_antivirus(0x060100), (False, True))
        # 0x11 también significa vigilando: lo usan algunos productos.
        self.assertEqual(_estado_antivirus(0x061000), (True, True))

    def test_el_tipo_de_producto_no_altera_la_lectura(self):
        for tipo in (0x00, 0x04, 0x06, 0xFF):
            with self.subTest(tipo=hex(tipo)):
                self.assertEqual(_estado_antivirus((tipo << 16) | 0x1100), (True, True))


if __name__ == "__main__":
    unittest.main()
