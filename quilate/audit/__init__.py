"""Auditoria del sistema: comprobaciones de configuracion y hallazgos.

La fachada del paquete. Reexporta lo que el resto del programa importa de
`audit` —el vocabulario de `modelo`, y el `Auditor` que se arma aquí— para que
dividir esto por dentro no obligue a tocar ni un import de fuera.
"""

from __future__ import annotations

from ..benchmark import Benchmark
from ..console import section, spinner_done, spinner_step
from ..const import IS_LINUX, IS_WINDOWS
from ..storage_scan import ScanResult
from ..sysinfo import SystemInfo

from .almacenamiento import ChecksAlmacenamiento
from .linux import ChecksLinux
from .modelo import (SEGURIDAD, SEVERITY_COLOR, SEVERITY_ORDER, SEVERITY_TEXT,
                     Finding, NoAplica, SinDato, _ESFUERZOS, _ID_VALIDO,
                     _RIESGOS, security_findings, sev_label)
from .rendimiento import ChecksRendimiento
from .seguridad import ChecksSeguridad
from .tablas import (_DRIVER_VIEJO_DIAS, _PNP_DELIBERADO, _PNP_PROBLEMA,
                     _estado_antivirus)

# La superficie que el resto del programa importa de `audit`. Se declara para
# que quede escrito qué es fachada y qué es detalle interno: los cuatro mixins
# se arman aquí y nadie de fuera tiene por qué conocerlos.
__all__ = ["Auditor", "Finding", "NoAplica", "SEGURIDAD", "SEVERITY_COLOR",
           "SEVERITY_ORDER", "SEVERITY_TEXT", "SinDato", "security_findings",
           "sev_label", "_DRIVER_VIEJO_DIAS", "_ESFUERZOS", "_ID_VALIDO",
           "_PNP_DELIBERADO", "_PNP_PROBLEMA", "_RIESGOS", "_estado_antivirus"]


class Auditor(ChecksRendimiento, ChecksSeguridad,
              ChecksAlmacenamiento, ChecksLinux):
    """Reúne las cuatro familias de comprobaciones y las ejecuta.

    La clase no tiene comprobaciones propias: aporta el estado que todas
    comparten (`si`, `bench`, `scan`, `findings`), la validación de `add()` y
    el orden de `run()`. Cada familia vive en su módulo y se enchufa aquí.
    """

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

        Qué depende hoy de cada validación, que ya no es lo mismo para las tres:

        · `id` — el HTML lo usa como ancla (`#h-{id}`) y como destino del enlace
          que apunta a ella. Un ancla no se puede escapar sin dejar de ser la
          misma ancla, así que **esto sigue siendo la única garantía que hay**.
          Es la razón principal de que este método valide.
        · `severity` — el HTML ya lo pasa por `_e()` en los cinco sitios donde
          lo interpola, así que el escapado no depende de aquí. Se sigue
          validando porque el valor también da nombre a una clase CSS (`b-high`)
          y porque una severidad inventada rompería el orden del informe.
        · `effort` y `risk` — van al `.ps1` sin escapar, dentro de unas comillas
          dobles de PowerShell. Ahí esta validación sí es lo único que hay.

        Se valida en el origen, y no solo en cada destino, porque así la
        garantía vale para las cuatro salidas y no hay que acordarse en cada
        una. Falla ruidosamente a propósito: es un error de programación del
        propio Quilate, no un dato del sistema que haya que tolerar, y el sitio
        donde tiene que saltar es el del programador y no el informe del
        usuario.
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
