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

import inspect
import io
import os
import re
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path

from quilate import audit
from quilate.audit import (SEGURIDAD, SEVERITY_ORDER, Auditor, Finding, NoAplica,
                           SinDato, _ESFUERZOS, _RIESGOS, _estado_antivirus,
                           security_findings)
from quilate.audit.tablas import (_RDP_CLAVE, _RDP_TCP_CLAVE, _SOPORTE_REVISADO,
                                  _build_de, _tabla_de_soporte_caducada)
from quilate.const import IS_WINDOWS
from quilate.platform_utils import PSResult, _system_drive
from quilate.export.html_export import export_html
from quilate.export.plan_export import export_plan
from quilate.projection import project_improvement, priority_rank
from quilate.sysinfo import SystemInfo
from tests.support import FakeRegistry, fuente_completa, patched


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


class EnLaFichaDeComponente(unittest.TestCase):
    """Un riesgo de seguridad no es una «mejora aplicable».

    La ficha por componente metía en un solo bloque todo lo que caía en ese
    grupo y lo titulaba «Mejoras aplicables». Sobre «SMB1 activo» o «el disco no
    está cifrado» eso no es un matiz de redacción: dice que arreglarlo mejora el
    rendimiento, que es falso, y los mete en la misma lista que un plan de
    energía. Nueve de los diez riesgos caen en la ficha «Sistema y software», así
    que era el caso normal y no un borde.
    """

    def ficha(self, auditor: Auditor, clave: str = "system"):
        from quilate.components import build_component_cards
        fichas = build_component_cards(SystemInfo(), None, auditor)
        return next((c for c in fichas if c.key == clave), None)

    def html(self, auditor: Auditor, clave: str = "system") -> str:
        """El HTML de UNA ficha, no el informe entero.

        Las demás fichas dicen «Sin mejoras pendientes» con toda la razón —un
        equipo puede tener la CPU impecable y el sistema comprometido—, así que
        buscar esa frase en el documento completo no probaría nada.
        """
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "informe.html"
            export_html(destino, SystemInfo(), None, auditor,
                        project_improvement(None, auditor.findings))
            completo = destino.read_text(encoding="utf-8")
        inicio = completo.index(f'id="c-{clave}"')
        siguiente = completo.find('<div class="card" id="c-', inicio + 1)
        return completo[inicio:siguiente if siguiente != -1 else len(completo)]

    def test_los_riesgos_no_van_en_las_mejoras(self):
        card = self.ficha(auditor_con(riesgo("smb1_activo"), mejora("sysmain")))
        self.assertEqual([f.id for f in card.riesgos], ["smb1_activo"])
        self.assertNotIn("smb1_activo", [f.id for f in card.findings])

    def test_el_corte_es_por_categoria_y_no_por_quien_lo_emite(self):
        # `av_stack` lo emite `check_antivirus`, que vive entre las
        # comprobaciones de seguridad, pero es un hallazgo de fluidez: dos
        # motores en tiempo real cuestan E/S. Su sitio son las mejoras.
        av_stack = Finding(id="av_stack", title="Varios antivirus", severity="high",
                           category="fluidez", component="system", detail="d",
                           gain=0.08, gain_note="n", effort="bajo", risk="nulo",
                           steps=["paso"])
        card = self.ficha(auditor_con(av_stack, riesgo("smb1_activo")))
        self.assertEqual([f.id for f in card.findings], ["av_stack"])
        self.assertEqual([f.id for f in card.riesgos], ["smb1_activo"])

    def test_los_riesgos_no_inventan_ganancia_en_la_ficha(self):
        card = self.ficha(auditor_con(riesgo("smb1_activo"), riesgo("sin_tpm")))
        self.assertEqual(card.gain, 0.0)

    def test_una_ficha_con_solo_riesgos_no_desaparece(self):
        # Si la condición de incluir la ficha solo mirase `findings`, un equipo
        # cuyo único problema fuera de seguridad se quedaría sin ficha.
        self.assertIsNotNone(self.ficha(auditor_con(riesgo("smb1_activo"))))

    def test_el_html_les_da_encabezado_propio_y_enlace(self):
        html = self.html(auditor_con(riesgo("smb1_activo")))
        self.assertIn("Riesgos de este componente", html)
        self.assertIn('href="#s-smb1_activo"', html)

    def test_el_html_no_canta_victoria_con_riesgos_delante(self):
        # Lo peor que podía pasar al separar las listas: la ficha del sistema de
        # un equipo con SMB1 activo enseñando un «sin nada pendiente» en verde.
        html = self.html(auditor_con(riesgo("smb1_activo")))
        self.assertNotIn("Sin mejoras pendientes", html)

    def test_el_html_sigue_diciendolo_cuando_de_verdad_no_hay_nada(self):
        self.assertIn("Sin mejoras pendientes", self.html(auditor_con(mejora("sysmain"))))

    def test_la_consola_hace_lo_mismo(self):
        from quilate.console import C
        from quilate.report import print_report
        C.disable()
        auditor = auditor_con(riesgo("smb1_activo"))
        salida = io.StringIO()
        with redirect_stdout(salida):
            print_report(SystemInfo(), None, auditor,
                         project_improvement(None, auditor.findings))
        # Solo el bloque de «Sistema y software», por lo mismo que en el HTML:
        # que la ficha del procesador diga que no tiene mejoras pendientes es
        # correcto y no tiene nada que ver con esto.
        texto = salida.getvalue()
        inicio = texto.index("SISTEMA Y SOFTWARE")
        ficha = texto[inicio:texto.index("▌", inicio)]
        self.assertIn("Riesgos de este componente", ficha)
        self.assertNotIn("Sin mejoras pendientes", ficha)

    def test_el_json_expone_los_dos_campos(self):
        # El informe pide que no se pierda información: el JSON tiene que seguir
        # diciendo qué hay en cada ficha, ahora en dos listas en vez de una.
        from quilate.export.json_export import build_payload
        auditor = auditor_con(riesgo("smb1_activo"), mejora("sysmain"))
        payload = build_payload(SystemInfo(), None, auditor,
                                project_improvement(None, auditor.findings))
        sistema = next(c for c in payload["components"] if c["key"] == "system")
        self.assertEqual([f["id"] for f in sistema["riesgos"]], ["smb1_activo"])
        self.assertNotIn("smb1_activo", [f["id"] for f in sistema["findings"]])


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


class Cortafuegos(unittest.TestCase):
    """Tres perfiles, no uno.

    Windows aplica el perfil de la red a la que estés conectado. Un portátil
    con el perfil Público apagado va sin cortafuegos en la wifi del aeropuerto
    y con él en casa, y desde el panel de control eso se ve como dos de tres en
    verde. Por eso Público y Privado pesan más que Dominio, que solo aplica en
    una red de empresa donde suele haber una política central delante.
    """

    def _auditar(self, perfiles):
        a = Auditor(SystemInfo(), None)
        with patched(audit.seguridad, ps_json=lambda *a_, **k: perfiles):
            return a, a.check_firewall()

    def perfil(self, nombre, activo=1):
        return {"Name": nombre, "Enabled": activo}

    def todos(self, activo=1):
        return [self.perfil(n, activo) for n in ("Domain", "Private", "Public")]

    def test_los_tres_activos_no_generan_nada(self):
        a, resumen = self._auditar(self.todos())
        self.assertEqual(a.findings, [])
        self.assertIn("3 perfiles", resumen)

    def test_publico_apagado_es_grave(self):
        a, _ = self._auditar([self.perfil("Domain"), self.perfil("Private"),
                              self.perfil("Public", 0)])
        self.assertEqual([f.id for f in a.findings], ["firewall_off"])
        f = a.findings[0]
        self.assertEqual((f.severity, f.category, f.gain), ("high", SEGURIDAD, 0.0))
        self.assertIn("Public", f.title)

    def test_privado_apagado_tambien_es_grave(self):
        a, _ = self._auditar([self.perfil("Domain"), self.perfil("Private", 0),
                              self.perfil("Public")])
        self.assertEqual(a.findings[0].severity, "high")

    def test_solo_dominio_apagado_es_medio(self):
        # No es lo mismo: en una red con controlador suele haber politica
        # central y un cortafuegos perimetral delante.
        a, _ = self._auditar([self.perfil("Domain", 0), self.perfil("Private"),
                              self.perfil("Public")])
        self.assertEqual(a.findings[0].severity, "medium")
        self.assertIn("Dominio", a.findings[0].detail)

    def test_los_apagados_se_nombran_todos(self):
        a, resumen = self._auditar(self.todos(activo=0))
        for nombre in ("Domain", "Private", "Public"):
            self.assertIn(nombre, a.findings[0].title)
            self.assertIn(nombre, resumen)

    def test_acepta_las_dos_formas_de_contestar(self):
        # `ConvertTo-Json` serializa la enumeración GpoBoolean unas veces como
        # entero y otras por nombre, según la versión de PowerShell.
        for apagado in (0, "0", False, "False", "NotConfigured"):
            with self.subTest(valor=apagado):
                a, _ = self._auditar([self.perfil("Public", apagado)])
                self.assertEqual([f.id for f in a.findings], ["firewall_off"])
        for encendido in (1, "1", True, "True", "Enabled"):
            with self.subTest(valor=encendido):
                a, _ = self._auditar([self.perfil("Public", encendido)])
                self.assertEqual(a.findings, [])

    def test_un_estado_desconocido_no_acusa_a_nadie(self):
        with self.assertRaises(SinDato):
            self._auditar([self.perfil("Public", "QuizasQuizas")])

    def test_sin_respuesta_es_sin_dato(self):
        with self.assertRaises(SinDato):
            self._auditar(PSResult((), ok=False, error="no existe el cmdlet"))

    def test_una_lista_vacia_no_es_un_equipo_sin_cortafuegos(self):
        with self.assertRaises(SinDato):
            self._auditar([])


class VersionDeWindowsConSoporte(unittest.TestCase):
    """La comprobación que hace inútiles a casi todas las demás.

    En un Windows fuera de soporte, los fallos que se descubran a partir de la
    fecha de fin no se arreglan nunca. Pero el dato sale de una tabla escrita a
    mano, así que lo que de verdad hay que probar no es solo que detecte: es
    que **se calle cuando la tabla ya no es de fiar**. Afirmar «tu Windows no
    recibe parches» con una tabla vieja es un aviso sobre el que la gente actúa.
    """

    def _auditar(self, build, nombre="Microsoft Windows 11 Pro"):
        si = SystemInfo()
        si.os_build = build
        si.os_name = nombre
        a = Auditor(si, None)
        return a, a.check_windows_soportado()

    def test_una_version_con_soporte_no_genera_nada(self):
        # 24H2 para Home/Pro: hasta 2026-10-13.
        a, resumen = self._auditar("10.0.26100 (build 26100)")
        self.assertEqual(a.findings, [])
        self.assertIn("2026-10-13", resumen)

    def test_una_version_caducada_es_un_hallazgo(self):
        # Windows 10 22H2 dejó de recibir parches en octubre de 2025.
        a, resumen = self._auditar("10.0.19045 (build 19045)")
        self.assertEqual([f.id for f in a.findings], ["windows_sin_soporte"])
        f = a.findings[0]
        self.assertEqual((f.severity, f.category, f.gain), ("high", SEGURIDAD, 0.0))
        self.assertIn("2025-10-14", f.detail)
        self.assertEqual(resumen, "SIN SOPORTE")

    def test_enterprise_recibe_la_fecha_larga(self):
        # Misma build, dos veredictos: 22H2 caducó en 2024 para Home/Pro y en
        # octubre de 2025 para Enterprise. Usar una sola fecha marcaría como
        # caducado un equipo que sí tenía parches.
        pro, _ = self._auditar("10.0.22621 (build 22621)")
        self.assertEqual([f.id for f in pro.findings], ["windows_sin_soporte"])
        ent, _ = self._auditar("10.0.22621 (build 22621)",
                               "Microsoft Windows 11 Enterprise")
        self.assertIn("Enterprise", ent.findings[0].detail)

    def test_lo_anterior_a_la_tabla_no_necesita_tabla(self):
        # Windows 7 y 8.1 llevan años sin parches: esto no caduca, solo puede
        # volverse más cierto.
        a, _ = self._auditar("6.1.7601 (build 7601)")
        self.assertEqual([f.id for f in a.findings], ["windows_sin_soporte"])
        self.assertIn("anterior a Windows 10", a.findings[0].title)

    def test_una_build_mas_nueva_que_la_tabla_no_se_da_por_buena(self):
        # No saber no es lo mismo que estar bien. Si se devolviera «con
        # soporte», la tabla envejecería en silencio.
        with self.assertRaises(SinDato):
            self._auditar("10.0.99999 (build 99999)")

    def test_con_la_tabla_caducada_no_se_afirma_nada(self):
        # La garantía que pide el informe: la tabla se revisa a mano, así que
        # cuando lleva demasiado sin tocarse esta comprobación se calla.
        with mock.patch.object(audit.seguridad, "_tabla_de_soporte_caducada",
                               return_value=True):
            with self.assertRaises(SinDato) as caso:
                self._auditar("10.0.19045 (build 19045)")
        self.assertIn(_SOPORTE_REVISADO, str(caso.exception))

    def test_sin_build_legible_no_se_opina(self):
        for valor in ("", None, "no hay build aquí"):
            with self.subTest(valor=valor):
                with self.assertRaises(SinDato):
                    self._auditar(valor)

    def test_la_build_se_lee_de_las_dos_formas(self):
        self.assertEqual(_build_de("10.0.26100 (build 26100)"), 26100)
        self.assertEqual(_build_de("10.0.26100"), 26100)
        self.assertIsNone(_build_de("Linux 6.8"))

    def test_la_tabla_no_esta_caducada_hoy(self):
        # Si este test falla, la tabla necesita una revisión: no es un fallo de
        # código, es el recordatorio funcionando.
        self.assertFalse(
            _tabla_de_soporte_caducada(),
            f"la tabla de fin de soporte se revisó en {_SOPORTE_REVISADO} y ya "
            f"ha caducado: revísala y actualiza la fecha")


class MotorDePowerShell2(unittest.TestCase):
    """El intérprete que no deja registro.

    Lo que importa de PowerShell 2.0 no es que sea de 2009: es que no registra
    los bloques de script, no deja transcripción y no pasa por AMSI. Mientras
    esté instalado, `powershell -Version 2` ejecuta lo mismo sin rastro.
    """

    def _auditar(self, estado):
        a = Auditor(SystemInfo(), None)
        with patched(audit.seguridad, elevado={"powershell2": estado}):
            return a, a.check_powershell_v2()

    def test_desinstalado_no_genera_nada(self):
        a, resumen = self._auditar(PSResult([{"disponible": True, "State": "Disabled"}]))
        self.assertEqual(a.findings, [])
        self.assertIn("desinstalado", resumen)

    def test_instalado_es_un_hallazgo(self):
        a, resumen = self._auditar(PSResult([{"disponible": True, "State": "Enabled"}]))
        self.assertEqual([f.id for f in a.findings], ["powershell_v2"])
        f = a.findings[0]
        self.assertEqual((f.severity, f.category, f.gain), ("medium", SEGURIDAD, 0.0))
        self.assertIn("AMSI", f.detail)
        self.assertEqual(resumen, "INSTALADO")

    def test_acepta_el_estado_como_entero_o_como_nombre(self):
        for valor in (2, "Disabled", "DisabledWithPayloadRemoved"):
            with self.subTest(valor=valor):
                a, _ = self._auditar(PSResult([{"disponible": True, "State": valor}]))
                self.assertEqual(a.findings, [])
        for valor in (1, "Enabled"):
            with self.subTest(valor=valor):
                a, _ = self._auditar(PSResult([{"disponible": True, "State": valor}]))
                self.assertEqual([f.id for f in a.findings], ["powershell_v2"])

    def test_un_estado_desconocido_no_acusa_a_nadie(self):
        with self.assertRaises(SinDato):
            self._auditar(PSResult([{"disponible": True, "State": "Vaya"}]))

    def test_sin_el_cmdlet_no_aplica(self):
        with self.assertRaises(NoAplica):
            self._auditar(PSResult([{"disponible": False}]))

    def test_sin_permisos_es_sin_dato(self):
        # Sin privilegios no se sabe, y no saber no es «está desinstalado».
        a = Auditor(SystemInfo(), None)
        with patched(audit.seguridad):
            with self.assertRaises(SinDato):
                a.check_powershell_v2()

    def test_viaja_en_el_lote_que_ya_existia(self):
        # La condición que pone el informe: ni un proceso ni un aviso de UAC
        # nuevos. Se cumple por estar en el lote, que se pregunta de una vez.
        from quilate.elevacion import _CONSULTAS_ELEVADAS
        self.assertIn("powershell2", _CONSULTAS_ELEVADAS)
        self.assertIn("MicrosoftWindowsPowerShellV2Root",
                      _CONSULTAS_ELEVADAS["powershell2"])


class EscritorioRemoto(unittest.TestCase):
    """Dos preguntas distintas: si RDP está abierto, y si autentica primero.

    Que RDP esté activo no es un fallo —hay quien lo usa a diario— pero sí una
    puerta que mucha gente tiene abierta sin saberlo. Lo que sí es un fallo es
    tenerlo activo sin NLA: sin autenticación a nivel de red, Windows levanta la
    sesión antes de saber quién llama, que es la condición de BlueKeep.
    """

    def _auditar(self, deny=None, nla=None):
        arbol = {}
        if deny is not None:
            arbol[f"HKLM\\{_RDP_CLAVE}"] = {"fDenyTSConnections": deny}
        if nla is not None:
            arbol[f"HKLM\\{_RDP_TCP_CLAVE}"] = {"UserAuthentication": nla}
        a = Auditor(SystemInfo(), None)
        with patched(audit.seguridad, registry=FakeRegistry(arbol)):
            return a, a.check_escritorio_remoto()

    def test_desactivado_no_genera_nada(self):
        a, resumen = self._auditar(deny=1)
        self.assertEqual(a.findings, [])
        self.assertIn("desactivado", resumen)

    def test_activo_con_nla_es_un_aviso_leve(self):
        a, _ = self._auditar(deny=0, nla=1)
        self.assertEqual([f.id for f in a.findings], ["rdp_activo"])
        f = a.findings[0]
        self.assertEqual((f.severity, f.category, f.gain), ("low", SEGURIDAD, 0.0))

    def test_activo_sin_nla_es_grave(self):
        a, resumen = self._auditar(deny=0, nla=0)
        self.assertEqual([f.id for f in a.findings], ["rdp_sin_nla"])
        self.assertEqual(a.findings[0].severity, "high")
        self.assertIn("BlueKeep", a.findings[0].detail)
        self.assertIn("sin NLA", resumen)

    def test_sin_el_valor_de_nla_se_trata_como_que_no_lo_exige(self):
        # Ausente equivale a no exigirlo, y se dice en el detalle en vez de
        # dejar al usuario suponiendo.
        a, _ = self._auditar(deny=0)
        self.assertEqual([f.id for f in a.findings], ["rdp_sin_nla"])
        self.assertIn("no está en el registro", a.findings[0].detail)

    def test_sin_poder_leer_la_rama_es_sin_dato(self):
        # El valor existe en cualquier Windows de escritorio: que no esté
        # significa que no se ha podido leer, no que RDP esté apagado.
        with self.assertRaises(SinDato):
            self._auditar()

    def test_no_lanza_ningun_proceso(self):
        # Dos valores del registro: ni PowerShell ni privilegios.
        fuente = inspect.getsource(Auditor.check_escritorio_remoto)
        self.assertNotIn("ps_json", fuente)
        self.assertNotIn("elevacion", fuente)


class CuentaAdministradorDeFabrica(unittest.TestCase):
    """La del RID -500, reconocida por SID y nunca por nombre.

    «Administrador», «Administrator» y el nombre que le haya puesto quien la
    renombrase son la misma cuenta. Buscarla por texto falla en cuanto el
    Windows no está en inglés, que es el error que documenta
    `check_filesystem_health`.
    """

    def _auditar(self, cuentas):
        respuesta = cuentas if isinstance(cuentas, PSResult) else PSResult(cuentas)
        a = Auditor(SystemInfo(), None)
        with patched(audit.seguridad, ps_json=lambda *a_, **k: respuesta):
            return a, a.check_cuenta_administrador()

    def cuenta(self, nombre, sid, habilitada=True):
        return {"disponible": True, "Name": nombre, "SID": sid,
                "Enabled": habilitada, "PasswordRequired": True}

    def test_deshabilitada_es_lo_normal(self):
        a, resumen = self._auditar([self.cuenta("Administrador", "S-1-5-21-1-2-3-500",
                                                habilitada=False)])
        self.assertEqual(a.findings, [])
        self.assertIn("de fábrica", resumen)

    def test_habilitada_es_un_hallazgo(self):
        a, _ = self._auditar([self.cuenta("Administrador", "S-1-5-21-1-2-3-500")])
        self.assertEqual([f.id for f in a.findings], ["admin_integrado_activo"])
        f = a.findings[0]
        self.assertEqual((f.severity, f.category, f.gain), ("medium", SEGURIDAD, 0.0))

    def test_se_reconoce_aunque_este_renombrada(self):
        # Es lo que gana identificarla por SID: renombrarla no la esconde.
        a, _ = self._auditar([self.cuenta("Paco", "S-1-5-21-1-2-3-500")])
        self.assertEqual([f.id for f in a.findings], ["admin_integrado_activo"])
        self.assertIn("Paco", a.findings[0].title)

    def test_una_cuenta_que_se_llame_administrador_no_cuela(self):
        # Al revés que lo anterior: el nombre no basta para acusar. Una cuenta
        # creada a mano y llamada «Administrador» no es la del RID -500.
        a, _ = self._auditar([self.cuenta("Administrador", "S-1-5-21-1-2-3-1001")])
        self.assertEqual(a.findings, [])

    def test_un_sid_que_acabe_en_500_de_otro_modo_no_confunde(self):
        for sid in ("S-1-5-21-1-2-3-1500", "S-1-5-21-1-2-3-5000"):
            with self.subTest(sid=sid):
                a, _ = self._auditar([self.cuenta("X", sid)])
                self.assertEqual(a.findings, [])

    def test_sin_sid_no_se_opina(self):
        # Distinto de «los SID han llegado y la integrada no está entre ellos»:
        # aquí no se ha podido identificar a nadie, así que no se afirma nada.
        for cuentas in ([{"disponible": True, "Name": "A", "Enabled": True}],
                        [self.cuenta("A", "")],
                        [self.cuenta("A", None)]):
            with self.subTest(cuentas=cuentas):
                with self.assertRaises(SinDato):
                    self._auditar(cuentas)

    def test_en_un_windows_sin_el_cmdlet_no_aplica(self):
        with self.assertRaises(NoAplica):
            self._auditar([{"disponible": False}])

    def test_comparte_la_consulta_con_las_cuentas_sin_contrasena(self):
        # Coste marginal cero: es la misma respuesta, pedida una sola vez.
        llamadas = []

        def contar(*a_, **k):
            llamadas.append(1)
            return PSResult([self.cuenta("Administrador", "S-1-5-21-1-2-3-500",
                                         habilitada=False)])

        a = Auditor(SystemInfo(), None)
        with patched(audit.seguridad, ps_json=contar):
            a.check_local_accounts()
            a.check_cuenta_administrador()
        self.assertEqual(len(llamadas), 1, "se ha lanzado Get-LocalUser dos veces")


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
        with patched(audit, elevado={"bitlocker": filas}):
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

    @unittest.skipUnless(IS_WINDOWS, "%SystemDrive% solo existe en Windows")
    def test_una_variable_de_entorno_manipulada_no_silencia_la_comprobacion(self):
        # El disco real es el que diga la API; BitLocker informa de ese. Si la
        # unidad saliera de `%SystemDrive%`, apuntarla a otra letra dejaría la
        # comprobación sin encontrar el volumen y se declararía «sin dato»:
        # el cifrado dejaría de comprobarse sin que el informe lo dijera.
        entorno = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(entorno)))
        real = _system_drive()
        os.environ["SystemDrive"] = "D:" if not real.startswith("D") else "E:"

        si = SystemInfo()
        si.system_drive = _system_drive()
        a = Auditor(si, None)
        with patched(audit, elevado={"bitlocker": PSResult(
                [self.volumen(mount=real.rstrip("\\"), proteccion=0, estado=0)])}):
            a.check_disk_encryption()
        self.assertEqual([f.id for f in a.findings], ["sin_cifrado"])

    def test_en_windows_home_no_aplica(self):
        # El cmdlet no existe. No es un dato que falte: es una pregunta que no
        # procede, y no puede contar como comprobación pendiente.
        with self.assertRaises(NoAplica):
            self._auditar(PSResult([{"disponible": False}]))

    def _con_volumenes(self, filas, discos):
        si = SystemInfo()
        si.system_drive = "C:"
        si.disks = discos
        a = Auditor(si, None)
        with patched(audit.seguridad, elevado={"bitlocker": PSResult(filas)}):
            return a, a.check_disk_encryption()

    def volumen_local(self, mount, kind="local"):
        return {"mount": mount, "kind": kind, "ignored": kind != "local"}

    def test_un_disco_de_datos_sin_cifrar_se_avisa_aparte(self):
        # Cifrar el sistema y dar el trabajo por hecho es el caso frecuente: el
        # segundo disco, donde suelen estar las fotos, se queda como estaba.
        a, resumen = self._con_volumenes(
            [self.volumen("C:"), self.volumen("D:", proteccion=0, estado=0)],
            [self.volumen_local("C:\\"), self.volumen_local("D:\\")])
        self.assertEqual([f.id for f in a.findings], ["sin_cifrado_datos"])
        f = a.findings[0]
        self.assertEqual((f.severity, f.category, f.gain), ("medium", SEGURIDAD, 0.0))
        self.assertIn("D:", f.title)
        self.assertIn("sin cifrar", resumen)

    def test_los_extraibles_no_cuentan(self):
        # Un USB sin cifrar es lo normal. Avisar de cada uno convertiría esto en
        # ruido que se aprende a ignorar, y con él el aviso del disco de sistema.
        a, _ = self._con_volumenes(
            [self.volumen("C:"), self.volumen("E:", proteccion=0, estado=0)],
            [self.volumen_local("C:\\"), self.volumen_local("E:\\", kind="removable")])
        self.assertEqual(a.findings, [])

    def test_los_volumenes_de_datos_cifrados_no_generan_nada(self):
        a, resumen = self._con_volumenes(
            [self.volumen("C:"), self.volumen("D:")],
            [self.volumen_local("C:\\"), self.volumen_local("D:\\")])
        self.assertEqual(a.findings, [])
        self.assertNotIn("sin cifrar", resumen)

    def test_el_sistema_sin_cifrar_sigue_siendo_lo_grave(self):
        # Las dos cosas a la vez: dos hallazgos, y el del sistema con más peso.
        a, _ = self._con_volumenes(
            [self.volumen("C:", proteccion=0, estado=0),
             self.volumen("D:", proteccion=0, estado=0)],
            [self.volumen_local("C:\\"), self.volumen_local("D:\\")])
        por_id = {f.id: f for f in a.findings}
        self.assertEqual(set(por_id), {"sin_cifrado", "sin_cifrado_datos"})
        self.assertEqual(por_id["sin_cifrado"].severity, "high")
        self.assertEqual(por_id["sin_cifrado_datos"].severity, "medium")

    def test_un_estado_ilegible_en_un_volumen_de_datos_no_acusa(self):
        a, _ = self._con_volumenes(
            [self.volumen("C:"), {"disponible": True, "MountPoint": "D:",
                                  "VolumeStatus": "?", "ProtectionStatus": "?"}],
            [self.volumen_local("C:\\"), self.volumen_local("D:\\")])
        self.assertEqual(a.findings, [])

    def test_no_cuesta_una_consulta_nueva(self):
        # La condición del informe: sale de la misma respuesta que ya llegaba.
        fuente = inspect.getsource(Auditor._volumenes_de_datos_sin_cifrar)
        self.assertNotIn("ps_json", fuente)
        self.assertNotIn("recoger", fuente)

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


class ArranqueSeguro(unittest.TestCase):
    def _auditar(self, filas):
        a = Auditor(SystemInfo(), None)
        with patched(audit, elevado={"secureboot": filas}):
            return a, a.check_secure_boot()

    def test_activo(self):
        a, resumen = self._auditar(PSResult([{"firmware": "UEFI", "activo": True}]))
        self.assertEqual(a.findings, [])
        self.assertEqual(resumen, "activo")

    def test_desactivado(self):
        a, _ = self._auditar(PSResult([{"firmware": "UEFI", "activo": False}]))
        self.assertEqual([f.id for f in a.findings], ["secureboot_off"])
        f = a.findings[0]
        self.assertEqual((f.severity, f.category, f.gain), ("medium", SEGURIDAD, 0.0))

    def test_explica_lo_de_windows_11(self):
        a, _ = self._auditar(PSResult([{"firmware": "UEFI", "activo": False}]))
        self.assertIn("Windows 11", a.findings[0].detail)

    def test_con_bios_heredada_no_aplica(self):
        # No tiene arranque seguro que activar: no es un dato que falte.
        with self.assertRaises(NoAplica):
            self._auditar(PSResult([{"firmware": "Legacy", "activo": None}]))

    def test_sin_privilegios_es_sin_dato(self):
        # Verificado en un equipo real: sin administrador el cmdlet contesta
        # «No se pudieron establecer privilegios adecuados» y aquí llega None.
        # Eso no es «desactivado».
        with self.assertRaises(SinDato):
            self._auditar(PSResult([{"firmware": "UEFI", "activo": None}]))


class ChipTpm(unittest.TestCase):
    def _auditar(self, filas):
        a = Auditor(SystemInfo(), None)
        with patched(audit, elevado={"tpm": filas}):
            return a, a.check_tpm()

    def fila(self, **campos) -> dict:
        base = {"disponible": True, "TpmPresent": True, "TpmReady": True, "TpmEnabled": True}
        base.update(campos)
        return base

    def test_presente_y_activo(self):
        a, resumen = self._auditar(PSResult([self.fila()]))
        self.assertEqual(a.findings, [])
        self.assertIn("activo", resumen)

    def test_sin_chip(self):
        a, _ = self._auditar(PSResult([self.fila(TpmPresent=False, TpmEnabled=False)]))
        self.assertEqual([f.id for f in a.findings], ["sin_tpm"])
        self.assertEqual(a.findings[0].gain, 0.0)

    def test_presente_pero_apagado(self):
        a, _ = self._auditar(PSResult([self.fila(TpmEnabled=False)]))
        self.assertEqual([f.id for f in a.findings], ["tpm_desactivado"])

    def test_menciona_los_nombres_de_fabricante(self):
        # Casi siempre está y solo hay que encenderlo, pero se llama de otra cosa.
        a, _ = self._auditar(PSResult([self.fila(TpmPresent=False)]))
        detalle = a.findings[0].detail
        self.assertIn("fTPM", detalle)
        self.assertIn("PTT", detalle)

    def test_sin_privilegios_no_se_inventa_que_no_hay_chip(self):
        # Verificado en un equipo real: sin administrador `Get-Tpm` NO falla,
        # devuelve los campos a null. Darlos por «no hay TPM» habría acusado de
        # faltarle el chip a casi cualquier equipo.
        with self.assertRaises(SinDato):
            self._auditar(PSResult([self.fila(TpmPresent=None, TpmReady=None,
                                              TpmEnabled=None)]))

    def test_sin_el_cmdlet_no_aplica(self):
        with self.assertRaises(NoAplica):
            self._auditar(PSResult([{"disponible": False}]))


class ProtocoloSmb1(unittest.TestCase):
    def _auditar(self, filas):
        a = Auditor(SystemInfo(), None)
        with patched(audit, elevado={"smb1": filas}):
            return a, a.check_smb1()

    def test_desactivado(self):
        for estado in ("Disabled", "disabled", 2, "DisabledWithPayloadRemoved"):
            with self.subTest(estado=estado):
                a, resumen = self._auditar(
                    PSResult([{"disponible": True, "State": estado}]))
                self.assertEqual(a.findings, [])

    def test_activo(self):
        for estado in ("Enabled", 1):
            with self.subTest(estado=estado):
                a, _ = self._auditar(PSResult([{"disponible": True, "State": estado}]))
                self.assertEqual([f.id for f in a.findings], ["smb1_activo"])
                self.assertEqual(a.findings[0].severity, "high")
                self.assertEqual(a.findings[0].gain, 0.0)

    def test_avisa_de_lo_que_puede_dejar_de_funcionar(self):
        # Quitarlo puede tirar un NAS viejo, y decirlo antes evita el susto.
        a, _ = self._auditar(PSResult([{"disponible": True, "State": "Enabled"}]))
        pasos = " ".join(a.findings[0].steps)
        self.assertIn("NAS", pasos)

    def test_un_estado_desconocido_no_lo_da_por_desactivado(self):
        # Dar por apagado un SMB1 encendido es el único error que importa aquí.
        with self.assertRaises(SinDato):
            self._auditar(PSResult([{"disponible": True, "State": "Vete a saber"}]))

    def test_sin_privilegios_es_sin_dato(self):
        # Verificado en un equipo real: «La operación solicitada requiere elevación».
        with self.assertRaises(SinDato):
            self._auditar(PSResult((), ok=False, error="requiere elevación"))


class CuentasLocales(unittest.TestCase):
    """El filtro por `Enabled` no es un detalle: es lo que evita el falso positivo.

    Un Windows recién instalado trae `Invitado`, `DefaultAccount` y
    `WDAGUtilityAccount` sin exigir contraseña y deshabilitadas. Contarlas
    convertiría cualquier equipo del mundo en tres hallazgos graves.
    """

    # Salida literal de `Get-LocalUser` en un Windows 11 en español.
    DE_FABRICA = [
        {"disponible": True, "Name": "Administrador", "Enabled": False, "PasswordRequired": True},
        {"disponible": True, "Name": "DefaultAccount", "Enabled": False, "PasswordRequired": False},
        {"disponible": True, "Name": "Invitado", "Enabled": False, "PasswordRequired": False},
        {"disponible": True, "Name": "WDAGUtilityAccount", "Enabled": False,
         "PasswordRequired": True},
    ]

    def _auditar(self, filas):
        a = Auditor(SystemInfo(), None)
        with patched(audit, wmi=PSResult(filas)):
            return a, a.check_local_accounts()

    def test_un_equipo_normal_no_da_hallazgos(self):
        usuario = {"disponible": True, "Name": "Cristian", "Enabled": True,
                   "PasswordRequired": True}
        a, resumen = self._auditar(self.DE_FABRICA + [usuario])
        self.assertEqual(a.findings, [], "las cuentas de fábrica no cuentan")
        self.assertIn("todas con contraseña", resumen)

    def test_una_cuenta_activa_sin_contraseña(self):
        abierta = {"disponible": True, "Name": "Taller", "Enabled": True,
                   "PasswordRequired": False}
        a, _ = self._auditar(self.DE_FABRICA + [abierta])
        self.assertEqual([f.id for f in a.findings], ["cuenta_sin_clave"])
        f = a.findings[0]
        self.assertIn("Taller", f.title)
        self.assertEqual((f.severity, f.category, f.gain), ("high", SEGURIDAD, 0.0))

    def test_no_delata_las_de_fabrica_en_el_titulo(self):
        abierta = {"disponible": True, "Name": "Taller", "Enabled": True,
                   "PasswordRequired": False}
        a, _ = self._auditar(self.DE_FABRICA + [abierta])
        for nombre in ("Invitado", "DefaultAccount"):
            self.assertNotIn(nombre, a.findings[0].title)

    def test_varias_cuentas_abiertas(self):
        abiertas = [{"disponible": True, "Name": n, "Enabled": True,
                     "PasswordRequired": False} for n in ("Taller", "Caja")]
        a, resumen = self._auditar(self.DE_FABRICA + abiertas)
        self.assertIn("Taller", a.findings[0].title)
        self.assertIn("Caja", a.findings[0].title)
        self.assertIn("2", resumen)

    def test_aclara_que_no_va_de_pin_ni_cuenta_de_microsoft(self):
        abierta = {"disponible": True, "Name": "Taller", "Enabled": True,
                   "PasswordRequired": False}
        a, _ = self._auditar([abierta])
        self.assertIn("PIN", a.findings[0].detail)

    def test_sin_el_cmdlet_no_aplica(self):
        with self.assertRaises(NoAplica):
            self._auditar([{"disponible": False}])


class ActualizacionesDeSeguridad(unittest.TestCase):
    """Detrás de un flag, igual que `--check-drivers` y por el mismo motivo."""

    def _auditar(self, filas):
        a = Auditor(SystemInfo(), None, check_updates=True)
        with patched(audit, pending_security_updates=lambda *a, **k: filas):
            return a, a.check_security_updates()

    def fila(self, titulo="KB5000001", severidad="Critical") -> dict:
        return {"Title": titulo, "MsrcSeverity": severidad}

    def test_sin_actualizaciones_pendientes(self):
        a, resumen = self._auditar(PSResult([]))
        self.assertEqual(a.findings, [])
        self.assertIn("ninguna de seguridad", resumen)

    def test_una_actualizacion_que_no_es_de_seguridad_no_cuenta(self):
        # Las que no son de seguridad vienen sin MsrcSeverity. Contarlas
        # inflaría el hallazgo con una actualización de zona horaria.
        a, resumen = self._auditar(PSResult([self.fila("Zona horaria", None),
                                             self.fila("Otra", "")]))
        self.assertEqual(a.findings, [])
        self.assertIn("ninguna de seguridad", resumen)

    def test_criticas(self):
        a, _ = self._auditar(PSResult([self.fila(severidad="Critical")]))
        self.assertEqual([f.id for f in a.findings], ["updates_pendientes"])
        f = a.findings[0]
        self.assertEqual((f.severity, f.category, f.gain), ("high", SEGURIDAD, 0.0))

    def test_solo_importantes_baja_de_severidad(self):
        a, _ = self._auditar(PSResult([self.fila(severidad="Important")]))
        self.assertEqual(a.findings[0].severity, "medium")

    def test_solo_menores(self):
        a, _ = self._auditar(PSResult([self.fila(severidad="Moderate")]))
        self.assertEqual(a.findings[0].severity, "low")

    def test_la_mezcla_manda_la_mas_grave(self):
        a, _ = self._auditar(PSResult([self.fila("A", "Moderate"),
                                       self.fila("B", "Critical"),
                                       self.fila("C", "Important")]))
        self.assertEqual(a.findings[0].severity, "high")
        self.assertIn("3 actualización(es)", a.findings[0].title)

    def test_dice_cuales_son(self):
        a, _ = self._auditar(PSResult([self.fila("KB5000123 para Windows 11")]))
        self.assertIn("KB5000123", a.findings[0].detail)

    def test_no_poder_preguntar_no_es_estar_al_dia(self):
        with self.assertRaises(SinDato):
            self._auditar(PSResult((), ok=False, error="sin conexión"))

    def test_sin_el_flag_ni_se_pregunta(self):
        # Registrarla sin haberla pedido la dejaría como «sin comprobar» en un
        # informe donde nadie ha querido esperar los 30 segundos.
        sin_flag = Auditor(SystemInfo(), None)
        con_flag = Auditor(SystemInfo(), None, check_updates=True)
        self.assertFalse(sin_flag.check_updates)
        self.assertTrue(con_flag.check_updates)


class LoQueLasSalidasNoEscapan(unittest.TestCase):
    """Los cuatro campos que se interpolan crudos, y el que no puede validarse.

    `f.severity` y `f.id` van al HTML sin pasar por `_e()`; `f.effort` y
    `f.risk` van al `.ps1` dentro de unas comillas dobles de PowerShell, en un
    fichero que se ejecuta como Administrador por diseño. Los cuatro salen hoy
    de constantes escritas en `audit.py`, pero era una invariante que nadie
    había declarado: validarla en `add()` la convierte en garantía, y vale para
    las cuatro salidas de una vez.

    `gain_note` no entra en ese trato porque es prosa libre y no hay conjunto
    cerrado con el que validarla. Esa se escapa en su destino, y aquí se
    comprueba que no puede partir el script.
    """

    def _añadir(self, **cambios):
        a = Auditor(SystemInfo(), None)
        campos = dict(id="prueba", title="T", severity="high", category="fluidez",
                      component="system", detail="d", gain=0.0, gain_note="n",
                      effort="bajo", risk="nulo", steps=[])
        campos.update(cambios)
        a.add(**campos)
        return a

    def test_un_hallazgo_normal_pasa(self):
        self.assertEqual(len(self._añadir().findings), 1)

    def test_todas_las_severidades_declaradas_valen(self):
        for severidad in SEVERITY_ORDER:
            with self.subTest(severidad=severidad):
                self._añadir(severity=severidad)

    def test_una_severidad_inventada_no_pasa(self):
        for severidad in ("grave", "HIGH", "", None, 1, "high "):
            with self.subTest(severidad=severidad):
                with self.assertRaises(ValueError):
                    self._añadir(severity=severidad)

    def test_todos_los_esfuerzos_y_riesgos_declarados_valen(self):
        for esfuerzo in _ESFUERZOS:
            with self.subTest(effort=esfuerzo):
                self._añadir(effort=esfuerzo)
        for riesgo in _RIESGOS:
            with self.subTest(risk=riesgo):
                self._añadir(risk=riesgo)

    def test_un_esfuerzo_o_un_riesgo_inventados_no_pasan(self):
        hostiles = ('" -Accion { Remove-Item C:\\ }', "$(whoami)", "BAJO", "",
                    None, 1, "bajo ", "ninguno")
        for valor in hostiles:
            with self.subTest(effort=valor):
                with self.assertRaises(ValueError):
                    self._añadir(effort=valor)
            with self.subTest(risk=valor):
                with self.assertRaises(ValueError):
                    self._añadir(risk=valor)

    def test_nulo_solo_vale_para_el_riesgo(self):
        # Los dos conjuntos no son el mismo: un esfuerzo «nulo» no existe —algo
        # habrá que hacer— y confundirlos delataría una copia sin leer.
        self._añadir(risk="nulo")
        with self.assertRaises(ValueError):
            self._añadir(effort="nulo")

    def test_la_nota_de_ganancia_no_puede_partir_el_script(self):
        # La que no se puede validar en origen. Cae dentro de las comillas
        # dobles de `-Impacto`, donde una comilla cierra la cadena y lo que
        # venga detrás lo parsea PowerShell como más argumentos de `Bloque`.
        a = Auditor(SystemInfo(), None)
        a.add(id="trim_off", title="T", severity="high", category="almacenamiento",
              component="disk", detail="d", gain=0.1,
              gain_note='fluidez" -Accion { Remove-Item C:\\ } #',
              effort="bajo", risk="nulo", steps=[])
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "plan.ps1"
            export_plan(destino, SystemInfo(), None, a)
            script = destino.read_text(encoding="utf-8")
        self.assertNotIn('fluidez" -Accion', script)
        self.assertIn('fluidez`" -Accion', script)

    def test_la_nota_de_ganancia_no_puede_inyectar_una_variable(self):
        a = Auditor(SystemInfo(), None)
        a.add(id="trim_off", title="T", severity="high", category="almacenamiento",
              component="disk", detail="d", gain=0.1, gain_note="coste $(whoami)",
              effort="bajo", risk="nulo", steps=[])
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "plan.ps1"
            export_plan(destino, SystemInfo(), None, a)
            script = destino.read_text(encoding="utf-8")
        self.assertNotIn("coste $(whoami)", script)
        self.assertIn("coste `$(whoami)", script)

    def test_un_id_con_html_no_pasa(self):
        # El caso que esto viene a impedir: un `id` construido con el nombre de
        # un disco, que lo decide el firmware del dispositivo y no nosotros.
        hostiles = ['"><script>alert(1)</script>', "SanDisk Ultra", "id con espacios",
                    "id-con-guion", "Mayúsculas", "", None, 42, "acentuado_ñ"]
        for identificador in hostiles:
            with self.subTest(id=identificador):
                with self.assertRaises(ValueError):
                    self._añadir(id=identificador)

    def test_el_mensaje_dice_que_hay_que_arreglar(self):
        with self.assertRaises(ValueError) as caso:
            self._añadir(id="Disco SanDisk")
        self.assertIn("a-z0-9_", str(caso.exception))

    def test_el_html_escapa_la_severidad_aunque_add_no_la_haya_visto(self):
        """`add()` no es la única puerta por la que entra un `Finding`.

        Varios módulos y varios tests construyen `Finding(...)` directamente,
        sin pasar por la validación. Mientras el HTML interpolaba `f.severity`
        crudo, la garantía del escapado dependía de que nadie lo hiciera nunca
        —y ya lo hacen—. Ahora el HTML escapa en los cinco sitios, así que la
        validación de `add()` cubre el ancla `#h-{id}`, que no se puede escapar,
        y no tiene que cubrir además esto.
        """
        hostil = Finding(
            id="hostil", title="T", severity='high"><script>alert(1)</script>',
            category="fluidez", component="disk", detail="d", gain=0.1,
            gain_note="n", effort="bajo", risk="nulo", steps=["un paso"])
        a = Auditor(SystemInfo(), None)
        a.findings = [hostil]
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "informe.html"
            export_html(destino, SystemInfo(), None, a,
                        project_improvement(None, a.findings))
            html = destino.read_text(encoding="utf-8")
        # El informe embebe JS propio, así que `<script>` aparece de forma
        # legítima: lo que no puede aparecer es el que venía en el hallazgo.
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_una_severidad_no_declarada_no_tira_el_informe(self):
        # Antes ni se llegaba al escapado: el recuento de hallazgos indexaba
        # `SEVERITY_ORDER[...]` y saltaba un KeyError con el informe a medias.
        # Ordena la última, que es lo que significa «no la conozco».
        hostil = Finding(id="hostil", title="T", severity="inventada",
                         category="fluidez", component="disk", detail="d",
                         gain=0.1, gain_note="n", effort="bajo", risk="nulo",
                         steps=["un paso"])
        a = Auditor(SystemInfo(), None)
        a.findings = [hostil]
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "informe.html"
            export_html(destino, SystemInfo(), None, a,
                        project_improvement(None, a.findings))
            self.assertIn("inventada", destino.read_text(encoding="utf-8"))
        # Y la consola, que tenía el mismo recuento con la misma indexación.
        from quilate.report import print_report
        salida = io.StringIO()
        with redirect_stdout(salida):
            print_report(SystemInfo(), None, a, project_improvement(None, a.findings))
        self.assertIn("INVENTADA", salida.getvalue())

    def test_todos_los_hallazgos_del_auditor_cumplen(self):
        # Sobre el código, no sobre lo que los tests ejerciten: una comprobación
        # que solo salta en un equipo concreto no puede reventar allí.
        fuente = fuente_completa(audit)
        for identificador in re.findall(r'id="([^"]+)"', fuente):
            with self.subTest(id=identificador):
                self.assertRegex(identificador, r"^[a-z0-9_]+$")
        for severidad in set(re.findall(r'severity="([^"]+)"', fuente)):
            with self.subTest(severidad=severidad):
                self.assertIn(severidad, SEVERITY_ORDER)
        for esfuerzo in set(re.findall(r'effort="([^"]+)"', fuente)):
            with self.subTest(esfuerzo=esfuerzo):
                self.assertIn(esfuerzo, _ESFUERZOS)
        for riesgo in set(re.findall(r'risk="([^"]+)"', fuente)):
            with self.subTest(riesgo=riesgo):
                self.assertIn(riesgo, _RIESGOS)


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
