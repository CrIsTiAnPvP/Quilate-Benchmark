"""Inventario del equipo: CPU, RAM, discos, GPU y datos del sistema operativo."""

from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from .const import IS_LINUX, IS_WINDOWS
from .platform_utils import is_admin, ps_json, reg_read, winreg
from .sensors import gpu_telemetry


@dataclass
class SystemInfo:
    hostname: str = ""
    os_name: str = ""
    os_build: str = ""
    os_install_date: str | None = None
    os_age_days: int | None = None
    uptime_hours: float = 0.0
    cpu_name: str = ""
    cpu_cores: int = 0
    cpu_threads: int = 0
    cpu_base_mhz: float = 0.0
    cpu_max_mhz: float = 0.0
    ram_total: int = 0
    ram_available: int = 0
    ram_sticks: list[dict] = field(default_factory=list)
    ram_speed_mhz: int | None = None
    ram_speed_rated_mhz: int | None = None
    ram_channels: int | None = None
    gpus: list[dict] = field(default_factory=list)
    disks: list[dict] = field(default_factory=list)
    physical_disks: list[dict] = field(default_factory=list)
    system_drive: str = ""
    system_drive_media: str = "Desconocido"
    system_disk_number: int | None = None
    bios_date: str | None = None
    is_laptop: bool = False
    is_admin: bool = False
    python_version: str = ""


def collect_system_info() -> SystemInfo:
    si = SystemInfo()
    si.hostname = platform.node()
    si.python_version = platform.python_version()
    si.is_admin = is_admin()
    si.os_name = f"{platform.system()} {platform.release()}"
    si.os_build = platform.version()

    # --- CPU ---
    si.cpu_cores = psutil.cpu_count(logical=False) or 0
    si.cpu_threads = psutil.cpu_count(logical=True) or 0
    si.cpu_name = platform.processor() or "Desconocido"
    try:
        freq = psutil.cpu_freq()
        if freq:
            si.cpu_base_mhz = freq.current or 0.0
            si.cpu_max_mhz = freq.max or 0.0
    except Exception:
        pass

    # --- RAM ---
    vm = psutil.virtual_memory()
    si.ram_total = vm.total
    si.ram_available = vm.available

    # --- Uptime ---
    si.uptime_hours = (time.time() - psutil.boot_time()) / 3600.0

    # --- Discos ---
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        si.disks.append({
            "device": part.device,
            "mount": part.mountpoint,
            "fstype": part.fstype,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": usage.percent,
            "label": "",
            "kind": "local",     # local | cloud | network | removable | cdrom | virtual
            "ignored": False,    # los que no son almacenamiento físico local no se auditan
            "disk_number": None,
            "physical": None,
        })

    si.system_drive = os.environ.get("SystemDrive", "C:") + "\\" if IS_WINDOWS else "/"

    if IS_WINDOWS:
        _collect_windows_info(si)
    elif IS_LINUX:
        _collect_linux_info(si)

    return si


def _collect_windows_info(si: SystemInfo) -> None:
    osinfo = ps_json("Get-CimInstance Win32_OperatingSystem | "
                     "Select-Object Caption,BuildNumber,Version,InstallDate,OSArchitecture")
    if osinfo:
        d = osinfo[0]
        si.os_name = d.get("Caption") or si.os_name
        si.os_build = f"{d.get('Version', '')} (build {d.get('BuildNumber', '')})"
        raw = d.get("InstallDate")
        parsed = _parse_cim_date(raw)
        if parsed:
            si.os_install_date = parsed.strftime("%Y-%m-%d")
            si.os_age_days = (datetime.now() - parsed).days

    cpu = ps_json("Get-CimInstance Win32_Processor | "
                  "Select-Object Name,MaxClockSpeed,NumberOfCores,NumberOfLogicalProcessors")
    if cpu:
        si.cpu_name = (cpu[0].get("Name") or si.cpu_name).strip()
        si.cpu_max_mhz = float(cpu[0].get("MaxClockSpeed") or si.cpu_max_mhz)

    mem = ps_json("Get-CimInstance Win32_PhysicalMemory | Select-Object BankLabel,DeviceLocator,"
                  "Capacity,Speed,ConfiguredClockSpeed,Manufacturer,PartNumber")
    if mem:
        # En SMBIOS, Speed es la velocidad máxima que soporta el módulo y
        # ConfiguredClockSpeed la que está corriendo de verdad. Sin XMP/EXPO
        # activado difieren, y la que importa es la segunda: informar de la
        # nominal es dar por bueno un rendimiento que el equipo no tiene.
        si.ram_sticks = [{
            "slot": m.get("DeviceLocator") or m.get("BankLabel") or "?",
            "capacity": int(m.get("Capacity") or 0),
            "speed": int(m.get("ConfiguredClockSpeed") or m.get("Speed") or 0),
            "rated_speed": int(m.get("Speed") or 0),
            "vendor": (m.get("Manufacturer") or "").strip(),
            "part": (m.get("PartNumber") or "").strip(),
        } for m in mem]
        speeds = [s["speed"] for s in si.ram_sticks if s["speed"]]
        rated = [s["rated_speed"] for s in si.ram_sticks if s["rated_speed"]]
        si.ram_speed_mhz = max(speeds) if speeds else None
        si.ram_speed_rated_mhz = max(rated) if rated else None
        si.ram_channels = len([s for s in si.ram_sticks if s["capacity"] > 0])

    gpus = ps_json("Get-CimInstance Win32_VideoController | "
                   "Select-Object Name,DriverVersion,DriverDate,AdapterRAM,CurrentHorizontalResolution,"
                   "CurrentVerticalResolution,CurrentRefreshRate")
    vram_by_name = _vram_from_registry()
    telemetry = {str(t["name"]).lower(): t for t in gpu_telemetry()}
    for g in gpus:
        drv_date = _parse_cim_date(g.get("DriverDate"))
        name = str(g.get("Name") or "")
        # AdapterRAM es un entero de 32 bits: se satura en 4 GB, así que una GPU
        # de 8, 12 o 24 GB aparece siempre como 4 GB. El tamaño real está en el
        # registro (qwMemorySize, 64 bits) y, en NVIDIA, en nvidia-smi.
        vram = int(g.get("AdapterRAM") or 0)
        vram_source = "Win32_VideoController"
        reg_vram = vram_by_name.get(name.lower())
        if reg_vram and reg_vram > vram:
            vram, vram_source = reg_vram, "registro (qwMemorySize)"
        # LibreHardwareMonitor y Win32_VideoController no siempre escriben igual
        # el nombre de la tarjeta, así que si no casa exacto se busca por
        # coincidencia parcial antes de darla por no medida.
        live = telemetry.get(name.lower())
        if live is None:
            live = next((t for clave, t in telemetry.items()
                         if clave in name.lower() or name.lower() in clave), None)
        if live and live.get("vram"):
            vram, vram_source = live["vram"], "nvidia-smi"
        width = g.get("CurrentHorizontalResolution")
        si.gpus.append({
            "name": name,
            "integrated": _is_integrated(name),
            # Sin resolución activa el adaptador está ahí pero no pinta nada:
            # típico de la iGPU cuando el monitor va por la tarjeta dedicada.
            "active": bool(width),
            "driver": g.get("DriverVersion"),
            "driver_date": drv_date.strftime("%Y-%m-%d") if drv_date else None,
            "driver_age_days": (datetime.now() - drv_date).days if drv_date else None,
            "vram": vram,
            "vram_source": vram_source,
            "vram_used": (live or {}).get("vram_used"),
            "temperature": (live or {}).get("temperature"),
            "utilization": (live or {}).get("utilization"),
            "clock_mhz": (live or {}).get("clock_mhz"),
            "power_w": (live or {}).get("power_w"),
            "resolution": (f"{width}x{g.get('CurrentVerticalResolution') or '?'}"
                           f" @ {g.get('CurrentRefreshRate') or '?'}Hz") if width else None,
        })

    _map_storage(si)

    bios = ps_json("Get-CimInstance Win32_BIOS | Select-Object ReleaseDate,SMBIOSBIOSVersion,Manufacturer")
    if bios:
        bd = _parse_cim_date(bios[0].get("ReleaseDate"))
        si.bios_date = bd.strftime("%Y-%m-%d") if bd else None

    chassis = ps_json("Get-CimInstance Win32_SystemEnclosure | Select-Object ChassisTypes")
    if chassis:
        types = chassis[0].get("ChassisTypes") or []
        if isinstance(types, int):
            types = [types]
        si.is_laptop = any(t in (8, 9, 10, 11, 12, 14, 18, 21, 30, 31, 32) for t in types)


# Nombres de gráficas integradas. Las dedicadas llevan modelo ("GeForce RTX 5060 Ti",
# "Radeon RX 7800 XT", "Arc A770"); las iGPU se quedan en el genérico del fabricante.
_INTEGRATED_HINTS = ("amd radeon(tm) graphics", "radeon(tm) vega", "vega graphics",
                     "uhd graphics", "hd graphics", "iris", "intel(r) graphics",
                     "microsoft basic display")


def _is_integrated(name: str) -> bool:
    return any(hint in name.lower() for hint in _INTEGRATED_HINTS)


def gpu_label(gpu: dict) -> str:
    """Nombre del adaptador con el contexto que evita confundirlo con la
    gráfica principal (integrada, o presente pero sin pintar nada)."""
    tags = []
    if gpu.get("integrated"):
        tags.append("integrada")
    if not gpu.get("active", True):
        tags.append("sin pantalla conectada")
    name = str(gpu.get("name") or "?")
    return f"{name} ({', '.join(tags)})" if tags else name


def primary_gpu(gpus: list[dict]) -> dict | None:
    """La gráfica que representa al equipo: la que pinta en pantalla y, entre
    varias, la dedicada. Si nada lo aclara, la de más VRAM.

    Windows lista también la iGPU del procesador aunque el monitor vaya por la
    tarjeta dedicada, y no siempre la deja en primer lugar.
    """
    if not gpus:
        return None
    return max(gpus, key=lambda g: (bool(g.get("active", True)),
                                    not g.get("integrated", False),
                                    g.get("vram") or 0))


def _storage_reliability() -> dict[int, dict]:
    """Desgaste, horas de uso y errores acumulados de cada disco físico.

    Sale de Get-StorageReliabilityCounter, que es nativo de Windows y expone los
    atributos SMART que de verdad importan. Requiere privilegios de
    administrador: sin ellos devuelve vacío, y quien llame debe tratar la
    ausencia como «no medido», nunca como «disco nuevo».

    Un campo a None significa que ese disco no publica el atributo. Es habitual
    en discos USB y en algunos SATA antiguos.
    """
    rows = ps_json(
        "Get-PhysicalDisk | ForEach-Object { $d = $_; "
        "$c = $d | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; "
        "if ($c) { [PSCustomObject]@{ DeviceId = $d.DeviceId; Wear = $c.Wear; "
        "Temperature = $c.Temperature; PowerOnHours = $c.PowerOnHours; "
        "ReadErrorsUncorrected = $c.ReadErrorsUncorrected; "
        "WriteErrorsUncorrected = $c.WriteErrorsUncorrected } } }",
        timeout=40)

    def num(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    out: dict[int, dict] = {}
    for row in rows:
        number = num(row.get("DeviceId"))
        if number is None:
            continue
        out[number] = {
            "wear": num(row.get("Wear")),
            "temperature": num(row.get("Temperature")),
            "power_on_hours": num(row.get("PowerOnHours")),
            "read_errors": num(row.get("ReadErrorsUncorrected")),
            "write_errors": num(row.get("WriteErrorsUncorrected")),
        }
    return out


def _vram_from_registry() -> dict[str, int]:
    """VRAM real por adaptador, leída de la clase de dispositivos de pantalla."""
    if not IS_WINDOWS or winreg is None:
        return {}
    base = (r"SYSTEM\CurrentControlSet\Control\Class"
            r"\{4d36e968-e325-11ce-bfc1-08002be10318}")
    out: dict[str, int] = {}
    for index in range(12):
        path = f"{base}\\{index:04d}"
        desc = reg_read(winreg.HKEY_LOCAL_MACHINE, path, "DriverDesc")
        if not desc:
            continue
        for value_name in ("HardwareInformation.qwMemorySize",
                           "HardwareInformation.MemorySize"):
            raw = reg_read(winreg.HKEY_LOCAL_MACHINE, path, value_name)
            size = 0
            if isinstance(raw, int):
                size = raw
            elif isinstance(raw, bytes):     # algunos drivers lo guardan binario
                size = int.from_bytes(raw, "little")
            if size > 0:
                out[str(desc).lower()] = max(out.get(str(desc).lower(), 0), size)
    return out


# Etiquetas de volumen que delatan un almacenamiento sincronizado en la nube.
CLOUD_HINTS = ("google drive", "onedrive", "one drive", "dropbox", "icloud",
               "mega", "pcloud", "box", "nextcloud", "owncloud", "yandex",
               "tresorit", "sharepoint", "sync.com", "proton drive", "koofr")

DRIVE_TYPES = {2: "removable", 3: "local", 4: "network", 5: "cdrom", 6: "virtual"}

BUS_TYPES = {1: "SCSI", 2: "ATAPI", 3: "ATA", 4: "1394", 5: "SSA", 6: "Fibre Channel",
             7: "USB", 8: "RAID", 9: "iSCSI", 10: "SAS", 11: "SATA", 12: "SD",
             13: "MMC", 15: "Virtual", 16: "Storage Spaces", 17: "NVMe", 18: "SCM"}

KIND_LABELS = {"local": "local", "cloud": "nube (sincronizado)", "network": "unidad de red",
               "removable": "extraíble", "cdrom": "óptica", "virtual": "virtual"}


def _map_storage(si: SystemInfo) -> None:
    """Clasifica cada volumen y lo une con su disco físico.

    Hace falta porque psutil ve «un disco fijo de 930 GB con FAT32» donde en
    realidad hay una carpeta de Google Drive montada como unidad. Auditar el
    espacio libre de eso no tiene sentido: no es almacenamiento del equipo, su
    tamaño es ficticio y no se puede liberar borrando archivos locales.
    """
    logical = {}
    for row in ps_json("Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,"
                       "DriveType,ProviderName,VolumeName,FileSystem"):
        letter = str(row.get("DeviceID") or "")[:1].upper()
        if letter:
            logical[letter] = row

    letter_to_disk: dict[str, int] = {}
    for row in ps_json("Get-Partition | Where-Object DriveLetter | "
                       "Select-Object DriveLetter,DiskNumber"):
        letter = str(row.get("DriveLetter") or "")[:1].upper()
        number = row.get("DiskNumber")
        if letter and number is not None:
            letter_to_disk[letter] = int(number)

    reliability = _storage_reliability()
    for p in ps_json("Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,"
                     "BusType,Size,HealthStatus,SpindleSpeed"):
        media = p.get("MediaType")
        if isinstance(media, int):
            media = {3: "HDD", 4: "SSD", 5: "SCM"}.get(media, "Desconocido")
        bus = p.get("BusType")
        if isinstance(bus, int):
            bus = BUS_TYPES.get(bus, f"tipo {bus}")
        try:
            number = int(p.get("DeviceId"))
        except (TypeError, ValueError):
            number = None
        si.physical_disks.append({
            "number": number,
            "name": p.get("FriendlyName"),
            "media": str(media or "Desconocido"),
            "bus": str(bus or "?"),
            "size": int(p.get("Size") or 0),
            "health": p.get("HealthStatus"),
            "rpm": p.get("SpindleSpeed"),
            # HealthStatus es un binario sano/no sano. El desgaste y los errores
            # acumulados avisan mucho antes de que Windows lo dé por degradado.
            **reliability.get(number, {}),
        })
    by_number = {d["number"]: d for d in si.physical_disks if d["number"] is not None}

    for d in si.disks:
        letter = str(d["mount"])[:1].upper()
        info = logical.get(letter, {})
        label = str(info.get("VolumeName") or "").strip()
        provider = str(info.get("ProviderName") or "").strip()
        drive_type = info.get("DriveType")
        kind = DRIVE_TYPES.get(int(drive_type), "local") if drive_type is not None else "local"

        if provider or kind == "network":
            kind = "network"
        elif any(h in label.lower() for h in CLOUD_HINTS):
            kind = "cloud"
        elif kind == "local" and letter not in letter_to_disk:
            # Sin partición detrás no hay disco: subst, ramdisk, contenedor cifrado
            # o un cliente de nube que monta su propio sistema de ficheros.
            kind = "virtual"

        d["label"] = label
        d["kind"] = kind
        d["ignored"] = kind != "local"
        d["disk_number"] = letter_to_disk.get(letter)
        d["physical"] = by_number.get(d["disk_number"]) if d["disk_number"] is not None else None

    sys_letter = str(si.system_drive)[:1].upper()
    si.system_disk_number = letter_to_disk.get(sys_letter)
    system_disk = by_number.get(si.system_disk_number) if si.system_disk_number is not None else None
    if system_disk:
        si.system_drive_media = system_disk["media"]
    else:
        si.system_drive_media = _guess_system_media(
            [d["physical"]["media"] for d in si.disks if d.get("physical")])


def _guess_system_media(media_types: list[str]) -> str:
    """Respaldo cuando no se puede resolver el disco exacto del volumen."""
    if not media_types:
        return "Desconocido"
    normalized = [m.upper() for m in media_types]
    if all("SSD" in m or "SCM" in m for m in normalized):
        return "SSD"
    if all("HDD" in m for m in normalized):
        return "HDD"
    return "Mixto (" + ", ".join(sorted(set(media_types))) + ")"


def local_volumes(si: SystemInfo, min_size: int = 5 * 1024**3) -> list[dict]:
    """Volúmenes que son almacenamiento físico del equipo y merecen auditarse."""
    return [d for d in si.disks if not d["ignored"] and d["total"] >= min_size]


def _collect_linux_info(si: SystemInfo) -> None:
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.lower().startswith("model name"):
                    si.cpu_name = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    try:
        si.os_name = " ".join(
            l.split("=", 1)[1].strip().strip('"')
            for l in Path("/etc/os-release").read_text().splitlines()
            if l.startswith("PRETTY_NAME=")
        ) or si.os_name
    except OSError:
        pass


def _parse_cim_date(raw: Any) -> datetime | None:
    """Convierte fechas CIM/WMI o ISO en datetime."""
    if not raw:
        return None
    if isinstance(raw, dict):  # ConvertTo-Json serializa DateTime como {"value": "/Date(...)/"}
        raw = raw.get("value") or raw.get("DateTime") or ""
    text = str(raw)
    if "/Date(" in text:
        try:
            ms = int(text.split("/Date(")[1].split(")")[0].split("+")[0].split("-")[0])
            return datetime.fromtimestamp(ms / 1000)
        except (ValueError, IndexError):
            return None
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text[:len(fmt) + 2].split(".")[0], fmt)
        except ValueError:
            continue
    return None
