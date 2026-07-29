"""Auditoria del sistema: comprobaciones de configuracion y hallazgos.

La fachada del paquete. Reexporta lo que el resto del programa importa de
`audit` —el vocabulario de `modelo`, y el `Auditor` que se arma aquí— para que
dividir esto por dentro no obligue a tocar ni un import de fuera.
"""

from __future__ import annotations

import os
import re
import statistics
from datetime import date, datetime
from pathlib import Path

import psutil

from ..benchmark import Benchmark
from ..sensors import cpu_temperature, gpu_temperature, temperature_report, temperature_source
from ..storage_scan import ScanResult, candidate_bytes
from ..console import C, human_bytes, section, spinner_done, spinner_step
from ..const import IS_LINUX, IS_WINDOWS
from .. import elevacion
from ..platform_utils import (_sys_exe, boot_performance, pending_driver_updates,
                              pending_security_updates, ps_json, reg_key_readable,
                              reg_list_values, reg_read, run_cmd, winreg)
from ..network import WIFI_TECHO, wifi_capability
from ..sysinfo import (KIND_LABELS, SystemInfo, _parse_cim_date, local_volumes,
                       primary_gpu)

from .modelo import (SEGURIDAD, SEVERITY_COLOR, SEVERITY_ORDER, SEVERITY_TEXT,
                     Finding, NoAplica, SinDato, _ESFUERZOS, _ID_VALIDO,
                     _RIESGOS, security_findings, sev_label)


# `productState` es un entero de 32 bits que el Centro de seguridad de Windows
# no documenta en ninguna parte oficial, pero cuyo reparto es estable desde
# Vista: 0xAABBCC, donde BB dice si el motor está vigilando (0x10 u 0x11) y CC
# si las firmas están al día (0x00) o caducadas (0x10). AA es el tipo de
# producto y aquí no interesa.
#
# Precisamente por no estar documentado, un valor que no encaje en ninguno de
# los dos juegos conocidos no se interpreta: se calla. Es la misma decisión que
# toma `check_filesystem_health` cuando no reconoce la respuesta de fsutil.
_AV_MOTOR = {0x00: False, 0x01: False, 0x10: True, 0x11: True}   # {byte: vigilando}
_AV_FIRMAS = {0x00: True, 0x10: False}                           # {byte: al día}


def _estado_antivirus(state) -> tuple[bool, bool] | None:
    """(vigilando, firmas al día), o None si el entero no se reconoce.

    Los dos bytes se traducen por tabla y no por comparación: así, un valor
    nuevo que Microsoft empiece a devolver algún día cae por su propio peso en
    «no lo reconozco» en vez de colarse como uno de los conocidos.
    """
    if isinstance(state, bool) or not isinstance(state, int):
        return None
    motor, firmas = (state >> 8) & 0xFF, state & 0xFF
    if motor not in _AV_MOTOR or firmas not in _AV_FIRMAS:
        return None
    return _AV_MOTOR[motor], _AV_FIRMAS[firmas]


# El «código de problema» del Administrador de dispositivos: el número que hay
# detrás del signo de exclamación amarillo, que Windows enseña en una ventana que
# nadie abre. `ConfigManagerErrorCode` lo expone tal cual, y estos son los que
# significan que el dispositivo no está haciendo su trabajo.
_PNP_PROBLEMA = {
    1: "Windows no tiene su configuración",
    3: "su driver está dañado, o falta memoria",
    10: "no puede arrancar",
    12: "no hay recursos libres suficientes para él",
    14: "necesita que reinicies para terminar de configurarse",
    18: "hay que reinstalar sus drivers",
    19: "su configuración en el registro está dañada",
    21: "Windows lo está quitando",
    24: "no está presente, o no funciona bien",
    28: "no tiene drivers instalados",
    31: "Windows no puede cargar los drivers que necesita",
    35: "la BIOS no le ha reservado recursos",
    37: "su driver ha fallado al inicializarlo",
    39: "su driver falta o está dañado",
    43: "Windows lo ha parado porque el propio dispositivo avisó de un fallo",
    48: "su software está bloqueado por incompatible",
    52: "no se puede verificar la firma de su driver",
}

# Estos códigos también son distintos de cero y ninguno es una avería: los tres
# primeros los provoca quien usa el equipo y el último es un estado de paso. Van
# aparte para no acusar de estropeado a lo que alguien apagó a propósito.
_PNP_DELIBERADO = frozenset({22, 32, 45, 47})

# Cinco años. Por debajo hay demasiado driver que sencillamente está terminado y
# no necesita más versiones: un lector de tarjetas de 2022 funciona igual hoy.
_DRIVER_VIEJO_DIAS = round(5 * 365.25)


class Auditor:
    def __init__(self, si: SystemInfo, bench: Benchmark | None,
                 scan: ScanResult | None = None, check_drivers: bool = False,
                 network: dict | None = None, check_updates: bool = False):
        self.si = si
        self.network = network or {}
        self.bench = bench
        self.scan = scan
        self.check_drivers = check_drivers
        self.check_updates = check_updates
        self.findings: list[Finding] = []
        self.checks_run = 0
        self.notes: list[str] = []
        # Comprobaciones que no llegaron a un veredicto y por qué. Se informan
        # aparte: un informe que dice «24 pruebas» cuando cinco no pudieron
        # mirar nada está exagerando su propia cobertura.
        self.unverified: list[tuple[str, str]] = []
        self.not_applicable: list[tuple[str, str]] = []
        # Arranque medido por Windows. `None` es «no se pudo leer», que no es lo
        # mismo que «rápido»: el log pide privilegios de administrador.
        self.boot_report: dict = {}
        self.boot_seconds: float | None = None

    def add(self, **kwargs) -> None:
        """Registra un hallazgo, validando antes lo que el HTML no escapa.

        `export_html` interpola `f.severity` y `f.id` sin pasarlos por `_e()`,
        y `export_plan` hace lo propio con `f.effort` y `f.risk` dentro de unas
        comillas dobles de PowerShell. Hoy es seguro porque los cuatro salen de
        constantes escritas en este fichero. Pero es una invariante que nadie ha
        declarado: el día que una comprobación nueva construya un `id` con el
        nombre de un disco —cosa que el firmware del dispositivo decide, no
        nosotros— el escape no está.

        Se valida aquí, en el origen, y no aplicando `_e()` en el HTML, porque
        así la garantía vale para las cuatro salidas y no solo para una. Falla
        ruidosamente a propósito: es un error de programación del propio
        Quilate, no un dato del sistema que haya que tolerar, y el sitio donde
        tiene que saltar es el del programador y no el informe del usuario.
        """
        severity = kwargs.get("severity")
        if severity not in SEVERITY_ORDER:
            raise ValueError(
                f"severidad no declarada: {severity!r}. Las válidas son "
                f"{', '.join(SEVERITY_ORDER)}")
        identificador = kwargs.get("id")
        if not isinstance(identificador, str) or not _ID_VALIDO.match(identificador):
            raise ValueError(
                f"identificador de hallazgo no válido: {identificador!r}. Tiene que "
                f"casar con {_ID_VALIDO.pattern}: se usa como ancla del HTML "
                f"(«#h-{{id}}») y ahí no se escapa nada")
        for campo, validos in (("effort", _ESFUERZOS), ("risk", _RIESGOS)):
            valor = kwargs.get(campo)
            if valor not in validos:
                raise ValueError(
                    f"{campo} no declarado: {valor!r}. Los válidos son "
                    f"{', '.join(validos)}: van al `.ps1` sin escapar")
        self.findings.append(Finding(**kwargs))

    # ------------------------------------------------------------------ core --
    def run(self) -> None:
        section("Auditoría del sistema")
        checks = [
            ("Espacio en disco", self.check_disk_space),
            ("Archivos grandes y espacio recuperable", self.check_large_files),
            ("Tipo de disco de sistema", self.check_disk_media),
            ("Memoria RAM", self.check_memory),
            ("Configuración de canales de RAM", self.check_ram_channels),
            ("Temperaturas y throttling", self.check_thermals),
            ("Frecuencia de CPU bajo carga", self.check_cpu_frequency),
            ("Programas de inicio", self.check_startup),
            ("Procesos en segundo plano", self.check_background_load),
            ("Tiempo sin reiniciar", self.check_uptime),
            ("Antigüedad de la instalación", self.check_os_age),
            ("Drivers gráficos", self.check_gpu_drivers),
            ("Enlace de red", self.check_network_link),
            ("Latencia y DNS", self.check_network_latency),
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
                ("Duración real del arranque", self.check_boot_time),
                ("Integridad del sistema de archivos", self.check_filesystem_health),
                ("Antivirus y solapamientos", self.check_antivirus),
                ("Fragmentación / optimización", self.check_defrag),
                ("Salud SMART de los discos", self.check_smart),
                ("Dispositivos con problema", self.check_device_problems),
                ("Antigüedad de los drivers", self.check_old_drivers),
                ("Antigüedad de la BIOS", self.check_bios_age),
                ("Cifrado del disco (BitLocker)", self.check_disk_encryption),
                ("Arranque seguro (Secure Boot)", self.check_secure_boot),
                ("Chip TPM", self.check_tpm),
                ("Protocolo SMB1", self.check_smb1),
                ("Cuentas locales sin contraseña", self.check_local_accounts),
            ]
            # Detrás de un flag, igual que --check-drivers y por lo mismo: la
            # consulta a Windows Update tarda entre 10 y 30 segundos. No se
            # registra si no se ha pedido, para que no cuente como pendiente
            # algo que nadie ha querido preguntar.
            if self.check_updates:
                checks.append(("Actualizaciones de seguridad", self.check_security_updates))
        elif IS_LINUX:
            checks += [
                ("Gobernador de CPU", self.check_linux_governor),
                ("Swappiness", self.check_linux_swappiness),
                ("TRIM periódico", self.check_linux_trim),
            ]

        self.checks_total = len(checks)
        for label, fn in checks:
            spinner_step(label.ljust(38))
            try:
                msg = fn()
                self.checks_run += 1
                spinner_done(msg or "ok")
            except NoAplica as exc:
                self.not_applicable.append((label, str(exc)))
                spinner_done(f"no aplica: {exc}", neutral=True)
            except SinDato as exc:
                self.unverified.append((label, str(exc)))
                spinner_done(f"sin comprobar: {exc}", ok=False)
            except Exception as exc:
                # Una comprobación que revienta desaparecía del informe sin
                # dejar rastro, que es la peor forma posible de fallar.
                self.unverified.append((label, f"error interno · {type(exc).__name__}: {exc}"))
                spinner_done(f"no evaluable ({type(exc).__name__})", ok=False)

    # ------------------------------------------------------- comprobaciones --
    def check_disk_space(self) -> str:
        # Solo almacenamiento físico local: una unidad de Google Drive, OneDrive
        # o de red informa de un tamaño que no es del equipo y no se libera
        # borrando archivos aquí. Auditarla solo genera avisos falsos.
        ignored = [d for d in self.si.disks if d["ignored"] and d["total"] > 5 * 1024**3]
        if ignored:
            self.notes.append(
                "Volúmenes excluidos de la auditoría de almacenamiento por no ser discos "
                "locales: " + ", ".join(
                    f"{d['mount']} ({d['label'] or KIND_LABELS.get(d['kind'], d['kind'])})"
                    for d in ignored) + ".")

        worst = None
        for d in local_volumes(self.si):
            free_pct = 100 - d["percent"]
            if worst is None or free_pct < worst[1]:
                worst = (d, free_pct)
        if not worst:
            raise SinDato("no se ha identificado ningún volumen local")
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
        if not media or "desconocido" in media.lower():
            # De este dato dependen TRIM, desfragmentación y SysMain: darlo por
            # SSD cuando no se sabe apagaría tres avisos de golpe.
            raise SinDato("no se ha podido identificar el tipo de disco de sistema")
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
        # psutil siempre sabe cuánta RAM hay; si el inventario no la trae, es
        # que falló WMI. Cogerla de ahí evita el «RAM insuficiente (0 GB)».
        total_gb = (self.si.ram_total or vm.total) / 1024**3
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
            raise SinDato("no se han podido enumerar los módulos de memoria")
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
        rated = self.si.ram_speed_rated_mhz
        # Comparar con lo que los módulos dicen soportar detecta el perfil sin
        # activar en cualquier generación. El umbral fijo anterior (2400 MT/s y
        # solo en AMD) valía para DDR4: la base JEDEC de DDR5 ya son 4800, así
        # que un kit DDR5 de 6000 corriendo a 4800 no lo veía nadie.
        if speed and rated and rated > speed * 1.05:
            amd = "AMD" in (self.si.cpu_name or "").upper()
            self.add(
                id="ram_slow",
                title=f"RAM a {speed} MT/s con módulos de {rated} (perfil XMP/EXPO sin activar)",
                severity="medium", category="memoria", component="memory",
                detail=f"Los módulos declaran soportar {rated} MT/s pero están corriendo a {speed}: "
                       "sin el perfil activado, la BIOS arranca a la velocidad JEDEC base. Activarlo "
                       "es gratis y devuelve la velocidad por la que ya has pagado."
                       + (" En Ryzen el impacto en juegos es especialmente alto porque el Infinity "
                          "Fabric escala con la frecuencia de memoria." if amd else ""),
                gain=0.12 if amd else 0.07, gain_note="rendimiento en juegos y latencia",
                effort="bajo", risk="medio",
                steps=["Entra en la BIOS/UEFI (Del o F2 al arrancar)",
                       "Activa el perfil XMP (Intel) o EXPO/DOCP (AMD)",
                       "Guarda y verifica estabilidad con MemTest86 o TestMem5 durante 1 hora",
                       "Si no arranca: borra la CMOS o baja al perfil 2 / velocidad inferior"])
            return f"{len(sticks)} módulos a {speed} MT/s (XMP sin activar)"
        return f"{len(sticks)} módulos" + (f" a {speed} MT/s" if speed else "")

    def _pico_termico(self) -> tuple[float | None, str]:
        """La temperatura más representativa que haya, y de qué momento es.

        Por orden: el muestreo continuo durante la carga, la foto tomada al
        terminar la carga, y como último recurso el reposo de ahora. Las tres
        son medidas reales de un sensor —aquí no se estima nada—; lo que cambia
        es cuándo se tomaron, y por eso la procedencia viaja con la cifra hasta
        el título del hallazgo.

        El muestreo continuo no existe en Windows: `_sample_sensors` se salta
        entero por la frecuencia, que ahí es nominal. La foto sí, y es la misma
        que ya se pinta en el informe y se apunta en el histórico.
        """
        samples = self.bench.thermal_samples if self.bench else []
        if samples:
            return max(samples), "bajo carga"
        fotos = self.bench.load_snapshots if self.bench else []
        # Hacen falta las dos fotos: `run_cpu_multi` toma la de antes y se va sin
        # tomar la de después si el Pool no arranca. Quedarse con la última a
        # ciegas sería llamar «bajo carga» a un reposo previo al benchmark.
        if len(fotos) >= 2 and fotos[-1].get("cpu_temp") is not None:
            return float(fotos[-1]["cpu_temp"]), "bajo carga"
        return cpu_temperature(), "en reposo"

    def check_thermals(self) -> str:
        peak, momento = self._pico_termico()
        gpu = gpu_temperature()

        if peak is None:
            # Sin sensor no hay cifra, pero sí hay diagnóstico: si la CPU mantiene
            # el mismo trabajo por segundo del principio al final de una carga
            # larga, no está limitada térmicamente. Y la GPU casi siempre sí
            # publica su temperatura, que sirve de referencia del interior.
            sustained = (self.bench.metrics.get("sustained") if self.bench else None)
            tried = "; ".join(f"{src}: {res}" for src, res in temperature_report())
            self.notes.append(
                "La temperatura de la CPU no está disponible en este equipo. Windows no la "
                "expone: leerla requiere un driver en modo kernel. Instala y deja abierto "
                "LibreHardwareMonitor (gratuito) y Quilate la tomará automáticamente en la "
                "siguiente ejecución. Fuentes probadas → " + (tried or "ninguna"))
            partes = []
            if gpu is not None:
                partes.append(f"GPU a {gpu:.0f} °C")
            if sustained:
                partes.append(f"rendimiento sostenido {sustained['value']:.0f}%")
                if sustained["value"] < 90:
                    self.add(
                        id="thermal_sustained",
                        title=f"La CPU pierde un {100 - sustained['value']:.0f}% de rendimiento "
                              f"en carga sostenida",
                        severity="high", category="térmico", component="cpu_multi",
                        detail="Las últimas unidades de trabajo tardaron bastante más que las "
                               "primeras con la misma carga. Sin sensor de temperatura no se "
                               "puede confirmar la causa, pero el patrón es el de un límite "
                               "térmico o de potencia: disipación degradada, curva de "
                               "ventiladores demasiado silenciosa o límites PL1/PL2 bajos.",
                        gain=0.15, gain_note="rendimiento en cargas largas",
                        effort="medio", risk="bajo",
                        steps=["Limpia el disipador y los ventiladores con aire comprimido",
                               "Renueva la pasta térmica si el equipo tiene más de 3 años",
                               "Instala LibreHardwareMonitor para confirmarlo con cifras reales",
                               "Revisa los límites de potencia en la BIOS (PL1/PL2, PPT en AMD)"])
            if not sustained:
                # Sin temperatura y sin medida de rendimiento sostenido no hay
                # ninguna base para decir que el equipo no está limitado: la
                # temperatura de la GPU, si la hay, es contexto, no veredicto.
                extra = f" (la GPU marca {gpu:.0f} °C)" if gpu is not None else ""
                raise SinDato("este equipo no expone la temperatura de la CPU y no hay "
                              "medida de rendimiento sostenido con la que deducirlo" + extra)
            return " · ".join(partes)

        if gpu is not None:
            self.notes.append(f"Temperatura de GPU durante las pruebas: {gpu:.0f} °C "
                              f"(fuente: nvidia-smi).")
        if peak >= 95:
            self.add(
                id="thermal_critical", title=f"Throttling térmico severo ({peak:.0f} °C {momento})",
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
                id="thermal_high", title=f"Temperaturas elevadas ({peak:.0f} °C {momento})",
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
        source = temperature_source()
        return f"{peak:.0f} °C (correcto)" + (f" · {source}" if source else "")

    def check_large_files(self) -> str:
        """Espacio recuperable según el rastreo de archivos grandes."""
        scan = self.scan
        if scan is None or not scan.available:
            raise NoAplica("rastreo de archivos no ejecutado")
        if not scan.files and not scan.special:
            return f"nada por encima de {human_bytes(scan.min_size)}"

        system_free = next((d["free"] for d in local_volumes(self.si)
                            if d["mount"].upper().startswith(str(self.si.system_drive)[:1].upper())),
                           0)
        recoverable, review = candidate_bytes(scan)
        candidates = recoverable + review

        top = ", ".join(f"{f['name'][:38]} ({human_bytes(f['size'])})" for f in scan.files[:3])
        if candidates >= 5 * 1024**3 or (recoverable >= 2 * 1024**3 and system_free
                                         and candidates > system_free * 0.1):
            severity = "medium" if candidates < 20 * 1024**3 else "high"
            self.add(
                id="large_files",
                title=f"{human_bytes(candidates)} en archivos grandes prescindibles",
                severity=severity, category="almacenamiento", component="disk",
                detail=f"El rastreo encontró {human_bytes(scan.total_large)} en ficheros de más de "
                       f"{human_bytes(scan.min_size)}, de los cuales {human_bytes(candidates)} son "
                       "temporales, cachés, volcados, instaladores ya usados o copias antiguas. "
                       "En un SSD el espacio libre no es solo capacidad: por debajo del 20% la "
                       f"velocidad de escritura cae. Los más grandes: {top}.",
                gain=0.06, gain_note="margen de escritura del SSD",
                effort="bajo", risk="bajo",
                steps=["Revisa la lista de la sección «Archivos grandes» antes de borrar nada",
                       "Ejecuta `cleanmgr /sageset:1` y marca temporales, volcados y Windows Update",
                       "Configuración → Sistema → Almacenamiento → Sensor de almacenamiento",
                       "Los instaladores y comprimidos ya usados se pueden volver a descargar",
                       "Mueve vídeos y copias a otra unidad en lugar de borrarlos"])
        note = f"{human_bytes(scan.total_large)} en {len(scan.files)} ficheros grandes"
        if candidates:
            note += f" · {human_bytes(candidates)} prescindibles"
        if scan.truncated:
            note += " (rastreo parcial)"
        return note

    def check_cpu_frequency(self) -> str:
        """Frecuencia real bajo carga frente al reloj base.

        La referencia es el reloj BASE, no un supuesto máximo: `MaxClockSpeed` de
        Win32_Processor devuelve la base (3701 MHz en un 5900X que llega a 4.9
        GHz), así que compararla consigo misma daba siempre «100% del máximo».
        Lo que sí significa algo es quedarse por debajo de la base con todos los
        núcleos cargados: eso es límite de potencia o térmico.
        """
        base = self.si.cpu_base_mhz or self.si.cpu_max_mhz
        sustained = self.bench.freq_under_load if self.bench else None
        nominal = bool(self.bench and "nominal" in self.bench.freq_source)
        if sustained is None:
            samples = self.bench.freq_samples if self.bench else []
            sustained = statistics.median(samples) if samples else None
        if not sustained or not base:
            raise SinDato("no hay medida de frecuencia bajo carga"
                          if not sustained else "se desconoce el reloj base")
        if nominal:
            # El sistema no expone la frecuencia real: la cifra es la de catálogo
            # y compararla consigo misma daba siempre «100% del máximo».
            raise SinDato(f"este equipo solo expone la frecuencia nominal ({sustained:.0f} MHz), "
                          "que no dice nada sobre la real")

        ratio = sustained / base
        if ratio < 0.85:
            self.add(
                id="freq_low", title=f"CPU al {ratio * 100:.0f}% de su frecuencia base bajo carga",
                severity="high", category="cpu", component="cpu_multi",
                detail=f"{sustained:.0f} MHz con todos los núcleos cargados, por debajo de los "
                       f"{base:.0f} MHz de reloj base. Un procesador sano se mantiene en la base o "
                       "por encima gracias al boost. Causas habituales: plan de energía en modo "
                       "ahorro, límites de potencia (PL1/PL2 o TDP del portátil), batería, o "
                       "throttling térmico.",
                gain=0.25, gain_note="rendimiento de CPU",
                effort="bajo", risk="bajo",
                steps=["Pon el plan de energía en Alto rendimiento y conecta el portátil a la red",
                       "Revisa el modo de rendimiento del fabricante (Lenovo Vantage, MyASUS, Armoury Crate…)",
                       "Comprueba temperaturas: si superan 90 °C, es un problema térmico",
                       "En portátiles: revisa que el cargador sea el original y con potencia suficiente"])
            return f"{sustained:.0f} MHz bajo carga ({ratio * 100:.0f}% de la base)"
        return (f"{sustained:.0f} MHz bajo carga · base {base:.0f} MHz "
                f"({ratio * 100:.0f}%)")

    def check_startup(self) -> str:
        items: list[dict] = []
        if not (IS_WINDOWS and winreg is not None):
            raise NoAplica("el inicio con Windows solo existe en Windows")
        leidas = 0
        for hive, path, bucket in (
            (winreg.HKEY_CURRENT_USER,
             r"Software\Microsoft\Windows\CurrentVersion\Run", "Run"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"Software\Microsoft\Windows\CurrentVersion\Run", "Run"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "Run32"),
        ):
            leidas += reg_key_readable(hive, path)
            scope = "usuario" if hive == winreg.HKEY_CURRENT_USER else "equipo"
            for name in reg_list_values(hive, path):
                items.append({"name": name, "location": f"Run ({scope})",
                              "enabled": self._startup_enabled([(hive, bucket)], name)})
        items += self._startup_folder_items()
        seen: set[tuple] = set()
        unique = []
        for it in sorted(items, key=lambda i: i["name"].lower()):
            key = (it["name"].lower(), it["location"])
            if key not in seen:
                seen.add(key)
                unique.append(it)
        items = unique
        self.startup_items = items
        if not leidas:
            # Las tres claves Run existen en cualquier Windows. Si ninguna se
            # deja abrir, «0 programas de inicio» no es un elogio al equipo.
            raise SinDato("no se ha podido abrir ninguna clave Run del registro")
        enabled = [i["name"] for i in items if i["enabled"]]
        count = len(enabled)
        off = len(items) - count
        if count >= 12:
            sev, gain = ("high", 0.35) if count >= 20 else ("medium", 0.22)
            self.add(
                id="startup_bloat", title=f"{count} programas configurados para iniciarse con Windows",
                severity=sev, category="arranque", component="system",
                detail="Cada entrada compite por CPU y, sobre todo, por E/S de disco durante el "
                       "arranque. Es la causa número uno de «el PC tarda mucho en estar usable». "
                       f"Activos: {', '.join(enabled[:10])}"
                       f"{'…' if count > 10 else ''}."
                       + (f" No se cuentan {off} entradas ya desactivadas." if off else ""),
                gain=gain, gain_note="tiempo hasta escritorio usable",
                effort="bajo", risk="nulo",
                steps=["Ctrl+Shift+Esc → pestaña Inicio: ordena por «Impacto de inicio» y desactiva lo de impacto Alto",
                       "Desactiva actualizadores (Adobe, Java, iTunes), launchers de juegos y clientes de nube que no uses a diario",
                       "No desactives: antivirus, drivers de audio, software de touchpad ni utilidades del fabricante esenciales",
                       "Revisa también Programador de tareas → tareas con disparador «Al iniciar sesión»"])
            return f"{count} entradas de inicio activas" + (f" (+{off} desactivadas)" if off else "")
        return (f"{count} entradas de inicio activas (razonable)"
                + (f", {off} desactivadas" if off else ""))

    # Task Manager no borra el valor de Run al desactivar una app: deja la entrada
    # y anota el estado en StartupApproved. Sin mirar ahí, todo lo que el usuario
    # ha ido desactivando sigue contando como si arrancara con Windows.
    _APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"

    def _startup_enabled(self, buckets: list[tuple], name: str) -> bool:
        """El primer byte del blob lleva el bit 0 a 1 cuando está desactivada
        (0x03, 0x07); 0x02 y 0x06 son activas. Sin blob, Windows la ejecuta."""
        for hive, bucket in buckets:
            raw = reg_read(hive, f"{self._APPROVED}\\{bucket}", name)
            if isinstance(raw, (bytes, bytearray)) and raw:
                return not raw[0] & 1
        return True

    def _startup_folder_items(self) -> list[dict]:
        """Accesos directos de las carpetas Inicio. Van por WMI porque la ruta
        depende del perfil; su estado está en StartupApproved\\StartupFolder,
        con el nombre del .lnk. Las entradas de registro que devuelve la misma
        consulta se ignoran: ya se leen del registro, y ahí llegan duplicadas
        por cada perfil de usuario (incluido .DEFAULT)."""
        data = ps_json("Get-CimInstance Win32_StartupCommand | Select-Object Name,Location",
                       timeout=25)
        if not getattr(data, "ok", True):
            self.notes.append("No se han podido leer las carpetas de Inicio "
                              f"({data.error}): el recuento de programas de arranque "
                              "solo cubre las entradas del registro.")
        hives = [(winreg.HKEY_CURRENT_USER, "StartupFolder"),
                 (winreg.HKEY_LOCAL_MACHINE, "StartupFolder")]
        out = []
        for d in data:
            name = str(d.get("Name") or "").strip()
            location = str(d.get("Location") or "")
            # Todo lo que no sea una ruta de registro es una carpeta de inicio.
            if not name or location.lower().startswith("hk"):
                continue
            out.append({"name": name, "location": "Carpeta Inicio",
                        "enabled": self._startup_enabled(hives, f"{name}.lnk")})
        return out

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
            raise SinDato("no se ha podido leer la fecha de instalación del sistema")
        years = age / 365.25
        startup_count = sum(1 for i in getattr(self, "startup_items", []) if i.get("enabled"))
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

    def check_uptime(self) -> str:
        """Tiempo sin reiniciar.

        En Windows la trampa es que «Apagar» no reinicia el kernel: con el inicio
        rápido activado guarda la sesión hibernada y el contador sigue subiendo.
        Por eso mucha gente cree que apaga a diario y lleva semanas sin reiniciar
        de verdad, acumulando fugas de memoria y actualizaciones a medio aplicar.
        """
        hours = self.si.uptime_hours
        if not hours:
            # Un equipo encendido cero horas no existe: es que no se ha leído.
            raise SinDato("no se ha podido leer el tiempo desde el último arranque")
        days = hours / 24
        if days >= 14:
            severity, gain = ("high", 0.14) if days >= 30 else ("medium", 0.10)
            self.add(
                id="uptime_long", title=f"{days:.0f} días sin reiniciar el equipo",
                severity=severity, category="fluidez", component="system",
                detail=f"El sistema lleva {days:.0f} días encendido ({hours:.0f} h). Con el tiempo "
                       "se acumulan fugas de memoria de aplicaciones de larga vida, controladores "
                       "que no liberan recursos, actualizaciones pendientes de aplicar y "
                       "fragmentación del espacio de trabajo. Reiniciar es la optimización más "
                       "barata que existe. Ojo: con el inicio rápido activado, «Apagar» no "
                       "reinicia el kernel y este contador no se pone a cero; hay que usar "
                       "«Reiniciar».",
                gain=gain, gain_note="fluidez y memoria disponible",
                effort="bajo", risk="nulo",
                steps=["Usa Inicio → Reiniciar (no «Apagar»: con inicio rápido no es lo mismo)",
                       "Aprovecha para instalar las actualizaciones pendientes",
                       "Si lo dejas encendido por descargas, programa un reinicio semanal"])
            return f"{days:.0f} días encendido"
        if days >= 7:
            return f"{days:.0f} días encendido (empieza a ser mucho)"
        return f"{hours:.0f} h encendido"

    def check_gpu_drivers(self) -> str:
        if not self.si.gpus:
            raise SinDato("no se ha detectado ningún adaptador gráfico")

        newer = self._newer_gpu_driver()
        if newer:
            self.add(
                id="gpu_driver_new", title="Hay un driver gráfico más nuevo disponible",
                severity="medium", category="fluidez", component="system",
                detail="Windows Update ofrece una actualización de controlador de vídeo "
                       f"pendiente de instalar: {newer}. Los drivers de GPU traen optimizaciones "
                       "por juego y correcciones de rendimiento. Nota: la web del fabricante "
                       "suele ir por delante de Windows Update, así que compara antes de decidir "
                       "de dónde instalarlo.",
                gain=0.10, gain_note="rendimiento gráfico (variable por título)",
                effort="bajo", risk="bajo",
                steps=["Descárgalo de nvidia.com / amd.com / intel.com: suele ser más reciente",
                       "En NVIDIA elige instalación personalizada → limpia",
                       "O instálalo desde Configuración → Windows Update → Opciones avanzadas → "
                       "Actualizaciones opcionales"])
            return f"hay uno más nuevo: {newer[:40]}"

        # Solo cuenta la gráfica que mueve la pantalla. Un equipo con GPU dedicada
        # suele arrastrar además la iGPU del procesador, con un driver de hace
        # años porque no se usa: culpar a esa desvía la recomendación de la que
        # de verdad importa.
        target = primary_gpu(self.si.gpus) or {}
        age = target.get("driver_age_days")
        if age is None:
            raise SinDato(f"{target.get('name') or 'la GPU'} no informa de la fecha de su driver")
        # Los fabricantes publican drivers cada 4-6 semanas, así que la
        # antigüedad es un indicador razonable cuando no se puede consultar.
        if age > 365:
            severity, gain = "medium", 0.10
        elif age > 180:
            severity, gain = "low", 0.06
        else:
            return f"{target['name']}: al día ({age} días)"
        self.add(
            id="gpu_driver", title=f"{target['name']}: driver con {age} días de antigüedad",
            severity=severity, category="fluidez", component="system",
            detail=f"{target['name']} usa un driver de {target.get('driver_date')}. Los "
                   "fabricantes publican versiones cada 4-6 semanas con optimizaciones "
                   "específicas por juego y correcciones de rendimiento; es una de las "
                   "actualizaciones con mejor relación beneficio/esfuerzo. No se ha podido "
                   "comprobar en línea si hay uno más nuevo: ejecuta con --check-drivers "
                   "para consultarlo.",
            gain=gain, gain_note="rendimiento gráfico (variable por título)",
            effort="bajo", risk="bajo",
            steps=["Descarga el driver desde nvidia.com / amd.com / intel.com, no desde Windows Update",
                   "En NVIDIA elige instalación personalizada → limpia",
                   "Si vienes de otra marca de GPU, limpia primero con DDU en modo seguro"])
        return f"{target['name']}: driver de {target.get('driver_date')} ({age} días)"


    def _newer_gpu_driver(self) -> str | None:
        """Título de la actualización de driver gráfico pendiente, si se pidió
        comprobarlo. La consulta a Windows Update tarda 10-30 s, así que solo se
        hace con --check-drivers."""
        if not self.check_drivers or not IS_WINDOWS:
            return None
        pending = pending_driver_updates()
        for title in pending:
            lowered = title.lower()
            if any(k in lowered for k in ("nvidia", "geforce", "radeon", "amd ", "intel(r) "
                                          "graphics", "intel corporation - display", "display",
                                          "gráfic", "graphics")):
                return title
        return None

    def check_device_problems(self) -> str:
        """Los dispositivos con el signo de exclamación amarillo.

        Se pregunta a `Win32_PnPEntity` filtrando en la propia consulta, y no a
        `Get-PnpDevice -Status Error,Degraded` como parecería natural: verificado
        ejecutándolo, ese cmdlet lanza excepción cuando NO hay ninguno («No
        Win32_PnPEntity objects found with property 'Status' equal to 'Error'»).
        Con él, un equipo sano y una consulta que falla llegan aquí iguales, y el
        equipo sano acabaría contado como «Sin comprobar». El filtro WQL devuelve
        lista vacía, que es la respuesta correcta y además cuesta 0,26 s.
        """
        rows = ps_json("Get-CimInstance Win32_PnPEntity "
                       "-Filter 'ConfigManagerErrorCode <> 0' | "
                       "Select-Object Name,PNPClass,ConfigManagerErrorCode")
        if not rows.ok:
            raise SinDato("no se ha podido consultar el estado de los dispositivos"
                          f" ({rows.error})")

        averiados: list[tuple[str, str]] = []
        deliberados = 0
        for row in rows:
            codigo = row.get("ConfigManagerErrorCode")
            # `isinstance(True, int)` es cierto en Python, y un booleano aquí
            # significaría que la consulta devolvió otra cosa, no un código.
            if isinstance(codigo, bool) or not isinstance(codigo, int) or codigo == 0:
                continue
            if codigo in _PNP_DELIBERADO:
                deliberados += 1
                continue
            nombre = str(row.get("Name") or row.get("PNPClass") or "dispositivo sin nombre")
            # Un código que no está en la tabla sigue siendo un problema: lo que
            # no se sabe es cuál, y decir el número permite buscarlo.
            averiados.append((nombre, _PNP_PROBLEMA.get(
                codigo, f"Windows le ha asignado el código de problema {codigo}")))

        cola = f", {deliberados} apagado(s) a propósito" if deliberados else ""
        if not averiados:
            return f"ninguno con problema{cola}"

        # Con muchos dispositivos rotos la lista completa no cabe en un título ni
        # ayuda: los primeros bastan para reconocer de qué se está hablando.
        visibles = averiados[:4]
        detalle = ". ".join(f"«{n}»: {m}" for n, m in visibles)
        if len(averiados) > len(visibles):
            detalle += f". Y {len(averiados) - len(visibles)} más"
        titulo = (f"«{averiados[0][0]}» no está funcionando" if len(averiados) == 1
                  else f"{len(averiados)} dispositivos no están funcionando")
        self.add(
            id="dispositivo_con_error", title=titulo,
            severity="medium", category="dispositivos", component="system",
            detail=f"{detalle}. Es el signo de exclamación amarillo del Administrador de "
                   "dispositivos: hardware que está montado en el equipo y que Windows ha "
                   "dado por imposible. No se nota como lentitud, se nota como algo que "
                   "sencillamente no va —un lector de tarjetas, el Bluetooth, un puerto— y "
                   "que casi nadie relaciona con un driver porque nadie abre esa ventana.",
            gain=0.0, gain_note="no es una optimización: es hardware que no funciona",
            effort="bajo", risk="bajo",
            steps=["Abre el Administrador de dispositivos (`devmgmt.msc`) y busca el aviso amarillo",
                   "Botón derecho → Actualizar controlador → Buscar automáticamente",
                   "Si no lo encuentra, busca el modelo en la web del fabricante de la placa "
                   "o del portátil: ahí están los drivers que Windows Update no trae",
                   "Si el dispositivo ya no está conectado, botón derecho → Desinstalar"])
        return f"{len(averiados)} con problema{cola}"

    def check_old_drivers(self) -> str:
        """Drivers de terceros que nadie ha vuelto a tocar.

        La regla que parecería natural —«driver anterior a la instalación del
        SO»— es inservible, y está medido: en este equipo la cumplen 209 de 239
        drivers. Microsoft fecha a propósito los suyos en 21/06/2006 para que
        cualquier driver del fabricante gane siempre la resolución de PnP, así
        que `WAN Miniport (IP)` y compañía son de 2006 y están perfectos. La
        señal aparece al quedarse solo con los de terceros: 39 de esos 239.

        La fecha también está en el registro (`Control\\Class\\{clase}\\NNNN`),
        y leerla de ahí cuesta 0,02 s frente a los 2,5 s de WMI. Se descartó
        porque el registro conserva los drivers de dispositivos que ya no están
        conectados —aquí saca uno de Apple de 2013, resto de un iTunes— y este
        hallazgo dice «actualiza esto», que sobre algo desenchufado no se puede
        hacer. WMI enumera dispositivos presentes, que es justo lo que afirma.

        Se paga: `Win32_PnPSignedDriver` comprueba la firma de cada driver, y eso
        cuesta 2 s con la caché caliente y hasta 11 s en frío, medido aquí. Es la
        comprobación más cara de las 34, y está asumido a cambio de no acusar a
        nadie de tener viejo el driver de algo que ya no tiene enchufado.
        """
        rows = ps_json("Get-CimInstance Win32_PnPSignedDriver "
                       "-Filter \"NOT DriverProviderName LIKE 'Microsoft%'\" | "
                       "Select-Object DeviceName,DriverDate,DriverProviderName,DeviceClass")
        if not rows.ok:
            raise SinDato(f"no se han podido consultar los drivers ({rows.error})")
        if not rows:
            # Un Windows recién instalado en hardware corriente funciona entero
            # con drivers de caja. No hay nada que auditar, y eso no es un dato
            # que falte: es la respuesta.
            return "no hay drivers de terceros"

        hoy = datetime.now()
        # Un mismo teclado aparece una vez por cada función que expone (HID,
        # ratón, teclado…), a veces con drivers de fechas distintas. Se queda la
        # más antigua: es la que dice desde cuándo no se toca ese dispositivo.
        por_dispositivo: dict[str, int] = {}
        ilegibles = 0
        for row in rows:
            # La gráfica ya la audita `check_gpu_drivers`, con su fecha y su
            # ganancia. Repetirla aquí sería el mismo aviso dos veces.
            if str(row.get("DeviceClass") or "").upper() == "DISPLAY":
                continue
            fecha = _parse_cim_date(row.get("DriverDate"))
            nombre = str(row.get("DeviceName") or "").strip()
            if fecha is None or not nombre:
                ilegibles += 1
                continue
            dias = (hoy - fecha).days
            # Hay drivers fechados en el futuro, y no es un error del fabricante
            # sino de la máquina que los firmó. Uno así no es viejo.
            if dias < 0:
                continue
            por_dispositivo[nombre] = max(por_dispositivo.get(nombre, 0), dias)

        if not por_dispositivo:
            # Quedarse sin ninguno no significa siempre lo mismo. Si es porque
            # nadie dijo su fecha, falta el dato y hay que decirlo. Si es porque
            # los que había estaban excluidos con motivo —la gráfica, que audita
            # otra comprobación; una fecha futura—, la respuesta es que no hay
            # nada que auditar, y eso sí es una respuesta.
            if ilegibles:
                raise SinDato("ningún driver de terceros ha informado de su fecha")
            return "no hay drivers de terceros que auditar"
        viejos = sorted(((n, d) for n, d in por_dispositivo.items()
                         if d > _DRIVER_VIEJO_DIAS), key=lambda x: -x[1])
        if not viejos:
            return f"{len(por_dispositivo)} de terceros, ninguno de más de 5 años"

        def años(dias: int) -> int:
            return round(dias / 365.25)

        visibles = viejos[:4]
        lista = ", ".join(f"{n} ({años(d)} años)" for n, d in visibles)
        if len(viejos) > len(visibles):
            lista += f" y {len(viejos) - len(visibles)} más"
        titulo = (f"{viejos[0][0]}: driver de hace {años(viejos[0][1])} años"
                  if len(viejos) == 1
                  else f"{len(viejos)} dispositivos con drivers de más de 5 años")
        self.add(
            id="driver_viejo", title=titulo,
            severity="low", category="dispositivos", component="system",
            detail=f"{lista}. Son drivers del fabricante del dispositivo, no de Windows: "
                   "Windows Update casi nunca los renueva y quedan como estaban el día que "
                   "se instalaron. Pasados unos años eso significa fallos conocidos sin "
                   "corregir y, en los que manejan datos —red, audio, almacenamiento—, "
                   "también agujeros de seguridad que ya tienen parche publicado. No es "
                   "urgente y no va a acelerar el equipo; es mantenimiento pendiente.",
            gain=0.0, gain_note="no es una optimización: es mantenimiento",
            effort="bajo", risk="bajo",
            steps=["Busca el modelo del equipo o de la placa en la web del fabricante y "
                   "mira su sección de soporte: ahí está lo que Windows Update no trae",
                   "Actualiza primero los que manejan datos: red, audio y almacenamiento",
                   "Un driver de hace años que funciona bien puede quedarse: si el "
                   "dispositivo no da problemas, esto no corre prisa"])
        return f"{len(viejos)} de más de 5 años, de {len(por_dispositivo)} de terceros"

    def check_network_link(self) -> str:
        """Lo que el enlace está haciendo frente a lo que la tarjeta sabe hacer.

        Es el mismo patrón que la RAM a velocidad JEDEC o el driver de la GPU
        parada: el hardware es capaz de más y nadie lo mira nunca.
        """
        datos = self.network or {}
        conectados = datos.get("connected") or []
        if not conectados:
            raise SinDato("no hay ningún adaptador de red conectado, o no se han "
                          "podido enumerar")

        wifi = datos.get("wifi") or {}
        partes = []
        for adaptador in conectados:
            mbps = adaptador.get("link_mbps") or 0
            nombre = adaptador["name"]
            descripcion = adaptador["description"]

            if adaptador.get("wireless"):
                capaz = wifi_capability(descripcion)
                radio = wifi.get("radio")
                partes.append(f"{nombre} {radio or 'wifi'} a {mbps:.0f} Mbps"
                              if mbps else f"{nombre} {radio or 'wifi'}")
                if capaz and radio and WIFI_TECHO.get(capaz, 0) > WIFI_TECHO.get(radio, 0):
                    techo = WIFI_TECHO[capaz]
                    self.add(
                        id="wifi_downgrade",
                        title=f"Tarjeta {capaz} conectada en {radio}",
                        severity="medium", category="fluidez", component="system",
                        detail=f"«{descripcion}» soporta {capaz} (hasta ~{techo} Mbps) pero "
                               f"el enlace actual es {radio}"
                               + (f", a {mbps:.0f} Mbps." if mbps else ".")
                               + " Casi siempre es el router: o no tiene esa generación, o "
                                 "la tiene desactivada, o la banda a la que te has conectado "
                                 "no la ofrece. Se nota sobre todo al copiar archivos por red "
                                 "y en la latencia de los juegos.",
                        gain=0.10, gain_note="velocidad y latencia de red",
                        effort="bajo", risk="nulo",
                        steps=["Comprueba en el router que la generación esté activada "
                               "(Wi-Fi 6 / 802.11ax, o Wi-Fi 7 / be)",
                               "Conéctate a la banda de 5 o 6 GHz, no a la de 2,4",
                               "Wi-Fi 6E y 7 necesitan WPA3: con WPA2 se cae a la generación anterior",
                               "Actualiza el driver de la tarjeta desde la web del fabricante"])
                if wifi.get("rssi_dbm") is not None and wifi["rssi_dbm"] <= -70:
                    self.add(
                        id="wifi_signal",
                        title=f"Señal wifi débil ({wifi['rssi_dbm']} dBm)",
                        severity="medium", category="fluidez", component="system",
                        detail="Por debajo de -70 dBm el enlace baja de velocidad y empieza a "
                               "reintentar paquetes, lo que se percibe como tirones y no como "
                               "lentitud. Es un problema de distancia, obstáculos o canal, no "
                               "de la tarjeta.",
                        gain=0.08, gain_note="estabilidad de la conexión",
                        effort="bajo", risk="nulo",
                        steps=["Acerca el equipo al router o cambia su orientación",
                               "Prueba la banda de 5 GHz si estás en 2,4 (o al revés si "
                               "hay paredes de por medio)",
                               "Cambia el canal del router si hay muchas redes vecinas",
                               "Un repetidor o un punto de acceso por cable resuelve el caso difícil"])
                if wifi.get("band_ghz") == "2.4" and capaz in ("802.11ac", "802.11ax", "802.11be"):
                    self.add(
                        id="wifi_banda", title="Conectado a la banda de 2,4 GHz",
                        severity="low", category="fluidez", component="system",
                        detail="La banda de 2,4 GHz va más lejos pero es mucho más lenta y la "
                               "comparten microondas, mandos y las redes de los vecinos. La "
                               "tarjeta soporta 5 GHz.",
                        gain=0.06, gain_note="velocidad de red",
                        effort="bajo", risk="nulo",
                        steps=["Conéctate a la red de 5 GHz (suele tener el mismo nombre con «_5G»)",
                               "Si el router usa un único nombre para las dos, mira su opción "
                               "de «banda preferida»"])
                continue

            # Cable: un adaptador gigabit a 100 Mbps casi siempre es el cable.
            gigabit = re.search(r"gigabit|gbe|2\.5g|\b1000\b", descripcion, re.I)
            partes.append(f"{nombre} a {mbps:.0f} Mbps" if mbps else nombre)
            if gigabit and 0 < mbps <= 100:
                self.add(
                    id="ethernet_lento",
                    title=f"Adaptador gigabit negociando a {mbps:.0f} Mbps",
                    severity="high", category="fluidez", component="system",
                    detail=f"«{descripcion}» admite 1000 Mbps y ha negociado {mbps:.0f}. La causa "
                           "casi siempre es física: un cable de categoría 5 sin más, un cable "
                           "dañado o con un par suelto, o un switch antiguo por el camino. No "
                           "es un ajuste de Windows.",
                    gain=0.15, gain_note="velocidad de red local",
                    effort="bajo", risk="nulo",
                    steps=["Cambia el cable por uno Cat 5e o Cat 6 y prueba de nuevo",
                           "Prueba otro puerto del router o del switch",
                           "Comprueba que ningún equipo intermedio sea de 100 Mbps",
                           "Revisa que la velocidad del adaptador esté en «Negociación automática»"])
        return " · ".join(partes) if partes else "conectado"

    def check_network_latency(self) -> str:
        """Latencia y resolución DNS. Solo si se han pedido las sondas."""
        datos = self.network or {}
        if not datos.get("active"):
            raise NoAplica("se desactivaron las sondas de red con --no-net")
        latencia = datos.get("latency") or {}
        dns = datos.get("dns") or {}
        if not latencia.get("reachable"):
            raise SinDato("ningún destino ha respondido: sin conexión a internet, "
                          "o un cortafuegos bloquea las sondas")

        mejor = latencia["best_ms"]
        perdida = max((t["loss_pct"] for t in latencia["targets"]), default=0)
        partes = [f"{mejor:.0f} ms al mejor destino"]

        if dns.get("median_ms"):
            partes.append(f"DNS {dns['median_ms']:.0f} ms")
            if dns["median_ms"] >= 120:
                self.add(
                    id="dns_lento", title=f"Resolución DNS lenta ({dns['median_ms']:.0f} ms)",
                    severity="medium", category="fluidez", component="system",
                    detail="Cada dominio nuevo de cada página espera esto antes de que empiece "
                           "a descargarse nada. Se percibe como «internet va lento» aunque el "
                           "ancho de banda esté perfecto. El resolutor del operador suele ser "
                           "el culpable.",
                    gain=0.07, gain_note="tiempo hasta que una página empieza a cargar",
                    effort="bajo", risk="bajo",
                    steps=["Prueba un resolutor público: 1.1.1.1 (Cloudflare) o 8.8.8.8 (Google)",
                           "Configúralo en el router para que valga en toda la casa",
                           "Vacía la caché con `ipconfig /flushdns` tras cambiarlo"])

        if perdida >= 25:
            partes.append(f"{perdida}% de pérdida")
            self.add(
                id="red_perdida", title=f"Pérdida de paquetes del {perdida}%",
                severity="high", category="fluidez", component="system",
                detail="Los intentos de conexión se están quedando sin respuesta. En una "
                       "conexión sana esto es cero. Con wifi suele ser cobertura o "
                       "interferencia; con cable, el cable o el router.",
                gain=0.12, gain_note="estabilidad de la conexión",
                effort="medio", risk="nulo",
                steps=["Repite la prueba con cable para descartar el wifi",
                       "Reinicia el router y comprueba si mejora",
                       "Si persiste con cable, es cosa de la línea: avisa al operador"])
        elif mejor >= 80:
            partes.append("latencia alta")
            self.add(
                id="latencia_alta", title=f"Latencia alta hasta internet ({mejor:.0f} ms)",
                severity="medium", category="fluidez", component="system",
                detail="Es el tiempo mínimo de ida y vuelta hasta un servidor cercano. Por "
                       "encima de 80 ms se nota en juegos y videollamadas. Puede ser el wifi, "
                       "el router saturado o la propia línea.",
                gain=0.08, gain_note="respuesta en juegos y videollamadas",
                effort="medio", risk="nulo",
                steps=["Compara con cable: si baja mucho, el problema es el wifi",
                       "Mira si hay descargas o copias de seguridad en marcha",
                       "Activa la gestión de cola (SQM/QoS) en el router si la tiene"])
        return " · ".join(partes)

    # ------------------------------------------------------- solo Windows ----
    def check_power_plan(self) -> str:
        out = run_cmd([_sys_exe("powercfg.exe"), "/getactivescheme"], timeout=15)
        if not out.ok:
            raise SinDato(f"no se ha podido preguntar el plan de energía: {out.error}")
        name = out.split("(", 1)[1].rstrip(")").strip() if "(" in out else str(out)
        if not name.strip():
            # Sin nombre de plan, la comparación contra la lista de nombres
            # «buenos» siempre fallaba y el hallazgo se emitía con el plan
            # vacío: «Plan de energía en «»».
            raise SinDato("powercfg no ha devuelto el plan activo")
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
            raise NoAplica("el disco de sistema no es un SSD")
        out = run_cmd([_sys_exe("fsutil.exe"), "behavior", "query", "DisableDeleteNotify"],
                      timeout=15)
        if not out.ok:
            raise SinDato(f"no se ha podido leer el estado de TRIM: {out.error}")
        if not out.strip():
            raise SinDato("fsutil no ha devuelto el estado de TRIM")
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
        if not self.si.gpus and self.bench is None:
            # El consejo depende de si al equipo le cuesta mover la interfaz.
            # Sin GPU identificada ni puntuación, recomendarlo sería a ciegas.
            raise SinDato("sin datos de GPU ni benchmark con los que juzgar si compensa")
        weak_gpu = any("intel" in (g.get("name") or "").lower() and "arc" not in (g.get("name") or "").lower()
                       for g in self.si.gpus)
        low_score = bool(self.bench and self.bench.overall() < 65)
        # `pref` ausente (None) es el estado por defecto de Windows —«deja que
        # Windows elija»—, que sí deja las animaciones puestas.
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
        if not getattr(data, "ok", True):
            # Sin esta distinción, una consulta fallida se leía como «no hay
            # archivo de paginación» y se emitía el hallazgo igualmente.
            raise SinDato(f"no se ha podido consultar el archivo de paginación ({data.error})")
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
        if val is None:
            # Ese valor existe en cualquier Windows moderno: si no está, lo que
            # ha fallado es la lectura, no el inicio rápido.
            raise SinDato("no se ha podido leer HiberbootEnabled")
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
        if not getattr(rows, "ok", True) or not rows:
            raise SinDato("no se ha podido enumerar los servicios"
                          + (f" ({rows.error})" if getattr(rows, "error", None) else ""))
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
        gamedvr = r"Software\Microsoft\Windows\CurrentVersion\GameDVR"
        if reg_read(winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Policies\Microsoft\Windows\GameDVR", "AllowGameDVR") == 0:
            return "desactivada por directiva"
        dvr = reg_read(winreg.HKEY_CURRENT_USER, r"System\GameConfigStore", "GameDVR_Enabled")
        capture = reg_read(winreg.HKEY_CURRENT_USER, gamedvr, "AppCaptureEnabled")
        historico = reg_read(winreg.HKEY_CURRENT_USER, gamedvr, "HistoricalCaptureEnabled")
        if dvr is None and capture is None and historico is None:
            # Esos valores existen en cualquier perfil de Windows 10/11. Que no
            # esté ninguno significa que no se ha podido leer la rama, no que la
            # captura esté apagada.
            raise SinDato("no se ha podido leer la configuración de Game Bar")
        if dvr == 0 or capture == 0:
            return "desactivada"
        # Lo que cuesta FPS es el búfer permanente de «Grabar lo que ha pasado»,
        # no que la Game Bar pueda grabar si se lo pides. Es opt-in y avisa de que
        # consume recursos, así que si el valor no existe está apagada. GameDVR_Enabled
        # y AppCaptureEnabled valen 1 en una instalación recién hecha: mirarlos a ellos
        # marcaba el hallazgo en prácticamente cualquier equipo.
        if historico == 1:
            self.add(
                id="game_dvr", title="Grabación en segundo plano de Xbox Game Bar activa",
                severity="low", category="fluidez", component="system",
                detail="La captura en segundo plano mantiene un búfer de vídeo constante. El coste "
                       "típico es de un 2-6% de FPS y algo de latencia añadida. Si no usas la "
                       "función de «grabar los últimos 30 segundos», no aporta nada.",
                gain=0.04, gain_note="FPS en juegos",
                effort="bajo", risk="nulo",
                steps=["Configuración → Juegos → Capturas: desactiva «Grabar lo que ha pasado»",
                       "Configuración → Juegos → Xbox Game Bar: desactivar si no la usas",
                       "Activa el Modo de juego (sí conviene tenerlo activado)"])
            return "activa"
        return "sin grabación en segundo plano"

    @staticmethod
    def _event_ms(fields: dict, *names: str) -> float | None:
        """Primer campo del evento que traiga un número de milisegundos."""
        for name in names:
            raw = fields.get(name)
            if raw in (None, ""):
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    def check_boot_time(self) -> str:
        """Duración real del arranque, medida por Windows en cada encendido.

        Es la única fuente que convierte «tienes muchos programas de inicio» en
        una cifra: el propio sistema apunta cuánto tardó y qué lo retrasó.
        """
        # Ya no se mira `si.is_admin`: este proceso no se eleva nunca, así que
        # eso diría siempre «no» aunque el lote con permisos haya contestado.
        # Lo que decide es si el dato está, que es la pregunta de verdad.
        data = boot_performance(elevacion.recoger()["arranque"])
        self.boot_report = data
        if data.get("error"):
            # El log tiene una ACL propia, y con -FilterHashtable el error que
            # devuelve PowerShell es «no se encontraron eventos», que se lee
            # como «tu arranque está limpio».
            raise SinDato(f"no se ha podido leer el registro de arranque "
                          f"({data['error'][:60]})")

        # Los reinicios de instalación de actualizaciones siempre son lentos y no
        # representan el arranque de cada día: no entran en la mediana.
        tiempos = []
        for boot in data["boots"]:
            fields = boot["fields"]
            if str(fields.get("BootIsRebootAfterInstall") or "0") == "1":
                continue
            ms = self._event_ms(fields, "MainPathBootTime", "BootTime")
            if ms:
                tiempos.append(ms)
        if not tiempos:
            raise SinDato("el registro no contiene ningún arranque utilizable "
                          "(solo reinicios por actualización, o ninguno todavía)")

        mediana = statistics.median(tiempos)
        segundos = mediana / 1000
        self.boot_seconds = segundos

        # Quién lo retrasa, agregado por nombre y quedándonos con el peor caso.
        culpables: dict[str, tuple[float, str]] = {}
        for item in data["delays"]:
            fields = item["fields"]
            nombre = str(fields.get("Name") or fields.get("FriendlyName") or "").strip()
            ms = self._event_ms(fields, "TotalTime", "DegradationTime", "IncidentTime")
            if not nombre or not ms:
                continue
            if nombre not in culpables or ms > culpables[nombre][0]:
                culpables[nombre] = (ms, item.get("kind") or "elemento")
        top = sorted(culpables.items(), key=lambda kv: -kv[1][0])[:5]
        detalle_top = ", ".join(f"{n} ({ms / 1000:.1f} s, {tipo})" for n, (ms, tipo) in top)

        if segundos <= 35:
            return f"{segundos:.0f} s de mediana en {len(tiempos)} arranques"
        if segundos <= 60:
            severidad, ganancia = "low", 0.08
        elif segundos <= 100:
            severidad, ganancia = "medium", 0.18
        else:
            severidad, ganancia = "high", 0.30

        pasos = ["Ctrl+Shift+Esc → pestaña Inicio: desactiva lo que no uses a diario"]
        if top:
            pasos.insert(0, f"Empieza por lo que más tarda: {top[0][0]}")
        pasos += ["Comprueba de nuevo tras un reinicio: Windows recalcula la medida sola",
                  "Visor de eventos → Registros de aplicaciones y servicios → Microsoft → "
                  "Windows → Diagnostics-Performance para ver el desglose completo"]
        self.add(
            id="boot_slow", title=f"El arranque tarda {segundos:.0f} s",
            severity=severidad, category="arranque", component="system",
            detail=f"Mediana de {segundos:.0f} s sobre {len(tiempos)} arranques, medida por el "
                   "propio Windows (se descartan los reinicios por actualización, que siempre "
                   "son lentos). Un equipo con SSD y un arranque limpio se queda en 15-30 s."
                   + (f" Lo que más lo retrasa: {detalle_top}." if detalle_top
                      else " Windows no ha señalado ningún culpable concreto."),
            gain=ganancia, gain_note="tiempo hasta escritorio usable (medido, no estimado)",
            effort="bajo", risk="nulo", steps=pasos)
        return f"{segundos:.0f} s de mediana en {len(tiempos)} arranques"

    # «Está sucio» y «NO está sucio» solo se diferencian por la negación, que va
    # traducida. Buscarla como palabra suelta es lo único que aguanta el cambio
    # de idioma y que la respuesta llegue con los acentos rotos. Decirle a
    # alguien que su sistema de ficheros está corrupto —y mandarle un chkdsk /r
    # de horas— por no saber leer la respuesta es mucho peor que no decir nada,
    # así que ante la duda no se informa.
    _NEGACIONES = {"not", "no", "nicht", "non", "pas", "niet", "nao", "não"}
    _SUCIO = ("dirty", "sucio", "sujo", "verschmutzt", "sporco", "vuil")

    def check_filesystem_health(self) -> str:
        rows = elevacion.recoger()["fsdirty"]
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar el volumen ({rows.error})")
        if not rows:
            raise SinDato("fsutil no ha respondido")
        drive = str(rows[0].get("unidad") or "C:")
        codigo = rows[0].get("codigo")
        out = str(rows[0].get("salida") or "")
        if codigo:
            # `fsutil dirty query` sale con código de error cuando no puede
            # mirar. Decirlo es la diferencia entre «prueba con permisos» y
            # «este Windows no trae fsutil».
            raise SinDato(f"fsutil ha terminado con código {codigo} "
                          f"(suele requerir administrador)")
        if not out:
            raise SinDato("fsutil no ha respondido")
        lowered = out.lower()
        if set(re.split(r"[^\w]+", lowered)) & self._NEGACIONES:
            return "limpio"
        if not any(termino in lowered for termino in self._SUCIO):
            raise SinDato(f"respuesta de fsutil no reconocida: «{out[:60]}»")
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

    def check_antivirus(self) -> str:
        rows = ps_json('Get-CimInstance -Namespace "root/SecurityCenter2" -ClassName AntiVirusProduct '
                       '-ErrorAction SilentlyContinue | Select-Object displayName,productState')
        if not getattr(rows, "ok", True):
            raise SinDato(f"no se ha podido consultar el Centro de seguridad ({rows.error})")
        names = [str(r.get("displayName")) for r in rows if r.get("displayName")]
        if not names:
            # Windows registra siempre Defender aquí; una lista vacía significa
            # que el Centro de seguridad no ha contestado, no que no haya nada.
            raise SinDato("el Centro de seguridad no ha devuelto ningún producto")
        self._check_proteccion_activa(rows)
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
        return ", ".join(names)

    # -------------------------------------------------------- seguridad ------
    # Estos hallazgos no aceleran el equipo, así que van todos con `gain=0.0` y
    # una nota que lo dice. Emitirlos con ganancia los metería en la proyección
    # y el informe acabaría prometiendo «+8% de fluidez por cifrar el disco»,
    # que es falso. El patrón ya estaba en `smart_warn`.

    # Cada cuánto se decide que una BIOS se ha quedado atrás. No se cruza con
    # CVE concretas a propósito: eso exigiría una base de datos externa y
    # rompería el «no envía nada a ninguna parte». Lo que sí es cierto sin
    # consultar nada es que Intel y AMD han publicado microcódigo después.
    BIOS_VIEJA_AÑOS = 3
    BIOS_MUY_VIEJA_AÑOS = 5

    def check_bios_age(self) -> str:
        if not self.si.bios_date:
            raise SinDato("el sistema no informa de la fecha de su BIOS")
        try:
            fecha = datetime.strptime(self.si.bios_date, "%Y-%m-%d").date()
        except ValueError:
            raise SinDato(f"fecha de BIOS no reconocida: «{self.si.bios_date}»") from None
        dias = (date.today() - fecha).days
        años = dias / 365.25
        if años < self.BIOS_VIEJA_AÑOS:
            return f"{self.si.bios_date} ({años:.0f} años)"
        severidad = "medium" if años >= self.BIOS_MUY_VIEJA_AÑOS else "low"
        self.add(
            id="bios_vieja", title=f"BIOS de {fecha.year}, con {años:.0f} años de antigüedad",
            severity=severidad, category=SEGURIDAD, component="system",
            detail=f"La BIOS de este equipo es del {self.si.bios_date}. Desde entonces Intel y "
                   "AMD han publicado varias revisiones de microcódigo, que es la vía por la "
                   "que se corrigen los fallos del propio procesador —los de la familia de "
                   "Spectre entre ellos— y que solo llegan al equipo dentro de una "
                   "actualización de BIOS. No se ha comprobado si a este modelo le falta "
                   "alguna en concreto: eso exigiría consultar una base de datos externa, y "
                   "este programa no envía nada a ninguna parte.",
            gain=0.0, gain_note="no es una optimización: son correcciones del procesador",
            effort="medio", risk="medio",
            steps=["Mira el modelo exacto de tu placa o portátil en la web del fabricante",
                   "Compara la versión publicada con la que tienes en el inventario de arriba",
                   "Actualiza con el equipo enchufado a la corriente y sin apagarlo a medias",
                   "Si no hay ninguna posterior, no hay nada que hacer: no fuerces una igual"])
        return f"{self.si.bios_date} ({años:.0f} años)"

    # `Get-BitLockerVolume` devuelve enumeraciones que `ConvertTo-Json` convierte
    # en enteros, aunque alguna versión de PowerShell las serializa por nombre.
    # Se aceptan las dos formas, y lo que no encaje en ninguna se trata como
    # desconocido y no como «sin cifrar»: decirle a alguien que su disco está
    # desprotegido cuando sí lo está es la clase de aviso que hace que se deje
    # de leer el informe entero.
    _CIFRADO_SI = {1, "1", "on", "fullyencrypted"}
    _CIFRADO_NO = {0, "0", "off", "fullydecrypted"}

    @staticmethod
    def _estado_cifrado(valor) -> bool | None:
        if isinstance(valor, bool):
            return None
        clave = valor.strip().lower() if isinstance(valor, str) else valor
        if clave in Auditor._CIFRADO_SI:
            return True
        if clave in Auditor._CIFRADO_NO:
            return False
        return None

    def check_disk_encryption(self) -> str:
        # La disponibilidad se resuelve con `Get-Command` y no leyendo el texto
        # del error: en Windows Home el cmdlet no existe y el mensaje viene
        # traducido, así que buscarlo por su texto es exactamente el fallo que
        # `check_filesystem_health` documenta y evita.
        rows = elevacion.recoger()["bitlocker"]
        if not rows.ok:
            # Sin privilegios el cmdlet existe pero rechaza contestar. No es «no
            # aplica»: es que no se ha podido mirar.
            raise SinDato(f"no se ha podido consultar BitLocker ({rows.error})")
        if not rows:
            raise SinDato("BitLocker no ha devuelto ningún volumen")
        if not rows[0].get("disponible"):
            raise NoAplica("esta edición de Windows no incluye BitLocker "
                           "(las ediciones Home no lo traen)")

        unidad = (self.si.system_drive or "C:").rstrip("\\")
        sistema = next((r for r in rows
                        if str(r.get("MountPoint") or "").rstrip("\\").upper() == unidad.upper()),
                       None)
        if sistema is None:
            raise SinDato(f"BitLocker no informa del volumen de sistema ({unidad})")
        protegido = self._estado_cifrado(sistema.get("ProtectionStatus"))
        if protegido is None:
            protegido = self._estado_cifrado(sistema.get("VolumeStatus"))
        if protegido is None:
            raise SinDato(f"estado de BitLocker no reconocido en {unidad}: "
                          f"«{sistema.get('ProtectionStatus')}»")
        if protegido:
            return f"{unidad} cifrado"

        self.add(
            id="sin_cifrado", title=f"El disco del sistema ({unidad}) no está cifrado",
            severity="high", category=SEGURIDAD, component="disk",
            detail="Sin cifrado, cualquiera que se lleve el equipo —o solo el disco— lee todos "
                   "los archivos sin necesidad de tu contraseña: basta con arrancar desde un USB "
                   "o poner la unidad en otro ordenador. La contraseña de Windows no protege los "
                   "datos, solo la sesión. Importa sobre todo en portátiles, que es donde se "
                   "pierden y se roban.",
            gain=0.0, gain_note="no es una optimización: es confidencialidad de tus archivos",
            effort="medio", risk="medio",
            steps=["Panel de control → Cifrado de unidad BitLocker → Activar BitLocker",
                   "GUARDA LA CLAVE DE RECUPERACIÓN antes de seguir, fuera de este equipo: "
                   "sin ella, un fallo de la placa o del TPM deja los datos irrecuperables",
                   "El cifrado inicial tarda y conviene hacerlo con el portátil enchufado",
                   "Si tu edición de Windows no lo incluye, VeraCrypt hace lo mismo"])
        return f"{unidad} SIN cifrar"

    def check_secure_boot(self) -> str:
        # El tipo de firmware sale de `$env:firmware_type`, que Windows rellena
        # con «UEFI» o «Legacy» y no está traducido. La alternativa era mirar el
        # texto de la excepción que lanza `Confirm-SecureBootUEFI` en un equipo
        # sin UEFI, y ese sí viene en el idioma del sistema.
        rows = elevacion.recoger()["secureboot"]
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar el arranque seguro ({rows.error})")
        if not rows:
            raise SinDato("el sistema no ha informado del tipo de firmware")
        firmware = str(rows[0].get("firmware") or "").strip().lower()
        if firmware and firmware != "uefi":
            raise NoAplica("este equipo arranca con BIOS heredada, que no tiene arranque seguro")
        activo = rows[0].get("activo")
        if not isinstance(activo, bool):
            # Sin privilegios el cmdlet contesta «Acceso denegado» y aquí llega
            # como None. Eso no es «desactivado».
            raise SinDato("el estado del arranque seguro requiere administrador")
        if activo:
            return "activo"
        self.add(
            id="secureboot_off", title="Arranque seguro (Secure Boot) desactivado",
            severity="medium", category=SEGURIDAD, component="system",
            detail="Con el arranque seguro apagado, el firmware no comprueba la firma de lo que "
                   "carga antes que Windows, así que un bootkit —malware que se instala por "
                   "debajo del sistema operativo— puede arrancar antes que el antivirus y "
                   "volverse invisible para él. Es además uno de los requisitos de Windows 11, "
                   "así que tenerlo apagado puede ser por lo que este equipo no da el salto.",
            gain=0.0, gain_note="no es una optimización: es integridad del arranque",
            effort="medio", risk="medio",
            steps=["Entra en la UEFI al encender (suele ser Supr, F2 o F10)",
                   "Busca «Secure Boot» y actívalo; puede exigir poner el modo de arranque "
                   "en UEFI puro y quitar CSM/Legacy",
                   "Si el disco usa MBR habrá que convertirlo a GPT antes, con `mbr2gpt`: "
                   "haz copia de seguridad primero",
                   "Si arrancas Linux en el mismo equipo, comprueba que tu distribución "
                   "esté firmada antes de activarlo"])
        return "desactivado"

    def check_tpm(self) -> str:
        rows = elevacion.recoger()["tpm"]
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar el TPM ({rows.error})")
        if not rows:
            raise SinDato("Get-Tpm no ha devuelto nada")
        if not rows[0].get("disponible"):
            raise NoAplica("esta edición de Windows no trae la consulta del TPM")
        presente = rows[0].get("TpmPresent")
        if not isinstance(presente, bool):
            # Verificado: sin privilegios `Get-Tpm` no falla, devuelve los campos
            # a null. Darlos por «no hay TPM» acusaría de faltarle el chip a casi
            # cualquier equipo, que es peor que no decir nada.
            raise SinDato("el estado del TPM requiere administrador")
        if not presente:
            self.add(
                id="sin_tpm", title="El equipo no tiene TPM",
                severity="medium", category=SEGURIDAD, component="system",
                detail="El TPM es el chip donde se guardan las claves de cifrado de forma que no "
                       "se puedan copiar. Sin él, BitLocker necesita que escribas una contraseña "
                       "en cada arranque, y este equipo no cumple los requisitos de Windows 11. "
                       "Muchas placas lo traen desactivado de fábrica bajo otro nombre: fTPM en "
                       "AMD, PTT en Intel.",
                gain=0.0, gain_note="no es una optimización: es dónde viven tus claves",
                effort="medio", risk="bajo",
                steps=["Entra en la UEFI y busca «fTPM» (AMD) o «Intel PTT»: suele estar ahí, "
                       "solo que apagado",
                       "Si no aparece ninguno, este equipo no lo tiene y no se le puede añadir",
                       "Comprueba después con `tpm.msc`"])
            return "no hay TPM"
        if rows[0].get("TpmEnabled") is False:
            self.add(
                id="tpm_desactivado", title="El equipo tiene TPM pero está desactivado",
                severity="medium", category=SEGURIDAD, component="system",
                detail="El chip está presente y apagado desde la UEFI, así que ni BitLocker "
                       "puede usarlo ni cuenta para los requisitos de Windows 11. Activarlo es "
                       "cambiar un ajuste, no comprar nada.",
                gain=0.0, gain_note="no es una optimización: es dónde viven tus claves",
                effort="bajo", risk="bajo",
                steps=["Entra en la UEFI y activa «fTPM» (AMD) o «Intel PTT»",
                       "Si ya usabas BitLocker, ten a mano la clave de recuperación: tocar el "
                       "TPM puede pedirla en el siguiente arranque",
                       "Comprueba después con `tpm.msc`"])
            return "presente pero desactivado"
        return "presente y activo"

    # `Get-WindowsOptionalFeature` devuelve una enumeración que, según la
    # versión, llega como texto o como el entero de DISM. Lo que no encaje en
    # ninguna de las dos formas se declara desconocido: dar por desactivado un
    # SMB1 que está activo sería justo el error que importa evitar.
    _SMB1_ACTIVO = {1, "enabled"}
    _SMB1_INACTIVO = {2, "disabled", "disabledwithpayloadremoved"}

    def check_smb1(self) -> str:
        rows = elevacion.recoger()["smb1"]
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar SMB1 ({rows.error})")
        if not rows:
            raise SinDato("Windows no ha informado del estado de SMB1")
        if not rows[0].get("disponible"):
            raise NoAplica("este Windows no permite consultar las características opcionales")
        estado = rows[0].get("State")
        clave = estado.strip().lower() if isinstance(estado, str) else estado
        if clave in self._SMB1_INACTIVO:
            return "desactivado"
        if clave not in self._SMB1_ACTIVO:
            raise SinDato(f"estado de SMB1 no reconocido: «{estado}»")
        self.add(
            id="smb1_activo", title="SMB1 sigue activo, un protocolo retirado en 2014",
            severity="high", category=SEGURIDAD, component="system",
            detail="SMB1 es la versión antigua del protocolo de archivos compartidos de Windows. "
                   "Microsoft lo dejó de instalar por defecto porque no se puede asegurar: no "
                   "firma los mensajes ni cifra nada, y es por donde entró WannaCry. Windows lo "
                   "deja activo cuando se ha actualizado desde una versión vieja o cuando algo "
                   "lo pidió: una impresora de red antigua o un NAS de hace años.",
            gain=0.0, gain_note="no es una optimización: es la puerta que usó WannaCry",
            effort="bajo", risk="bajo",
            steps=["Características de Windows → desmarca «Compatibilidad con el protocolo "
                   "para compartir archivos SMB 1.0/CIFS»",
                   "Antes de reiniciar, comprueba si algún NAS o impresora de red vieja "
                   "dependía de él: si deja de verse, casi siempre se arregla actualizando "
                   "su firmware",
                   "Nada de lo que se comparte hoy entre equipos Windows lo necesita"])
        return "ACTIVO"

    def check_local_accounts(self) -> str:
        rows = ps_json(
            "$( if (-not (Get-Command Get-LocalUser -ErrorAction SilentlyContinue)) {"
            "     [PSCustomObject]@{ disponible = $false } } else {"
            "     Get-LocalUser | Select-Object @{n='disponible';e={$true}},"
            "       Name,Enabled,PasswordRequired } )")
        if not rows.ok:
            raise SinDato(f"no se han podido enumerar las cuentas locales ({rows.error})")
        if not rows:
            raise SinDato("no se ha devuelto ninguna cuenta local")
        if not rows[0].get("disponible"):
            raise NoAplica("este Windows no trae la consulta de cuentas locales")

        # Solo las cuentas habilitadas. `Invitado`, `DefaultAccount` y
        # `WDAGUtilityAccount` vienen de fábrica sin exigir contraseña y
        # deshabilitadas: contarlas convertiría un Windows recién instalado en
        # tres hallazgos graves.
        abiertas = [str(r.get("Name")) for r in rows
                    if r.get("Enabled") is True and r.get("PasswordRequired") is False]
        if not abiertas:
            habilitadas = sum(1 for r in rows if r.get("Enabled") is True)
            return f"{habilitadas} cuenta(s) activa(s), todas con contraseña"
        self.add(
            id="cuenta_sin_clave",
            title=f"Cuenta local que no exige contraseña ({', '.join(abiertas)})",
            severity="high", category=SEGURIDAD, component="system",
            detail="Windows informa de que "
                   f"{'estas cuentas están habilitadas y no exigen' if len(abiertas) > 1 else 'esta cuenta está habilitada y no exige'} "
                   "contraseña. Cualquiera con acceso físico al equipo entra sin más, y en una "
                   "red local también sirve para conectarse a los recursos compartidos. No "
                   "afecta al inicio de sesión con cuenta de Microsoft o PIN, que sí llevan "
                   "credencial detrás.",
            gain=0.0, gain_note="no es una optimización: es quién puede entrar en tu equipo",
            effort="bajo", risk="bajo",
            steps=["Ponle contraseña: `net user NOMBRE *` en una consola de administrador",
                   "O deshabilita la cuenta si no se usa: Administración de equipos → "
                   "Usuarios y grupos locales",
                   "Las cuentas de fábrica deshabilitadas (Invitado, DefaultAccount) no "
                   "cuentan aquí y no hay que tocarlas"])
        return f"{len(abiertas)} sin contraseña"

    # Severidades que Microsoft asigna a sus boletines. Solo las llevan las
    # actualizaciones de seguridad: una de zona horaria viene sin ella, y
    # contarla como riesgo sería inflar el hallazgo con lo que no toca.
    _MSRC_GRAVES = ("critical",)
    _MSRC_SERIAS = ("important",)

    def check_security_updates(self) -> str:
        rows = pending_security_updates()
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar Windows Update ({rows.error})")
        seguridad = []
        for fila in rows:
            nivel = str(fila.get("MsrcSeverity") or "").strip().lower()
            if nivel:
                seguridad.append((nivel, str(fila.get("Title") or "sin título")))
        if not seguridad:
            return f"{len(rows)} pendiente(s), ninguna de seguridad"

        criticas = [t for n, t in seguridad if n in self._MSRC_GRAVES]
        importantes = [t for n, t in seguridad if n in self._MSRC_SERIAS]
        if criticas:
            severidad, cuantas = "high", len(criticas)
            resumen = f"{cuantas} crítica(s)"
        elif importantes:
            severidad, cuantas = "medium", len(importantes)
            resumen = f"{cuantas} importante(s)"
        else:
            severidad, cuantas = "low", len(seguridad)
            resumen = f"{cuantas} de severidad menor"
        primeras = "; ".join(t[:70] for _, t in seguridad[:3])
        self.add(
            id="updates_pendientes",
            title=f"{len(seguridad)} actualización(es) de seguridad sin instalar ({resumen})",
            severity=severidad, category=SEGURIDAD, component="system",
            detail=f"Windows Update tiene pendientes {len(seguridad)} actualizaciones que "
                   f"Microsoft clasifica como de seguridad, {resumen}. Un parche publicado es "
                   "también un aviso público de dónde está el fallo, así que el riesgo real de "
                   "no instalarlo sube con el tiempo, no baja. Las primeras de la lista: "
                   f"{primeras}.",
            gain=0.0, gain_note="no es una optimización: son fallos ya conocidos y publicados",
            effort="bajo", risk="bajo",
            steps=["Configuración → Windows Update → Buscar actualizaciones",
                   "Reinicia cuando lo pida: muchas no terminan de aplicarse hasta entonces",
                   "Si llevan meses sin instalarse, mira que el servicio Windows Update no "
                   "esté deshabilitado"])
        return f"{len(seguridad)} de seguridad ({resumen})"

    def _check_proteccion_activa(self, rows) -> None:
        """Si queda alguien vigilando, y con las firmas al día.

        Se mira el conjunto y no producto a producto por una razón concreta:
        cuando se instala un antivirus de terceros, **Defender se desactiva
        solo**, y esa es la configuración correcta. Avisar de cada producto
        apagado convertiría el caso más normal de todos en un hallazgo crítico
        falso. Lo que sí es un problema es que no quede ninguno activo.

        Igual con las firmas: unas firmas caducadas en un motor que está apagado
        a propósito no le importan a nadie.
        """
        estados = []
        for fila in rows:
            nombre = str(fila.get("displayName") or "").strip()
            estado = _estado_antivirus(fila.get("productState"))
            if nombre and estado is not None:
                estados.append((nombre,) + estado)
        if not estados:
            # `productState` no viene, o no encaja en el reparto conocido. No se
            # interpreta: acusar a alguien de tener el antivirus apagado por
            # haber leído mal un entero es peor que no decir nada.
            return

        activos = [(n, al_dia) for n, activo, al_dia in estados if activo]
        if not activos:
            self.add(
                id="av_tiempo_real_off",
                title="Ningún antivirus con la protección en tiempo real activa",
                severity="critical", category=SEGURIDAD, component="system",
                detail="El Centro de seguridad de Windows tiene registrados "
                       f"{', '.join(n for n, _, _ in estados)}, pero ninguno está vigilando "
                       "ahora mismo. Sin protección en tiempo real, un archivo malicioso solo "
                       "se detecta si alguien lanza un análisis a mano, es decir, después. "
                       "Suele pasar tras desinstalar un antivirus de pago sin reactivar "
                       "Defender, o porque algo lo apagó.",
                gain=0.0, gain_note="no es una optimización: es exposición a malware",
                effort="bajo", risk="nulo",
                steps=["Abre Seguridad de Windows → Protección antivirus y contra amenazas",
                       "Activa «Protección en tiempo real»",
                       "Si sigue apagándose sola, desinstala los restos del antivirus "
                       "anterior con la herramienta de limpieza de su fabricante"])
            return

        caducados = [n for n, al_dia in activos if not al_dia]
        if caducados:
            self.add(
                id="av_desactualizado",
                title=f"Firmas de antivirus caducadas ({', '.join(caducados)})",
                severity="high", category=SEGURIDAD, component="system",
                detail="El motor está activo pero sus definiciones no están al día, así que "
                       "no reconoce nada de lo aparecido desde la última actualización. Es la "
                       "situación más engañosa de todas: el icono dice que estás protegido y "
                       "técnicamente lo estás, pero contra las amenazas del mes pasado.",
                gain=0.0, gain_note="no es una optimización: es exposición a malware",
                effort="bajo", risk="nulo",
                steps=["Abre Seguridad de Windows → Protección antivirus y contra amenazas "
                       "→ Buscar actualizaciones",
                       "Si falla, comprueba que Windows Update no esté pausado",
                       "Sin conexión desde hace tiempo, basta con conectarlo a internet"])

    def check_defrag(self) -> str:
        if "HDD" not in self.si.system_drive_media:
            raise NoAplica("desfragmentar un SSD solo desgasta celdas")
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
        if not getattr(rows, "ok", True) or not rows:
            raise SinDato("no se ha podido consultar el estado de los discos"
                          + (f" ({rows.error})" if getattr(rows, "error", None) else ""))
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

        extra = self._check_disk_wear()
        return extra or f"{len(rows)} disco(s) sano(s)"

    def _check_disk_wear(self) -> str:
        """Desgaste y errores acumulados, que avisan mucho antes que HealthStatus.

        Un SSD al 90% de vida consumida sigue reportándose como «Healthy» hasta
        que deja de funcionar. Sin privilegios estos contadores no se leen, y
        entonces no se afirma nada: ausencia de dato no es ausencia de problema.
        """
        gastados, con_errores, calientes, con_sectores = [], [], [], []
        medidos = 0
        for disk in self.si.physical_disks:
            nombre = str(disk.get("name") or "disco")
            desgaste = disk.get("wear")
            errores = (disk.get("read_errors") or 0) + (disk.get("write_errors") or 0)
            grados = disk.get("temperature")
            # Los sectores solo los publican los ATA/SATA, así que un NVMe llega
            # con desgaste y sin ellos, y un HDD al revés si el contador de
            # fiabilidad no contestó. Cualquiera de los dos cuenta como medido.
            sectores = {campo: disk.get(campo)
                        for campo in ("reallocated", "pending", "uncorrectable")
                        if disk.get(campo) is not None}
            if (desgaste is None and grados is None and not sectores
                    and disk.get("power_on_hours") is None):
                continue
            medidos += 1
            # Los pendientes son sectores que el disco ya no consigue leer y aún
            # no ha sustituido: son los que más avisan y los que peor van a más.
            if sectores.get("pending") or sectores.get("uncorrectable"):
                con_sectores.append((nombre, sectores, True))
            elif sectores.get("reallocated"):
                con_sectores.append((nombre, sectores, False))
            # El desgaste es un contador de ciclos de escritura: en un disco
            # mecánico no significa nada, y los HDD lo devuelven igualmente a 0.
            es_ssd = "SSD" in str(disk.get("media") or "").upper()
            if es_ssd and desgaste is not None and desgaste >= 60:
                gastados.append((nombre, desgaste))
            if errores > 0:
                con_errores.append((nombre, errores))
            # Los USB suelen mentir con la temperatura; solo se mira lo interno.
            if grados and grados >= 65 and str(disk.get("bus") or "").upper() != "USB":
                calientes.append((nombre, grados))

        if con_errores:
            lista = ", ".join(f"{n} ({e} errores)" for n, e in con_errores)
            self.add(
                id="disk_errors", title=f"Errores de lectura/escritura no corregidos ({lista})",
                severity="critical", category="almacenamiento", component="disk",
                detail="El disco ha registrado operaciones que no pudo corregir. Es pérdida de "
                       "datos ya ocurrida, no un riesgo futuro, y Windows sigue informando del "
                       "disco como «Healthy» hasta que falla del todo.",
                gain=0.0, gain_note="no es una optimización: es pérdida de datos",
                effort="alto", risk="alto",
                steps=["Copia de seguridad inmediata, antes de cualquier otra cosa",
                       "Comprueba los atributos completos con CrystalDiskInfo o smartctl",
                       "Sustituye el disco: los errores no corregidos no se arreglan"])

        if con_sectores:
            urgente = any(grave for _, _, grave in con_sectores)

            def cuenta(datos: dict) -> str:
                partes = []
                if datos.get("pending"):
                    partes.append(f"{datos['pending']} pendientes")
                if datos.get("reallocated"):
                    partes.append(f"{datos['reallocated']} reasignados")
                if datos.get("uncorrectable"):
                    partes.append(f"{datos['uncorrectable']} irrecuperables")
                return ", ".join(partes)

            lista = "; ".join(f"{n} ({cuenta(d)})" for n, d, _ in con_sectores)
            self.add(
                id="disk_sectores", title=f"Sectores defectuosos en el disco ({lista})",
                severity="high" if urgente else "medium",
                category="almacenamiento", component="disk",
                detail="Un sector reasignado es un trozo del disco que se estropeó y que el "
                       "firmware sustituyó por uno de repuesto, sin que Windows se entere: el "
                       "disco sigue diciendo «Healthy» y la reserva de repuestos es limitada. "
                       "Los pendientes son peores, porque son sectores que ya no se leen y que "
                       "todavía no se han sustituido: si ahí había un archivo, ese archivo ya "
                       "no está entero. Es el aviso más temprano que da un disco mecánico, y "
                       "llega meses antes de que falle nada visible.",
                gain=0.0, gain_note="no es una optimización: es un disco empezando a fallar",
                effort="medio", risk="alto" if urgente else "medio",
                steps=["Copia de seguridad de lo que haya ahí, hoy y no la semana que viene",
                       "Vigila la cuenta con CrystalDiskInfo: lo que importa no es el número, "
                       "es si sube. Estable durante meses puede convivir; subiendo, no",
                       "`chkdsk /r` obliga al disco a releerlo todo y a sustituir lo que no "
                       "pueda leer: tarda horas y conviene hacerlo con la copia ya hecha",
                       "Si la cuenta sube, sustituye el disco antes de que se lleve algo"])

        if gastados:
            peor = max(gastados, key=lambda x: x[1])
            severidad = "critical" if peor[1] >= 90 else "high" if peor[1] >= 80 else "medium"
            self.add(
                id="disk_wear", title=f"{peor[0]}: {peor[1]}% de vida útil consumida",
                severity=severidad, category="almacenamiento", component="disk",
                detail=f"El contador de desgaste del disco va por el {peor[1]}%. Es una "
                       "estimación del propio firmware sobre los ciclos de escritura "
                       "consumidos. Cerca del 100% el disco pasa a solo lectura o falla, y "
                       "antes de eso la velocidad de escritura ya se degrada.",
                gain=0.0, gain_note="no es una optimización: es vida restante del disco",
                effort="alto", risk="bajo",
                steps=["Planifica la sustitución antes de llegar al 100%",
                       "Mantén copia de seguridad al día mientras tanto",
                       "Evita moverle cargas de escritura intensiva (torrents, edición, swap)"])

        if calientes:
            peor = max(calientes, key=lambda x: x[1])
            self.add(
                id="disk_hot", title=f"{peor[0]} a {peor[1]} °C",
                severity="medium", category="térmico", component="disk",
                detail="Por encima de 65-70 °C los SSD NVMe reducen velocidad para protegerse, "
                       "y el calor sostenido acorta su vida. Suele ser falta de disipador o de "
                       "flujo de aire, no un defecto del disco.",
                gain=0.08, gain_note="velocidad sostenida de disco",
                effort="medio", risk="bajo",
                steps=["Monta el disipador del M.2 si la placa lo trae y no está puesto",
                       "Mejora el flujo de aire de la caja",
                       "Aléjalo de la gráfica si comparte espacio con ella"])

        if not medidos:
            return "sin contadores de fiabilidad (requiere administrador)"
        partes = [f"{medidos} disco(s) con contadores"]
        if gastados:
            partes.append(f"desgaste máx {max(d for _, d in gastados)}%")
        if con_errores:
            partes.append("con errores no corregidos")
        return " · ".join(partes)

    # --------------------------------------------------------- solo Linux ----
    def check_linux_governor(self) -> str:
        path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        if not path.exists():
            raise NoAplica("este kernel no expone gobernadores de frecuencia")
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
            raise SinDato("no se ha podido leer /proc/sys/vm/swappiness")
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
        if not out:
            # `systemctl` devuelve código distinto de cero cuando la unidad no
            # existe o está deshabilitada, y run_cmd no da salida en ese caso:
            # sin esto, «no hay TRIM periódico» y «no hay systemd» eran lo mismo.
            # Se sigue sin poder separarlos, pero ahora al menos se dice cuál de
            # los dos fallos ha ocurrido en vez de callarlo.
            raise SinDato(f"systemctl no ha informado del estado de fstrim.timer"
                          + (f": {out.error}" if out.error else ""))
        if "enabled" not in out:
            self.add(
                id="linux_trim", title="fstrim.timer no está activado",
                severity="medium", category="almacenamiento", component="disk",
                detail="Sin TRIM periódico, el SSD degrada su rendimiento de escritura.",
                gain=0.12, gain_note="escritura sostenida", effort="bajo", risk="nulo",
                steps=["`sudo systemctl enable --now fstrim.timer`"])
        return out
