"""Auditoria del sistema: comprobaciones de configuracion y hallazgos."""

from __future__ import annotations

import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from .benchmark import Benchmark, read_cpu_temperature
from .console import C, human_bytes, section, spinner_done, spinner_step
from .const import IS_LINUX, IS_WINDOWS
from .platform_utils import (ps_json, reg_list_values, reg_read, run_cmd,
                             winreg)
from .sysinfo import SystemInfo


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SEVERITY_TEXT = {"critical": "CRÍTICO", "high": "ALTO", "medium": "MEDIO",
                 "low": "BAJO", "info": "INFO"}
SEVERITY_COLOR = {"critical": "RED", "high": "RED", "medium": "YELLOW",
                  "low": "CYAN", "info": "GREY"}


def sev_label(severity: str) -> str:
    """Se resuelve en tiempo de ejecución: si los colores están desactivados
    (--no-color o salida redirigida) no se cuelan códigos ANSI en el informe."""
    color = getattr(C, SEVERITY_COLOR.get(severity, "GREY"), "")
    return f"{color}{SEVERITY_TEXT.get(severity, severity.upper())}{C.RESET}"


@dataclass
class Finding:
    id: str
    title: str
    severity: str
    category: str            # arranque | fluidez | almacenamiento | térmico | memoria | cpu | seguridad
    component: str           # cpu_single | cpu_multi | memory | disk | system
    detail: str
    gain: float              # mejora estimada (fracción, 0.10 = 10%)
    gain_note: str
    effort: str              # bajo | medio | alto
    risk: str                # nulo | bajo | medio | alto
    steps: list[str] = field(default_factory=list)


class Auditor:
    def __init__(self, si: SystemInfo, bench: Benchmark | None):
        self.si = si
        self.bench = bench
        self.findings: list[Finding] = []
        self.checks_run = 0
        self.notes: list[str] = []

    def add(self, **kwargs) -> None:
        self.findings.append(Finding(**kwargs))

    # ------------------------------------------------------------------ core --
    def run(self) -> None:
        section("Auditoría del sistema")
        checks = [
            ("Espacio en disco", self.check_disk_space),
            ("Tipo de disco de sistema", self.check_disk_media),
            ("Memoria RAM", self.check_memory),
            ("Configuración de canales de RAM", self.check_ram_channels),
            ("Temperaturas y throttling", self.check_thermals),
            ("Frecuencia sostenida de CPU", self.check_cpu_frequency),
            ("Programas de inicio", self.check_startup),
            ("Procesos en segundo plano", self.check_background_load),
            ("Antigüedad de la instalación", self.check_os_age),
            ("Drivers gráficos", self.check_gpu_drivers),
        ]
        if IS_WINDOWS:
            checks += [
                ("Plan de energía", self.check_power_plan),
                ("TRIM en SSD", self.check_trim),
                ("Efectos visuales", self.check_visual_effects),
                ("Archivo de paginación", self.check_pagefile),
                ("Inicio rápido / hibernación", self.check_fast_startup),
                ("SysMain e indexación", self.check_services),
                ("Game DVR y captura en segundo plano", self.check_game_dvr),
                ("Integridad del sistema de archivos", self.check_filesystem_health),
                ("Antivirus y solapamientos", self.check_antivirus),
                ("Fragmentación / optimización", self.check_defrag),
                ("Salud SMART de los discos", self.check_smart),
            ]
        elif IS_LINUX:
            checks += [
                ("Gobernador de CPU", self.check_linux_governor),
                ("Swappiness", self.check_linux_swappiness),
                ("TRIM periódico", self.check_linux_trim),
            ]

        for label, fn in checks:
            spinner_step(label.ljust(38))
            try:
                msg = fn()
                self.checks_run += 1
                spinner_done(msg or "ok")
            except Exception as exc:
                spinner_done(f"no evaluable ({type(exc).__name__})", ok=False)

    # ------------------------------------------------------- comprobaciones --
    def check_disk_space(self) -> str:
        worst = None
        for d in self.si.disks:
            if d["total"] < 5 * 1024**3:
                continue
            free_pct = 100 - d["percent"]
            if worst is None or free_pct < worst[1]:
                worst = (d, free_pct)
        if not worst:
            return "sin datos"
        d, free_pct = worst
        if free_pct < 10:
            self.add(
                id="disk_space", title=f"Espacio libre crítico en {d['mount']}",
                severity="critical", category="almacenamiento", component="disk",
                detail=f"Solo queda un {free_pct:.1f}% libre ({human_bytes(d['free'])}). "
                       "Por debajo del 10% los SSD pierden capacidad de wear-leveling y "
                       "escribir bloques nuevos obliga a reorganizar celdas, lo que hunde "
                       "la velocidad de escritura. Windows tampoco puede crecer el pagefile.",
                gain=0.22, gain_note="escritura y respuesta general",
                effort="bajo", risk="nulo",
                steps=[
                    "Ejecuta `cleanmgr /sageset:1` y marca Windows Update Cleanup, archivos temporales y volcados",
                    "Revisa Configuración → Sistema → Almacenamiento → Recomendaciones de limpieza",
                    "Libera puntos de restauración antiguos: `vssadmin list shadowstorage`",
                    "Objetivo: dejar al menos un 20% libre en el disco de sistema",
                ])
            return f"{free_pct:.0f}% libre en {d['mount']} (crítico)"
        if free_pct < 20:
            self.add(
                id="disk_space", title=f"Espacio libre bajo en {d['mount']}",
                severity="medium", category="almacenamiento", component="disk",
                detail=f"Queda un {free_pct:.1f}% libre ({human_bytes(d['free'])}). Se recomienda "
                       "mantener un 20% libre para que el SSD mantenga rendimiento estable.",
                gain=0.08, gain_note="estabilidad de escritura",
                effort="bajo", risk="nulo",
                steps=["Limpieza de disco (`cleanmgr`)",
                       "Desinstalar software que no uses desde Aplicaciones instaladas",
                       "Mover bibliotecas grandes (juegos, vídeo) a otra unidad"])
            return f"{free_pct:.0f}% libre en {d['mount']}"
        return f"{free_pct:.0f}% libre (correcto)"

    def check_disk_media(self) -> str:
        media = self.si.system_drive_media
        if "HDD" in media and "SSD" not in media:
            self.add(
                id="hdd_system", title="El sistema arranca desde un disco mecánico (HDD)",
                severity="critical", category="almacenamiento", component="disk",
                detail="Es, con mucha diferencia, el mayor cuello de botella posible en un PC "
                       "moderno. Un HDD entrega ~100-200 IOPS aleatorias frente a las 20.000-500.000 "
                       "de un SSD NVMe. Ninguna optimización de software compensa esto: el arranque, "
                       "la apertura de programas y la carga de niveles en juegos están limitados por "
                       "acceso aleatorio, no por ancho de banda secuencial.",
                gain=0.85, gain_note="tiempos de carga y arranque (mejoras de 3-10x son habituales)",
                effort="medio", risk="bajo",
                steps=[
                    "Comprueba si la placa tiene ranura M.2 NVMe; si no, un SSD SATA 2,5\" también sirve",
                    "Instalación limpia de Windows en el SSD (preferible a clonar una instalación vieja)",
                    "Deja el HDD como almacenamiento secundario de datos, no de sistema",
                    "Tras migrar: verifica que TRIM está activo y desactiva la desfragmentación programada",
                ])
            return "HDD como disco de sistema (cuello de botella grave)"
        return f"{media}"

    def check_memory(self) -> str:
        vm = psutil.virtual_memory()
        total_gb = self.si.ram_total / 1024**3
        used_pct = vm.percent
        try:
            swap = psutil.swap_memory()
            swap_used_pct = swap.percent
        except Exception:
            swap_used_pct = 0.0

        if total_gb < 7.5:
            self.add(
                id="ram_low", title=f"RAM insuficiente ({total_gb:.0f} GB)",
                severity="high", category="memoria", component="memory",
                detail="Con menos de 8 GB, un sistema actual recurre constantemente al archivo "
                       "de paginación en cuanto abres un navegador con varias pestañas. Eso "
                       "convierte un problema de RAM en un problema de disco, y explica por sí "
                       "solo la mayoría de los casos de «va lentísimo».",
                gain=0.45, gain_note="multitarea y fluidez general",
                effort="medio", risk="bajo",
                steps=["Amplía a 16 GB como mínimo (32 GB si editas vídeo o compilas)",
                       "Instala los módulos en las ranuras correctas para dual channel (normalmente A2/B2)",
                       "Usa kits del mismo modelo para evitar problemas de compatibilidad"])
        elif total_gb < 15.5 and used_pct > 75:
            self.add(
                id="ram_pressure", title=f"Presión de memoria alta ({used_pct:.0f}% de {total_gb:.0f} GB)",
                severity="medium", category="memoria", component="memory",
                detail="El uso en reposo ya es alto; en carga real habrá paginación. Ampliar a "
                       "32 GB o reducir procesos residentes daría margen.",
                gain=0.18, gain_note="multitarea",
                effort="medio", risk="bajo",
                steps=["Revisa qué consume en el Administrador de tareas → Memoria",
                       "Amplía a 32 GB si el uso habitual supera el 80%"])
        if swap_used_pct > 60:
            self.add(
                id="swap_heavy", title="Uso intensivo del archivo de paginación",
                severity="medium", category="memoria", component="memory",
                detail=f"El swap está al {swap_used_pct:.0f}%. Indica que la RAM física no basta "
                       "para la carga actual.",
                gain=0.15, gain_note="fluidez bajo carga", effort="medio", risk="bajo",
                steps=["Cierra aplicaciones residentes innecesarias", "Considera ampliar RAM"])
        return f"{total_gb:.0f} GB · {used_pct:.0f}% en uso"

    def check_ram_channels(self) -> str:
        sticks = [s for s in self.si.ram_sticks if s["capacity"] > 0]
        if not sticks:
            return "sin datos (requiere Windows)"
        if len(sticks) == 1 and self.si.ram_total >= 6 * 1024**3:
            self.add(
                id="single_channel", title="RAM en single channel (un solo módulo)",
                severity="high", category="memoria", component="memory",
                detail="Un único módulo deja la mitad del ancho de banda de memoria sin usar. "
                       "El impacto es notable en gráficas integradas (hasta 30-40% de FPS) y "
                       "medible en CPU (5-15% en juegos y compilación).",
                gain=0.30, gain_note="ancho de banda de memoria; más en iGPU",
                effort="medio", risk="bajo",
                steps=["Añade un segundo módulo idéntico y colócalo según el manual de la placa (A2+B2)",
                       "Verifica en el Administrador de tareas → Rendimiento → Memoria que aparece «Dual»"])
            return "single channel (1 módulo)"
        speed = self.si.ram_speed_mhz
        if speed and speed <= 2400 and "AMD" in (self.si.cpu_name or "").upper():
            self.add(
                id="ram_slow", title=f"RAM funcionando a {speed} MT/s (perfil XMP/EXPO sin activar)",
                severity="medium", category="memoria", component="memory",
                detail="Las memorias arrancan por defecto a la velocidad JEDEC base. Activar el "
                       "perfil XMP/EXPO en la BIOS suele subir de 2133-2400 a 3200-6000 MT/s sin "
                       "coste alguno. En Ryzen el impacto en juegos es especialmente alto porque "
                       "el Infinity Fabric escala con la frecuencia de memoria.",
                gain=0.12, gain_note="rendimiento en juegos y latencia",
                effort="bajo", risk="medio",
                steps=["Entra en la BIOS/UEFI (Del o F2 al arrancar)",
                       "Activa el perfil XMP (Intel) o EXPO/DOCP (AMD)",
                       "Guarda y verifica estabilidad con MemTest86 o TestMem5 durante 1 hora",
                       "Si no arranca: borra la CMOS o baja al perfil 2 / velocidad inferior"])
            return f"{len(sticks)} módulos a {speed} MT/s (XMP sin activar)"
        return f"{len(sticks)} módulos" + (f" a {speed} MT/s" if speed else "")

    def check_thermals(self) -> str:
        samples = self.bench.thermal_samples if self.bench else []
        current = read_cpu_temperature()
        peak = max(samples) if samples else current
        if peak is None:
            self.notes.append("No se pudo leer la temperatura de la CPU. En Windows suele requerir "
                              "HWiNFO64 o LibreHardwareMonitor con permisos de administrador.")
            return "no disponible"
        if peak >= 95:
            self.add(
                id="thermal_critical", title=f"Throttling térmico severo ({peak:.0f} °C bajo carga)",
                severity="critical", category="térmico", component="cpu_multi",
                detail="A partir de ~95-100 °C la CPU reduce frecuencia para protegerse. Estás "
                       "perdiendo rendimiento de forma permanente y el equipo tiene un problema "
                       "físico de disipación: pasta térmica degradada, polvo en el disipador o "
                       "ventilación insuficiente. Ninguna optimización de software lo arregla.",
                gain=0.30, gain_note="frecuencia sostenida de CPU",
                effort="medio", risk="bajo",
                steps=["Limpia con aire comprimido disipador, ventiladores y filtros",
                       "Renueva la pasta térmica (dura 3-5 años; en portátiles a menudo menos)",
                       "Verifica que los ventiladores giran y que la curva del BIOS no es demasiado silenciosa",
                       "En portátiles: usa base elevada y no lo apoyes sobre superficies blandas",
                       "Si persiste, considera un disipador mejor o undervolting"])
            return f"{peak:.0f} °C (crítico)"
        if peak >= 85:
            self.add(
                id="thermal_high", title=f"Temperaturas elevadas ({peak:.0f} °C bajo carga)",
                severity="high", category="térmico", component="cpu_multi",
                detail="Todavía por debajo del límite, pero con poco margen. En verano o en cargas "
                       "sostenidas entrará en throttling. Limpieza y pasta térmica devuelven "
                       "típicamente 10-20 °C en equipos de más de 3 años.",
                gain=0.12, gain_note="frecuencia sostenida",
                effort="medio", risk="bajo",
                steps=["Limpieza física del sistema de refrigeración",
                       "Renovar pasta térmica si el equipo tiene más de 3 años",
                       "Revisar el flujo de aire de la caja (entrada frontal, salida trasera/superior)"])
            return f"{peak:.0f} °C (elevado)"
        return f"{peak:.0f} °C (correcto)"

    def check_cpu_frequency(self) -> str:
        samples = self.bench.freq_samples if self.bench else []
        if not samples or not self.si.cpu_max_mhz:
            return "sin datos"
        sustained = statistics.median(samples)
        ratio = sustained / self.si.cpu_max_mhz
        if ratio < 0.62:
            self.add(
                id="freq_low", title=f"CPU sostenida al {ratio * 100:.0f}% de su frecuencia máxima",
                severity="high", category="cpu", component="cpu_multi",
                detail=f"Media de {sustained:.0f} MHz bajo carga frente a {self.si.cpu_max_mhz:.0f} MHz "
                       "nominales. Causas habituales: plan de energía en modo ahorro, límites de "
                       "potencia (PL1/PL2 o TDP del portátil), batería, o throttling térmico.",
                gain=0.25, gain_note="rendimiento de CPU",
                effort="bajo", risk="bajo",
                steps=["Pon el plan de energía en Alto rendimiento y conecta el portátil a la red",
                       "Revisa el modo de rendimiento del fabricante (Lenovo Vantage, MyASUS, Armoury Crate…)",
                       "Comprueba temperaturas: si superan 90 °C, es un problema térmico",
                       "En portátiles: revisa que el cargador sea el original y con potencia suficiente"])
            return f"{sustained:.0f} MHz sostenidos ({ratio * 100:.0f}% del máximo)"
        return f"{sustained:.0f} MHz sostenidos ({ratio * 100:.0f}%)"

    def check_startup(self) -> str:
        items: list[str] = []
        if IS_WINDOWS and winreg is not None:
            for hive, path in (
                (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
            ):
                items.extend(reg_list_values(hive, path).keys())
            data = ps_json("Get-CimInstance Win32_StartupCommand | Select-Object Name,Location", timeout=25)
            items.extend(str(d.get("Name")) for d in data if d.get("Name"))
            items = sorted(set(items))
        self.startup_items = items
        count = len(items)
        if count >= 12:
            sev, gain = ("high", 0.35) if count >= 20 else ("medium", 0.22)
            self.add(
                id="startup_bloat", title=f"{count} programas configurados para iniciarse con Windows",
                severity=sev, category="arranque", component="system",
                detail="Cada entrada compite por CPU y, sobre todo, por E/S de disco durante el "
                       "arranque. Es la causa número uno de «el PC tarda mucho en estar usable». "
                       f"Detectados: {', '.join(items[:10])}"
                       f"{'…' if count > 10 else ''}.",
                gain=gain, gain_note="tiempo hasta escritorio usable",
                effort="bajo", risk="nulo",
                steps=["Ctrl+Shift+Esc → pestaña Inicio: ordena por «Impacto de inicio» y desactiva lo de impacto Alto",
                       "Desactiva actualizadores (Adobe, Java, iTunes), launchers de juegos y clientes de nube que no uses a diario",
                       "No desactives: antivirus, drivers de audio, software de touchpad ni utilidades del fabricante esenciales",
                       "Revisa también Programador de tareas → tareas con disparador «Al iniciar sesión»"])
            return f"{count} entradas de inicio"
        return f"{count} entradas de inicio (razonable)"

    def check_background_load(self) -> str:
        procs = []
        for p in psutil.process_iter(["name", "memory_info", "cpu_percent"]):
            try:
                info = p.info
                mem = info["memory_info"].rss if info["memory_info"] else 0
                procs.append((info["name"] or "?", mem))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        total = len(procs)
        heavy = sorted(procs, key=lambda x: -x[1])[:8]
        self.top_processes = [{"name": n, "rss": m} for n, m in heavy]
        if total > 220:
            self.add(
                id="proc_bloat", title=f"{total} procesos activos en reposo",
                severity="medium", category="fluidez", component="system",
                detail="Un sistema limpio de Windows 11 en reposo ronda los 120-170 procesos. "
                       "Un número muy superior indica acumulación de software residente. "
                       f"Los que más RAM consumen: "
                       f"{', '.join(f'{n} ({human_bytes(m)})' for n, m in heavy[:5])}.",
                gain=0.12, gain_note="fluidez y RAM disponible",
                effort="bajo", risk="bajo",
                steps=["Desinstala software que no uses desde Configuración → Aplicaciones instaladas",
                       "Revisa la bandeja del sistema: casi todo lo que hay ahí puede desactivarse al inicio",
                       "Usa Autoruns (Sysinternals) para ver todo lo que se carga, no solo el Administrador de tareas"])
            return f"{total} procesos"
        return f"{total} procesos (normal)"

    def check_os_age(self) -> str:
        age = self.si.os_age_days
        if age is None:
            return "sin datos"
        years = age / 365.25
        startup_count = len(getattr(self, "startup_items", []))
        heavy_install = startup_count >= 15 or age > 1095
        if years >= 3 and heavy_install:
            self.add(
                id="os_stale", title=f"Instalación de Windows con {years:.1f} años de antigüedad",
                severity="medium", category="fluidez", component="system",
                detail="Instalaciones muy antiguas acumulan drivers huérfanos, entradas de registro "
                       "muertas, servicios de software desinstalado y capas de actualizaciones. Una "
                       "instalación limpia suele recuperar entre un 10 y un 25% de fluidez percibida "
                       "y bastante espacio en disco. Nota: hazla solo después de descartar problemas "
                       "de hardware (disco, temperaturas, RAM); si el cuello de botella es físico, "
                       "reinstalar no arregla nada.",
                gain=0.18, gain_note="fluidez percibida y tiempo de arranque",
                effort="alto", risk="medio",
                steps=["Antes de nada: prueba `DISM /Online /Cleanup-Image /RestoreHealth` y luego `sfc /scannow`",
                       "Copia de seguridad completa de datos y lista de licencias/software",
                       "Descarga los drivers de chipset, red y GPU ANTES de reinstalar",
                       "Usa la Media Creation Tool para una instalación limpia (no «Restablecer este PC»)",
                       "La licencia digital se reactiva sola si es la misma placa base"])
            return f"{years:.1f} años (recomendable reinstalar)"
        return f"{years:.1f} años"

    def check_gpu_drivers(self) -> str:
        if not self.si.gpus:
            return "sin datos"
        stale = [g for g in self.si.gpus if (g.get("driver_age_days") or 0) > 400]
        if stale:
            g = stale[0]
            self.add(
                id="gpu_driver", title=f"Driver gráfico con {g['driver_age_days']} días de antigüedad",
                severity="medium", category="fluidez", component="system",
                detail=f"{g['name']} usa un driver de {g.get('driver_date')}. Los drivers de GPU "
                       "incluyen optimizaciones específicas por juego y correcciones de rendimiento; "
                       "es una de las actualizaciones con mejor relación beneficio/esfuerzo.",
                gain=0.10, gain_note="rendimiento gráfico (variable por título)",
                effort="bajo", risk="bajo",
                steps=["Descarga el driver desde nvidia.com / amd.com / intel.com, no desde Windows Update",
                       "En NVIDIA elige instalación personalizada → limpia",
                       "Si vienes de otra marca de GPU, limpia primero con DDU en modo seguro"])
            return f"driver de {g.get('driver_date')} (desactualizado)"
        return "actualizados"

    # ------------------------------------------------------- solo Windows ----
    def check_power_plan(self) -> str:
        out = run_cmd(["powercfg", "/getactivescheme"], timeout=15) or ""
        name = out.split("(", 1)[1].rstrip(")").strip() if "(" in out else out
        lowered = name.lower()
        good = any(k in lowered for k in ("alto rendimiento", "high performance", "máximo",
                                          "ultimate", "rendimiento"))
        if not good:
            self.add(
                id="power_plan", title=f"Plan de energía en «{name}»",
                severity="medium", category="fluidez", component="cpu_multi",
                detail="Los planes equilibrados o de ahorro limitan la frecuencia mínima del "
                       "procesador y retrasan la subida de turbo, lo que se nota como microtirones "
                       "y latencia al abrir programas. En sobremesa no hay motivo para no usar "
                       "Alto rendimiento; en portátil, úsalo solo enchufado.",
                gain=0.08, gain_note="latencia y frecuencia de CPU",
                effort="bajo", risk="nulo",
                steps=["`powercfg /setactive SCHEME_MIN` (Alto rendimiento)",
                       "Para desbloquear el plan Ultimate: "
                       "`powercfg -duplicatescheme e9a42b02-d5df-448d-aa00-03f14749eb61`",
                       "En portátil, mantén Equilibrado con batería para no perder autonomía"])
            return f"«{name}»"
        return f"«{name}» (correcto)"

    def check_trim(self) -> str:
        if "SSD" not in self.si.system_drive_media:
            return "no aplica (sin SSD detectado)"
        out = run_cmd(["fsutil", "behavior", "query", "DisableDeleteNotify"], timeout=15) or ""
        disabled = any(tok in out for tok in ("= 1", "=1"))
        if disabled:
            self.add(
                id="trim_off", title="TRIM desactivado en un SSD",
                severity="high", category="almacenamiento", component="disk",
                detail="Sin TRIM, el SSD no sabe qué bloques están libres y acaba haciendo "
                       "read-modify-write constantemente. La velocidad de escritura se degrada "
                       "progresivamente y la vida útil del disco se acorta.",
                gain=0.20, gain_note="escritura sostenida y durabilidad",
                effort="bajo", risk="nulo",
                steps=["Como administrador: `fsutil behavior set DisableDeleteNotify 0`",
                       "Fuerza un TRIM manual: `defrag C: /L`",
                       "Verifica que la tarea programada «Optimizar unidades» está activa"])
            return "desactivado"
        return "activo"

    def check_visual_effects(self) -> str:
        pref = reg_read(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects",
                        "VisualFXSetting")
        weak_gpu = any("intel" in (g.get("name") or "").lower() and "arc" not in (g.get("name") or "").lower()
                       for g in self.si.gpus)
        low_score = bool(self.bench and self.bench.overall() < 65)
        if pref in (0, 1, None) and (weak_gpu or low_score):
            self.add(
                id="visual_fx", title="Animaciones y transparencias de Windows activas",
                severity="low", category="fluidez", component="system",
                detail="Las animaciones no reducen los FPS, pero añaden 150-300 ms de latencia "
                       "percibida en cada acción (minimizar, abrir menús, cambiar de ventana). "
                       "En equipos modestos, desactivarlas es el cambio que más «se nota» sin "
                       "tocar hardware.",
                gain=0.06, gain_note="latencia percibida de la interfaz",
                effort="bajo", risk="nulo",
                steps=["Ejecuta `SystemPropertiesPerformance.exe` → Ajustar para obtener el mejor rendimiento",
                       "Vuelve a marcar «Suavizar bordes de las fuentes de pantalla» (si no, el texto se ve mal)",
                       "Configuración → Accesibilidad → Efectos visuales: desactiva transparencias y animaciones"])
            return "activas"
        return "configuradas"

    def check_pagefile(self) -> str:
        data = ps_json("Get-CimInstance Win32_PageFileUsage | Select-Object Name,AllocatedBaseSize,CurrentUsage")
        if not data:
            total_gb = self.si.ram_total / 1024**3
            if total_gb < 32:
                self.add(
                    id="pagefile_off", title="Archivo de paginación desactivado o no detectado",
                    severity="medium", category="memoria", component="system",
                    detail="Desactivar el pagefile es un «truco» muy extendido y contraproducente: "
                           "Windows lo usa para descargar páginas frías y para volcados de error. "
                           "Sin él, aplicaciones que reservan memoria de forma agresiva fallan con "
                           "errores de memoria insuficiente aunque sobre RAM física.",
                    gain=0.05, gain_note="estabilidad, no velocidad",
                    effort="bajo", risk="nulo",
                    steps=["`SystemPropertiesAdvanced.exe` → Rendimiento → Opciones avanzadas → Memoria virtual",
                           "Marca «Administrar automáticamente» y déjalo en el SSD más rápido"])
                return "desactivado"
            return "desactivado (aceptable con mucha RAM)"
        return f"{len(data)} archivo(s), {data[0].get('AllocatedBaseSize', '?')} MB"

    def check_fast_startup(self) -> str:
        val = reg_read(winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Power",
                       "HiberbootEnabled")
        if val == 0 and "HDD" in self.si.system_drive_media:
            self.add(
                id="fast_startup", title="Inicio rápido desactivado en un equipo con HDD",
                severity="low", category="arranque", component="system",
                detail="El inicio rápido guarda el kernel hibernado y recorta el arranque de forma "
                       "notable en discos lentos. Contrapartida: no libera la RAM entre sesiones y "
                       "puede dar problemas en arranque dual con Linux.",
                gain=0.15, gain_note="tiempo de arranque",
                effort="bajo", risk="bajo",
                steps=["`powercfg /hibernate on`",
                       "Panel de control → Opciones de energía → Elegir el comportamiento de los botones "
                       "→ Activar inicio rápido",
                       "Si usas arranque dual con Linux, déjalo desactivado"])
            return "desactivado (con HDD, conviene activarlo)"
        return "activado" if val else "desactivado"

    def check_services(self) -> str:
        rows = ps_json("Get-Service | Where-Object {$_.Status -eq 'Running'} | "
                       "Select-Object Name,DisplayName,StartType")
        running = len(rows)
        names = {str(r.get("Name", "")).lower() for r in rows}
        if "sysmain" in names and "HDD" not in self.si.system_drive_media:
            self.add(
                id="sysmain", title="SysMain (Superfetch) activo en un SSD",
                severity="low", category="fluidez", component="system",
                detail="SysMain precarga en RAM lo que cree que vas a abrir. Tiene sentido en HDD, "
                       "pero en SSD el beneficio es marginal y genera escrituras y uso de disco "
                       "en segundo plano. Nota: en algunos equipos desactivarlo empeora la "
                       "sensación de arranque, así que mídelo antes y después.",
                gain=0.04, gain_note="uso de disco en segundo plano",
                effort="bajo", risk="bajo",
                steps=["`sc config SysMain start=disabled` y `sc stop SysMain` (como administrador)",
                       "Reinicia y compara: si notas peor respuesta, reactívalo con `start=auto`"])
        if "wsearch" in names and "HDD" in self.si.system_drive_media:
            self.add(
                id="wsearch_hdd", title="Indexación de Windows Search activa en HDD",
                severity="medium", category="almacenamiento", component="disk",
                detail="El indexador genera acceso aleatorio constante, precisamente lo que peor "
                       "hace un disco mecánico. En HDD suele ser responsable de los picos de "
                       "«disco al 100%».",
                gain=0.10, gain_note="disponibilidad del disco",
                effort="bajo", risk="bajo",
                steps=["Panel de control → Opciones de indexación → Modificar: reduce a Documentos y Escritorio",
                       "O desactiva el servicio: `sc config WSearch start=disabled`",
                       "Contrapartida: las búsquedas en el menú Inicio serán más lentas"])
        return f"{running} servicios en ejecución"

    def check_game_dvr(self) -> str:
        dvr = reg_read(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled")
        capture = reg_read(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\GameDVR",
                           "AppCaptureEnabled")
        if dvr == 1 or capture == 1:
            self.add(
                id="game_dvr", title="Grabación en segundo plano de Xbox Game Bar activa",
                severity="low", category="fluidez", component="system",
                detail="La captura en segundo plano mantiene un búfer de vídeo constante. El coste "
                       "típico es de un 2-6% de FPS y algo de latencia añadida. Si no usas la "
                       "función de «grabar los últimos 30 segundos», no aporta nada.",
                gain=0.04, gain_note="FPS en juegos",
                effort="bajo", risk="nulo",
                steps=["Configuración → Juegos → Capturas: desactiva «Grabar lo que ocurrió»",
                       "Configuración → Juegos → Xbox Game Bar: desactivar si no la usas",
                       "Activa el Modo de juego (sí conviene tenerlo activado)"])
            return "activa"
        return "desactivada"

    def check_filesystem_health(self) -> str:
        drive = os.environ.get("SystemDrive", "C:")
        out = run_cmd(["fsutil", "dirty", "query", drive], timeout=15) or ""
        if "not dirty" not in out.lower() and "no está" not in out.lower() and out:
            self.add(
                id="fs_dirty", title="El volumen de sistema está marcado como «sucio»",
                severity="high", category="almacenamiento", component="disk",
                detail="Windows ha detectado inconsistencias en el sistema de archivos y programará "
                       "una comprobación. Puede indicar un apagado incorrecto o, más preocupante, "
                       "un disco con sectores defectuosos.",
                gain=0.10, gain_note="estabilidad y velocidad de E/S",
                effort="bajo", risk="bajo",
                steps=[f"Programa una comprobación: `chkdsk {drive} /f /r` y reinicia",
                       "Revisa el estado SMART del disco con CrystalDiskInfo",
                       "Haz copia de seguridad antes de nada si SMART muestra advertencias"])
            return "marcado como sucio"
        return "limpio"

    def check_antivirus(self) -> str:
        rows = ps_json('Get-CimInstance -Namespace "root/SecurityCenter2" -ClassName AntiVirusProduct '
                       '-ErrorAction SilentlyContinue | Select-Object displayName,productState')
        names = [str(r.get("displayName")) for r in rows if r.get("displayName")]
        third_party = [n for n in names if "defender" not in n.lower()]
        if len(third_party) >= 2:
            self.add(
                id="av_stack", title=f"Varios antivirus instalados ({', '.join(third_party)})",
                severity="high", category="fluidez", component="system",
                detail="Dos motores de análisis en tiempo real se escanean mutuamente. El resultado "
                       "es un impacto grande en la E/S de disco, conflictos y menor protección real, "
                       "no mayor.",
                gain=0.20, gain_note="E/S de disco y fluidez",
                effort="bajo", risk="bajo",
                steps=["Deja un único antivirus en tiempo real",
                       "Desinstala el resto con la herramienta de limpieza oficial del fabricante",
                       "Windows Defender es suficiente para la mayoría de usuarios"])
            return f"{len(names)} productos ({', '.join(names)})"
        return ", ".join(names) if names else "no detectado"

    def check_defrag(self) -> str:
        if "HDD" not in self.si.system_drive_media:
            return "no aplica (SSD)"
        self.add(
            id="defrag_hdd", title="Desfragmentación recomendable en disco mecánico",
            severity="low", category="almacenamiento", component="disk",
            detail="En HDD la fragmentación obliga al cabezal a saltar entre zonas del plato. "
                   "Desfragmentar recupera velocidad secuencial. (En SSD no se debe hacer nunca: "
                   "solo desgasta celdas.)",
            gain=0.07, gain_note="lectura secuencial en HDD",
            effort="bajo", risk="nulo",
            steps=["`defrag C: /U /V /O` como administrador",
                   "Comprueba que la tarea programada «Optimizar unidades» está activa (semanal)"])
        return "pendiente de optimizar"

    def check_smart(self) -> str:
        rows = ps_json("Get-PhysicalDisk | Select-Object FriendlyName,HealthStatus,OperationalStatus")
        bad = [r for r in rows if str(r.get("HealthStatus", "")).lower() not in ("healthy", "0", "sano")]
        if bad:
            names = ", ".join(str(r.get("FriendlyName")) for r in bad)
            self.add(
                id="smart_warn", title=f"Disco con estado de salud degradado ({names})",
                severity="critical", category="almacenamiento", component="disk",
                detail="Windows informa de un estado distinto de «Healthy». Antes de optimizar "
                       "nada, haz copia de seguridad: un disco en degradación puede fallar sin "
                       "más aviso y explica por sí solo cualquier lentitud.",
                gain=0.0, gain_note="no es una optimización: es riesgo de pérdida de datos",
                effort="alto", risk="alto",
                steps=["Copia de seguridad inmediata de los datos importantes",
                       "Revisa los atributos SMART con CrystalDiskInfo (reallocated sectors, pending)",
                       "Planifica la sustitución del disco"])
            return f"degradado: {names}"
        return f"{len(rows)} disco(s) sano(s)" if rows else "sin datos"

    # --------------------------------------------------------- solo Linux ----
    def check_linux_governor(self) -> str:
        path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        if not path.exists():
            return "no disponible"
        gov = path.read_text().strip()
        if gov in ("powersave", "conservative"):
            self.add(
                id="linux_governor", title=f"Gobernador de CPU en «{gov}»",
                severity="medium", category="cpu", component="cpu_multi",
                detail="Limita la escalada de frecuencia y añade latencia en cargas a ráfagas.",
                gain=0.10, gain_note="respuesta de CPU", effort="bajo", risk="nulo",
                steps=["`sudo cpupower frequency-set -g performance`",
                       "Para hacerlo persistente, configura tuned o un servicio systemd"])
        return gov

    def check_linux_swappiness(self) -> str:
        path = Path("/proc/sys/vm/swappiness")
        if not path.exists():
            return "no disponible"
        val = int(path.read_text().strip())
        if val >= 60 and self.si.ram_total > 8 * 1024**3:
            self.add(
                id="linux_swappiness", title=f"vm.swappiness = {val} con RAM abundante",
                severity="low", category="memoria", component="system",
                detail="Un valor alto hace que el kernel envíe páginas a swap antes de lo necesario.",
                gain=0.05, gain_note="latencia bajo carga", effort="bajo", risk="nulo",
                steps=["`sudo sysctl vm.swappiness=10`",
                       "Persistente: añade `vm.swappiness=10` a /etc/sysctl.d/99-tuning.conf"])
        return str(val)

    def check_linux_trim(self) -> str:
        out = run_cmd(["systemctl", "is-enabled", "fstrim.timer"], timeout=10)
        if out and "enabled" not in out:
            self.add(
                id="linux_trim", title="fstrim.timer no está activado",
                severity="medium", category="almacenamiento", component="disk",
                detail="Sin TRIM periódico, el SSD degrada su rendimiento de escritura.",
                gain=0.12, gain_note="escritura sostenida", effort="bajo", risk="nulo",
                steps=["`sudo systemctl enable --now fstrim.timer`"])
        return out or "no disponible"
