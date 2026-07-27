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
from .platform_utils import is_admin, ps_json


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
    ram_channels: int | None = None
    gpus: list[dict] = field(default_factory=list)
    disks: list[dict] = field(default_factory=list)
    system_drive: str = ""
    system_drive_media: str = "Desconocido"
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

    mem = ps_json("Get-CimInstance Win32_PhysicalMemory | "
                  "Select-Object BankLabel,DeviceLocator,Capacity,Speed,Manufacturer,PartNumber")
    if mem:
        si.ram_sticks = [{
            "slot": m.get("DeviceLocator") or m.get("BankLabel") or "?",
            "capacity": int(m.get("Capacity") or 0),
            "speed": int(m.get("Speed") or 0),
            "vendor": (m.get("Manufacturer") or "").strip(),
            "part": (m.get("PartNumber") or "").strip(),
        } for m in mem]
        speeds = [s["speed"] for s in si.ram_sticks if s["speed"]]
        si.ram_speed_mhz = max(speeds) if speeds else None
        si.ram_channels = len([s for s in si.ram_sticks if s["capacity"] > 0])

    gpus = ps_json("Get-CimInstance Win32_VideoController | "
                   "Select-Object Name,DriverVersion,DriverDate,AdapterRAM,CurrentHorizontalResolution,"
                   "CurrentVerticalResolution,CurrentRefreshRate")
    for g in gpus:
        drv_date = _parse_cim_date(g.get("DriverDate"))
        si.gpus.append({
            "name": g.get("Name"),
            "driver": g.get("DriverVersion"),
            "driver_date": drv_date.strftime("%Y-%m-%d") if drv_date else None,
            "driver_age_days": (datetime.now() - drv_date).days if drv_date else None,
            "vram": int(g.get("AdapterRAM") or 0),
            "resolution": f"{g.get('CurrentHorizontalResolution') or '?'}x"
                          f"{g.get('CurrentVerticalResolution') or '?'}"
                          f" @ {g.get('CurrentRefreshRate') or '?'}Hz",
        })

    phys = ps_json("Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,BusType,"
                   "Size,HealthStatus,SpindleSpeed")
    media_types = []
    for p in phys:
        mt = p.get("MediaType")
        if isinstance(mt, int):
            mt = {3: "HDD", 4: "SSD", 5: "SCM"}.get(mt, "Desconocido")
        media_types.append(str(mt))
        for d in si.disks:
            d.setdefault("candidates", []).append({
                "name": p.get("FriendlyName"), "media": mt, "bus": p.get("BusType"),
                "health": p.get("HealthStatus"),
            })
    si.system_drive_media = _guess_system_media(media_types)

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


def _guess_system_media(media_types: list[str]) -> str:
    """Determina el tipo de disco de sistema de forma conservadora."""
    if not media_types:
        return "Desconocido"
    normalized = [m.upper() for m in media_types]
    if all("SSD" in m or "SCM" in m for m in normalized):
        return "SSD"
    if all("HDD" in m for m in normalized):
        return "HDD"
    return "Mixto (" + ", ".join(sorted(set(media_types))) + ")"


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
