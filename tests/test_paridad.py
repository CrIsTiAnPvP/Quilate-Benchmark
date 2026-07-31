"""Nada de lo que se mide puede quedarse en un solo informe.

Quilate saca cuatro vistas de la misma ejecución —consola, HTML, JSON y plan
PowerShell— y el modo natural de que se desincronicen es añadir un dato nuevo,
enseñarlo donde se estaba trabajando y olvidarse del resto. Ha pasado ya: la
GPU entró en la puntuación global y la ficha de la gráfica siguió diciendo «sin
nota sintética» durante toda una versión, y la carga ajena que explica un margen
alto solo existía dentro del JSON.

Este test no comprueba que exista una clave: planta un valor reconocible en cada
fuente de datos y comprueba que sale por el otro lado. La diferencia importa,
porque una clave presente con la lista vacía es exactamente el fallo que se
quiere cazar.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from quilate.audit import Auditor
from quilate.benchmark import Benchmark
from quilate.const import WEBSITE_URL
from quilate.components import build_component_cards
from quilate.export.html_export import Seccion, export_html
from quilate.export.json_export import build_payload
from quilate.export.plan_export import export_plan
from quilate.projection import project_improvement
from quilate.storage_scan import ScanResult
from quilate.sysinfo import SystemInfo

# Cada testigo es único e improbable: si aparece en la salida, solo puede venir
# del sitio donde se plantó.
T = {
    "host": "EQUIPO-TESTIGO",
    "cpu": "Testigo Core X-9999",
    "gpu_dev": "Testigo Graphics 4096",
    "gpu_motivo": "sin runtime OpenCL instalado",
    "proceso": "programa-testigo.exe",
    "ambiente": "acaparador-testigo.exe",
    "inicio": "arranca-testigo.exe",
    "adaptador": "Testigo Wireless AX9999",
    "fichero": "volcado-testigo.dmp",
    "hallazgo": "Hallazgo testigo con margen",
    "sin_datos": "no se pudo leer el testigo",
}


def _sistema() -> SystemInfo:
    si = SystemInfo()
    si.hostname = T["host"]
    si.os_name, si.os_build = "Windows 11 Pro", "10.0.26200"
    si.cpu_name, si.cpu_cores, si.cpu_threads = T["cpu"], 6, 12
    si.cpu_base_mhz = 3500.0
    si.ram_total, si.ram_available = 32 * 1024**3, 18 * 1024**3
    si.ram_speed_mhz, si.ram_speed_rated_mhz, si.ram_channels = 2400, 3200, 2
    si.ram_sticks = [{"slot": "DIMM0", "capacity": 16 * 1024**3, "speed": 2400,
                      "rated_speed": 3200, "vendor": "Testigo", "part": "TG-16"}]
    si.gpus = [{"name": T["gpu_dev"], "driver": "999.99", "driver_date": "2026-01-01",
                "vram": 12 * 1024**3, "temperature": 61.0, "utilization": 4.0}]
    si.system_drive, si.system_drive_media = "C:", "SSD"
    si.physical_disks = [{"name": "Testigo NVMe 1TB", "media": "SSD", "bus": "NVMe",
                          "size": 1024**4, "health": "Healthy", "wear": 3,
                          "power_on_hours": 4200, "temperature": 41}]
    si.disks = [{"mount": "C:", "label": "Sistema", "fstype": "NTFS", "kind": "fixed",
                 "total": 1024**4, "free": 200 * 1024**3, "percent": 80.0,
                 "ignored": False, "physical": {"media": "SSD", "bus": "NVMe"}}]
    si.python_version, si.is_admin = "3.13.0", True
    return si


def _benchmark() -> Benchmark:
    b = Benchmark(quick=True, skip_disk=True, skip_gpu=True)
    b._register("cpu_single", "CPU monohilo", "pts", 118.0, 118.0, "media geométrica")
    b._register("cpu_multi", "CPU multihilo", "t/s", 40.0, 117.0)
    b._register("memory", "Memoria", "GB/s", 11.5, 115.0)
    b._register("disk_write", "Disco · escritura", "MB/s", 900.0, 105.0)
    b._register("disk_read", "Disco · lectura", "MB/s", 1800.0, 105.0)
    b._register("disk_iops", "Disco · IOPS 4K", "IOPS", 24000.0, 109.0)
    b._register("gpu_compute", "GPU · cómputo FP32", "GFLOPS", 10000.0, 111.0,
                f"{T['gpu_dev']} · 28 unidades de cómputo")
    b._register("gpu_vram", "GPU · ancho de banda VRAM", "GB/s", 320.0, 106.0)
    b._register("gpu_pcie", "GPU · transferencia PCIe", "GB/s", 12.0, 109.0)
    b._spread("memory", "Ancho de banda de memoria", [11.5, 9.0])
    b.memory_hierarchy = [{"level": "L1/L2", "size": 16384, "gbs": 73.5},
                          {"level": "RAM", "size": 64 * 1024**2, "gbs": 21.1}]
    b._metric("sustained", "Rendimiento sostenido", 93.0, "%", "último cuarto frente al primero")
    b.gpu_info = {"device": {"name": T["gpu_dev"], "compute_units": 28,
                             "clock_mhz": 1777, "vram": 12 * 1024**3,
                             "driver": "OpenCL 3.0 CUDA"},
                  "gflops": 10000.0, "vram_gbs": 320.0, "pcie_gbs": 12.0,
                  "compute_iters": 131072}
    b.ambient_load = {"antes": {"cpu_pct": 24.0, "top": [(T["ambiente"], 19.0)]},
                      "después": {"cpu_pct": 3.0, "top": []}}
    b.load_snapshots = [{"moment": "antes", "cpu_mhz": 4200.0, "cpu_mhz_source": "psutil",
                         "cpu_temp": 42.0, "gpu_temp": 38.0, "gpu_power_w": 20.0},
                        {"moment": "después", "cpu_mhz": 3600.0, "cpu_mhz_source": "psutil",
                         "cpu_temp": 88.0, "gpu_temp": 44.0, "gpu_power_w": 22.0}]
    b.scaling_efficiency = 82.0
    b.freq_under_load, b.freq_source = 3600.0, "psutil"
    b.thermal_samples = [88.0]
    return b


def _auditoria(si: SystemInfo, bench: Benchmark) -> Auditor:
    a = Auditor(si, bench)
    a.add(id="testigo_mejorable", title=T["hallazgo"], severity="high",
          category="fluidez", component="memory",
          detail="Hallazgo plantado por el test de paridad.",
          gain=0.12, gain_note="ancho de banda de memoria",
          effort="bajo", risk="nulo",
          steps=["Primer paso testigo", "Segundo paso testigo"])
    a.checks_run = 24
    a.checks_total = 26
    a.unverified = [("Testigo sin veredicto", T["sin_datos"])]
    a.not_applicable = [("Testigo inaplicable", "no hay disco mecánico")]
    a.notes = ["Nota testigo del auditor."]
    a.top_processes = [{"name": T["proceso"], "rss": 900 * 1024**2}]
    a.startup_items = [{"name": T["inicio"], "location": "HKCU\\Run", "enabled": True}]
    a.boot_seconds = 31.0
    a.boot_report = {"boots": [{"fields": {}}],
                     "delays": [{"fields": {"Name": T["inicio"], "TotalTime": 8200},
                                 "kind": "aplicación"}]}
    adaptador = {"name": "Wi-Fi", "description": T["adaptador"], "status": "Up",
                 "link_mbps": 866.7, "media": "Native 802.11", "wireless": True}
    a.network = {
        "active": True, "adapters": [adaptador], "connected": [adaptador],
        "wifi": {"radio": "802.11ac", "rate_mbps": 866.7, "band_ghz": "5",
                 "channel": 40, "rssi_dbm": -52, "signal_pct": 96},
        "latency": {"reachable": True, "best_ms": 9.9,
                    "targets": [{"name": "Cloudflare", "host": "1.1.1.1",
                                 "median_ms": 9.9, "jitter_ms": 1.2, "loss_pct": 0}]},
        "dns": {"median_ms": 28.0, "failures": 0, "queried": 3},
    }
    a.scan = ScanResult(
        roots=["C:\\"], min_size=128 * 1024**2,
        files=[{"path": f"C:\\temp\\{T['fichero']}", "size": 3 * 1024**3,
                "category": "volcado", "age_days": 210}],
        by_category={"volcado": {"size": 3 * 1024**3, "count": 1,
                                 "files": [{"path": f"C:\\temp\\{T['fichero']}",
                                            "size": 3 * 1024**3, "age_days": 210}]}},
        special=[{"name": "pagefile.sys", "size": 8 * 1024**3,
                  "note": "archivo de paginación"}],
        total_large=3 * 1024**3, reclaimable=3 * 1024**3,
        scanned_files=90000, scanned_dirs=7000, elapsed=28.0)
    return a


class Paridad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.si = _sistema()
        cls.bench = _benchmark()
        cls.auditor = _auditoria(cls.si, cls.bench)
        cls.projection = project_improvement(cls.bench, cls.auditor.findings)
        cls.tmp = tempfile.TemporaryDirectory()
        destino = Path(cls.tmp.name)
        export_html(destino / "i.html", cls.si, cls.bench, cls.auditor, cls.projection)
        cls.html = (destino / "i.html").read_text(encoding="utf-8")
        export_plan(destino / "p.ps1", cls.si, cls.bench, cls.auditor)
        cls.plan = (destino / "p.ps1").read_text(encoding="utf-8-sig")
        cls.payload = build_payload(cls.si, cls.bench, cls.auditor, cls.projection)
        cls.json = json.dumps(cls.payload, ensure_ascii=False, default=str)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _en_todas(self, testigo: str, *, plan: bool = True):
        self.assertIn(testigo, self.html, f"«{testigo}» no llega al HTML")
        self.assertIn(testigo, self.json, f"«{testigo}» no llega al JSON")
        if plan:
            self.assertIn(testigo, self.plan, f"«{testigo}» no llega al plan")

    # --- la navegación no lleva separadores ---------------------------------
    def test_la_navegacion_son_solo_enlaces(self):
        # Se probaron tres formas de marcar dónde acaba el grupo que explica la
        # nota y empieza el que dice qué hacer con ella, y las tres se leían como
        # una marca suelta en mitad de la barra en cuanto envolvía. El orden de
        # las secciones ya agrupa; pintarlo sobraba.
        nav = self.html[self.html.index("<nav>"):self.html.index("</nav>")]
        self.assertNotIn("navsep", nav)
        self.assertNotIn("ini-grupo", nav)
        self.assertNotIn("<span", nav.replace('<span class="sev', ""))

    # --- lo que tiene que estar en las tres salidas -------------------------
    def test_inventario(self):
        self._en_todas(T["cpu"])

    def test_la_gpu_medida(self):
        # El caso que motivó este test: la gráfica puntuaba y su ficha decía
        # «sin nota sintética» porque el grupo de componente estaba vacío.
        self._en_todas(T["gpu_dev"])

    def test_el_hallazgo_y_sus_pasos(self):
        self._en_todas(T["hallazgo"])
        self.assertIn("Primer paso testigo", self.html)
        self.assertIn("Primer paso testigo", self.json)
        self.assertIn("Primer paso testigo", self.plan)

    def test_los_archivos_grandes(self):
        self._en_todas(T["fichero"])

    # --- lo que basta con que llegue al informe y a los datos ---------------
    def test_carga_ajena_con_nombre(self):
        # El porcentaje ya salía; quién lo consumía se quedaba en el JSON, y es
        # justo el dato que convierte un margen alto en algo accionable.
        self.assertIn(T["ambiente"], self.html)
        self.assertIn(T["ambiente"], self.json)

    def test_sensores_antes_y_despues(self):
        self.assertIn("88", self.html)          # la temperatura al final de la carga
        self.assertIn("load_snapshots", self.json)
        self.assertIn("88.0", self.json)

    def test_procesos_y_programas_de_inicio(self):
        for testigo in (T["proceso"], T["inicio"]):
            self.assertIn(testigo, self.html)
            self.assertIn(testigo, self.json)

    def test_red(self):
        self.assertIn(T["adaptador"], self.html)
        self.assertIn(T["adaptador"], self.json)
        self.assertIn("9.9", self.html)         # latencia medida
        self.assertIn("28.0", self.json)        # mediana de DNS

    def test_cobertura(self):
        self.assertIn(T["sin_datos"], self.html)
        self.assertIn(T["sin_datos"], self.json)

    def test_el_veredicto_va_tambien_en_los_datos(self):
        veredicto = self.payload["verdict"]["summary"]
        self.assertTrue(veredicto)
        self.assertIn(veredicto[:40], self.html)

    def test_la_dispersion_y_la_escala_fechada(self):
        self.assertIn("dispersion", self.json)
        self.assertIn("reference_meta", self.json)
        self.assertIn("Escala de referencia", self.html)
        # El margen medido de memoria (11,5 frente a 9,0 GB/s) es grande y tiene
        # que verse: una cifra sin margen nunca delata que está contaminada.
        self.assertRegex(self.html, r"±\s*\d+%")

    def test_la_jerarquia_de_memoria(self):
        self.assertIn("73.5", self.json)
        self.assertIn("73.5", self.html)


class SinDatosOpcionales(unittest.TestCase):
    """La otra mitad: sin GPU, sin red y sin rastreo, el informe tiene que decir
    por qué faltan en vez de omitirlos en silencio."""

    def setUp(self):
        self.si = _sistema()
        self.bench = Benchmark(quick=True, skip_disk=True, skip_gpu=False)
        self.bench._register("cpu_single", "CPU monohilo", "pts", 100.0, 100.0)
        self.bench.gpu_unavailable = T["gpu_motivo"]
        self.auditor = Auditor(self.si, self.bench)
        self.auditor.network = {"active": False, "adapters": [], "connected": [], "wifi": {}}
        self.projection = project_improvement(self.bench, self.auditor.findings)

    def _html(self) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "i.html"
            export_html(destino, self.si, self.bench, self.auditor, self.projection)
            return destino.read_text(encoding="utf-8")

    def test_se_explica_por_que_no_hay_gpu(self):
        html = self._html()
        self.assertIn(T["gpu_motivo"], html)
        # Y la ficha de la gráfica no puede quedarse muda.
        ficha = build_component_cards(self.si, self.bench, self.auditor)
        gpu = next(c for c in ficha if c.key == "gpu")
        self.assertTrue(any(T["gpu_motivo"] in v for _, v in gpu.specs))

    def test_no_medir_la_red_no_se_confunde_con_medirla_bien(self):
        html = self._html()
        self.assertIn("--no-net", html)
        self.assertNotIn("--net<", html)      # el flag viejo ya no existe

    def test_sin_gpu_la_ficha_dice_que_es_medible(self):
        ficha = build_component_cards(self.si, self.bench, self.auditor)
        gpu = next(c for c in ficha if c.key == "gpu")
        # `measurable` distingue «no medido esta vez» de «no tiene nota posible»:
        # ahora la GPU sí puntúa, así que su ausencia es un hueco, no un vacío.
        self.assertTrue(gpu.measurable)
        self.assertIsNone(gpu.score)


class Marca(unittest.TestCase):
    """El isotipo va incrustado: ni .ico al lado, ni petición a ningún servidor."""

    # Atributos entre comillas dobles Y simples, y las `url()` del CSS. Las
    # comillas simples y el `url(` faltaban: hoy el grano del fondo es un `data:`
    # incrustado, pero nada impedía que el siguiente no lo fuera y la criba no
    # se habría enterado.
    RECURSOS = re.compile(r"""(?:href|src)\s*=\s*"([^"]*)"|"""
                          r"""(?:href|src)\s*=\s*'([^']*)'|"""
                          r"""url\(\s*['"]?([^'")]*)""", re.I)

    def _recursos_externos(self, html: str) -> list[str]:
        """Todo lo que el documento cargaría solo, si no fuera autocontenido."""
        # Fuera el script: dentro hay cadenas que construyen URLs en tiempo de
        # ejecución y no son enlaces del documento.
        marcado = re.sub(r"(?s)<script>.*?</script>", "", html)
        fuera = []
        for grupos in self.RECURSOS.findall(marcado):
            objetivo = next((g for g in grupos if g), "").strip()
            # Ancla interna, recurso incrustado o la web del autor —un enlace
            # que el lector pulsa, no algo que el documento cargue solo—.
            #
            # `%23` es una almohadilla codificada: dentro de un `data:image/svg+xml`
            # hay que escaparla, así que el degradado del logo y el filtro del
            # grano se referencian como `url(%23g)`. Apuntan a algo definido en
            # el mismo SVG y no salen del documento.
            if (not objetivo or objetivo.startswith(("#", "%23", "data:"))
                    or objetivo == WEBSITE_URL):
                continue
            fuera.append(objetivo)
        return fuera

    def test_el_informe_no_pide_nada_a_ninguna_parte(self):
        from quilate.export.html_export import export_html
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "i.html"
            si = _sistema()
            export_html(destino, si, None, Auditor(si, None), {})
            html = destino.read_text(encoding="utf-8")
        self.assertIn('rel="icon"', html, "el icono de pestaña no va incrustado")
        self.assertIn("data:image/svg+xml", html)
        self.assertEqual(self._recursos_externos(html), [],
                         "el informe deja de abrirse igual sin conexión")

    def test_tampoco_con_el_informe_lleno(self):
        """El informe vacío no ejercita ni la mitad de las secciones.

        `export_html(destino, si, None, Auditor(si, None), {})` no genera red, ni
        archivos grandes, ni benchmark, ni plan, ni hallazgos: un
        `<img src="https://…">` metido en cualquiera de esas pasaba la criba sin
        que nadie se enterara, porque esas secciones ni llegaban a escribirse.
        """
        from quilate.export.html_export import export_html
        si = _sistema()
        bench = _benchmark()
        auditor = _auditoria(si, bench)
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "i.html"
            export_html(destino, si, bench, auditor,
                        project_improvement(bench, auditor.findings))
            html = destino.read_text(encoding="utf-8")

        # Que las secciones que faltaban están de verdad en este informe: sin
        # esto, el test volvería a cribar la mitad del documento sin decirlo.
        for seccion in ('id="red"', 'id="archivos"', 'id="benchmark"',
                        'id="plan"', 'id="hallazgos"', 'id="proyeccion"'):
            self.assertIn(seccion, html, f"falta {seccion}: el informe sigue incompleto")

        self.assertEqual(self._recursos_externos(html), [],
                         "el informe deja de abrirse igual sin conexión")

    def test_la_criba_encuentra_lo_que_busca(self):
        # Un test que no falla nunca no protege de nada: aquí se comprueba que
        # la regex ve las tres formas, incluidas las dos que se le escapaban.
        muestras = {
            'comillas dobles': '<img src="https://ejemplo.com/a.png">',
            'comillas simples': "<img src='https://ejemplo.com/a.png'>",
            'url() de CSS': 'body{background-image:url("https://ejemplo.com/a.png")}',
            'url() sin comillas': "body{background:url(https://ejemplo.com/a.png)}",
        }
        for etiqueta, muestra in muestras.items():
            with self.subTest(forma=etiqueta):
                self.assertEqual(self._recursos_externos(muestra),
                                 ["https://ejemplo.com/a.png"])

    def test_la_mascara_del_anillo_declara_sus_unidades(self):
        # Sin `maskUnits`, la región de la máscara es la caja del círculo
        # ampliada un 10%, medida SIN el grosor del trazo: se queda por dentro
        # del borde exterior del anillo y lo achata arriba y a la izquierda.
        # Recortar una unidad de un círculo de radio 37 deja una cuerda plana de
        # diecisiete, así que el fallo no es sutil: el logo deja de ser redondo.
        from quilate.export.html_export import _favicon
        from quilate.export.html_export.piezas import _logo_defs
        for marcado in (_logo_defs(), _favicon()):
            self.assertIn("maskUnits", marcado)
            self.assertIn("userSpaceOnUse", marcado)

    def test_el_degradado_del_logo_se_declara_una_sola_vez(self):
        # Las cinco apariciones del isotipo llevaban cada una su propio
        # <linearGradient> y su propia <mask>, idénticos salvo el sufijo del id.
        # Ahora los declara el sprite y todas apuntan ahí.
        from quilate.export.html_export.piezas import _logo
        marcado = _logo()
        self.assertIn("url(#ql-oro)", marcado)
        self.assertNotIn("<defs", marcado)
        self.assertNotIn("linearGradient", marcado)

    def test_el_sprite_no_se_esconde_con_display_none(self):
        # Dentro de un elemento sin caja, Chromium no resuelve ni degradados ni
        # máscaras: los iconos de trazo salían igual porque no referencian nada,
        # pero el isotipo se quedaba invisible en las cinco apariciones. Con
        # tamaño cero sí se resuelven, y es lo único que sostiene que los
        # `<defs>` compartidos puedan vivir aquí.
        from quilate.export.html_export.piezas import _sprite
        marcado = _sprite()
        self.assertNotIn("display:none", marcado)
        self.assertIn("width:0", marcado)
        self.assertIn('id="ql-oro"', marcado)

    def test_el_extracto_exportado_se_lleva_el_degradado(self):
        # `buildDocument` clona el sprite, el <style>, la cabecera y el pie, y
        # nada más. Cabecera y pie llevan isotipo: si los `<defs>` vivieran
        # fuera del sprite, cada extracto descargado saldría con el logo sin
        # pintar y no habría forma de notarlo desde aquí.
        from quilate.export.html_export import HTML_JS
        self.assertIn("getElementById('sprite')", HTML_JS)


class JavaScriptDelInforme(unittest.TestCase):
    """El JS va incrustado en una cadena de Python y de ahí a un <script>.

    Son dos travesías donde se puede romper sin que nadie se entere: un error de
    sintaxis no da error al generar el fichero, da un informe mudo. Ha pasado —
    plegar secciones, buscar y exportar dejaron de responder a la vez— y desde
    fuera parecía un problema de diseño, no de una barra invertida.
    """

    def test_no_lleva_ni_una_barra_invertida(self):
        # Es la garantía barata: sin barras no hay escapes, y sin escapes la
        # cadena triple de Python no puede convertir un `\\n` del JavaScript en
        # un salto de línea real dentro de una cadena, que es justo lo que pasó.
        from quilate.export.html_export import HTML_JS
        self.assertNotIn("\\", HTML_JS,
                         "usa concatenación o entidades en vez de escapes")

    def test_ninguna_cadena_cierra_el_script_antes_de_tiempo(self):
        # El analizador de HTML corta el <script> en el primer «</script»,
        # aunque esté dentro de comillas.
        from quilate.export.html_export import HTML_JS
        self.assertNotIn("</script", HTML_JS.lower())

    def test_el_script_del_informe_es_un_solo_bloque(self):
        import re
        from quilate.export.html_export import Seccion, export_html
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "i.html"
            si = _sistema()
            export_html(destino, si, None, Auditor(si, None), {})
            html = destino.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"<script>", html)), 1)
        self.assertEqual(len(re.findall(r"</script>", html)), 1)

    def test_sintaxis_valida(self):
        """Con Node delante se comprueba de verdad; sin él, se dice y se salta."""
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("sin Node para analizar el JavaScript")
        from quilate.export.html_export import HTML_JS
        with tempfile.TemporaryDirectory() as tmp:
            fichero = Path(tmp) / "informe.js"
            fichero.write_text(HTML_JS, encoding="utf-8")
            hecho = subprocess.run([node, "--check", str(fichero)],
                                   capture_output=True, text=True)
        self.assertEqual(hecho.returncode, 0, hecho.stderr)


class SinEtiquetasHuerfanas(unittest.TestCase):
    """Cada componente que puntúa necesita nombre legible en todas las vistas."""

    def test_todo_lo_que_pesa_en_la_nota_tiene_etiqueta(self):
        from quilate.benchmark import WEIGHTS
        from quilate.console import COMPONENT_LABELS
        faltan = set(WEIGHTS) - set(COMPONENT_LABELS)
        self.assertEqual(faltan, set(),
                         "sin etiqueta, buscar el cuello de botella revienta con KeyError")

    def test_la_ficha_cubre_todos_los_componentes_puntuados(self):
        from quilate.benchmark import WEIGHTS
        from quilate.components import COMPONENT_GROUPS
        agrupados = {c for _k, _l, comps, _b in COMPONENT_GROUPS for c in comps}
        self.assertEqual(set(WEIGHTS) - agrupados, set(),
                         "un componente con nota que no aparece en ninguna ficha")


class RecuentoDeHallazgos(unittest.TestCase):
    """El informe da un único total de hallazgos, y no lo suma de trozos.

    `Seccion.findings` se rellena en cuatro sitios con cifras que se solapan
    —«componentes» recibe todos los hallazgos, «red» solo los suyos, «plan» los
    accionables y «hallazgos» todos otra vez—, así que enseñarlas juntas daría
    un total inflado. El comentario junto a la declaración lo explica; esto lo
    hace comprobable.
    """

    def _informe(self) -> tuple[Auditor, str]:
        si = _sistema()
        bench = _benchmark()
        auditor = _auditoria(si, bench)
        with tempfile.TemporaryDirectory() as d:
            destino = Path(d) / "informe.html"
            export_html(destino, si, bench, auditor,
                        project_improvement(bench, auditor.findings))
            return auditor, destino.read_text(encoding="utf-8")

    def test_el_total_se_da_una_sola_vez(self):
        auditor, html = self._informe()
        self.assertTrue(auditor.findings, "sin hallazgos esto no comprueba nada")
        total = (f'<div class="n">{len(auditor.findings)}</div>'
                 f'<div class="l">Hallazgos en')
        self.assertEqual(html.count(total), 1,
                         "el recuento total de hallazgos aparece más de una vez")

    def test_el_campo_muerto_sigue_sin_pintarse(self):
        # `Seccion.findings` guarda cuatro cifras que se solapan. Que ninguna
        # llegue al HTML es lo que impide que alguien las sume creyendo que el
        # resultado significa algo.
        self.assertIn("findings", Seccion.__dataclass_fields__)
        auditor, html = self._informe()
        campos = re.findall(r'data-findings|class="findings"', html)
        self.assertEqual(campos, [], "el recuento por sección ha llegado al HTML")

    def test_la_severidad_si_se_usa(self):
        # El otro campo opcional de Seccion sí se pinta: el punto de color de la
        # navegación. Documentar uno como muerto no puede sugerir que el otro
        # también lo esté.
        _, html = self._informe()
        self.assertIn('<span class="sev s-high"></span>', html)


if __name__ == "__main__":
    unittest.main()
