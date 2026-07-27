#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 PCBench Suite  ·  Benchmark + Auditoría de Optimización
--------------------------------------------------------------------------------
 Autor : Cristian Alonso
 Web   : https://cristianac.es
 Lic.  : MIT
--------------------------------------------------------------------------------
 Mide el rendimiento real del equipo (CPU, RAM, almacenamiento), audita la
 configuración del sistema y estima el porcentaje de mejora alcanzable con
 cada optimización, ordenado por impacto/esfuerzo.

 Incluye una ficha por componente (procesador, memoria, almacenamiento,
 gráfica y sistema) que reúne inventario, nota medida y mejoras agrupadas.
 Aparece en la consola y en los tres formatos de exportación.

 IMPORTANTE: la herramienta es de SOLO LECTURA. No modifica el registro ni la
 configuración. Con --export-plan genera un script PowerShell comentado que
 puedes revisar y ejecutar tú mismo.

 Uso:
   python pcbench.py                      # análisis completo
   python pcbench.py --quick              # benchmark rápido (sin test de disco largo)
   python pcbench.py --no-bench           # solo auditoría
   python pcbench.py --disk-size 1024     # tamaño del fichero de test de disco (MB)
   python pcbench.py --export-plan        # genera plan_optimizacion.ps1
   python pcbench.py --html informe.html --json datos.json

 Requisitos: Python 3.9+ · pip install psutil  (opcional: py-cpuinfo)
================================================================================
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Any, Callable

# ------------------------------------------------------------------------------
# BRANDING
# ------------------------------------------------------------------------------
APP_NAME = "PCBench Suite"
APP_VERSION = "1.5.0"
AUTHOR = "Cristian Alonso"
WEBSITE = "cristianac.es"
WEBSITE_URL = "https://cristianac.es"

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MAC = platform.system() == "Darwin"

CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

try:
    import psutil
except ImportError:  # pragma: no cover
    print("\n[!] Falta la dependencia 'psutil'.\n    Instálala con:  pip install psutil\n")
    sys.exit(1)

if IS_WINDOWS:
    import winreg  # noqa: E402
else:
    winreg = None


# ==============================================================================
# 1. UTILIDADES DE CONSOLA
# ==============================================================================
class C:
    """Códigos ANSI. Se desactivan solos si la terminal no los soporta."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GREY = "\033[90m"

    @classmethod
    def disable(cls) -> None:
        for attr in dir(cls):
            if attr.isupper():
                setattr(cls, attr, "")


def enable_ansi() -> None:
    """Activa las secuencias VT100 en consolas de Windows."""
    if not sys.stdout.isatty():
        C.disable()
        return
    if IS_WINDOWS:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            C.disable()


BOX_W = 78


def banner() -> None:
    line = "═" * BOX_W
    print(f"\n{C.CYAN}╔{line}╗{C.RESET}")
    title = f"{APP_NAME}  v{APP_VERSION}"
    print(f"{C.CYAN}║{C.RESET}{C.BOLD}{title.center(BOX_W)}{C.RESET}{C.CYAN}║{C.RESET}")
    sub = "Benchmark · Auditoría · Estimación de mejora"
    print(f"{C.CYAN}║{C.RESET}{C.DIM}{sub.center(BOX_W)}{C.RESET}{C.CYAN}║{C.RESET}")
    print(f"{C.CYAN}╠{line}╣{C.RESET}")
    brand = f"{AUTHOR}   ·   {WEBSITE}"
    print(f"{C.CYAN}║{C.RESET}{C.MAGENTA}{brand.center(BOX_W)}{C.RESET}{C.CYAN}║{C.RESET}")
    print(f"{C.CYAN}╚{line}╝{C.RESET}")


def section(title: str) -> None:
    print(f"\n{C.BOLD}{C.BLUE}▌ {title.upper()}{C.RESET}")
    print(f"{C.GREY}{'─' * BOX_W}{C.RESET}")


def kv(key: str, value: Any, width: int = 26) -> None:
    print(f"  {C.GREY}{str(key).ljust(width, '.')}{C.RESET} {value}")


def bar(pct: float, width: int = 28, good_high: bool = True) -> str:
    pct = max(0.0, min(150.0, float(pct)))
    filled = int(round(width * min(pct, 100) / 100))
    if good_high:
        color = C.GREEN if pct >= 85 else C.YELLOW if pct >= 55 else C.RED
    else:
        color = C.RED if pct >= 85 else C.YELLOW if pct >= 55 else C.GREEN
    return f"{color}{'█' * filled}{C.GREY}{'░' * (width - filled)}{C.RESET}"


def spinner_step(msg: str) -> None:
    print(f"  {C.CYAN}▸{C.RESET} {msg} ", end="", flush=True)


def spinner_done(detail: str = "ok", ok: bool = True) -> None:
    mark = f"{C.GREEN}✓{C.RESET}" if ok else f"{C.YELLOW}~{C.RESET}"
    print(f"{mark} {C.DIM}{detail}{C.RESET}")


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} EB"


def grade(score: float) -> tuple[str, str]:
    """Devuelve (letra, color) para un score normalizado (100 = referencia)."""
    table = [
        (140, "S", C.MAGENTA),
        (115, "A+", C.GREEN),
        (95, "A", C.GREEN),
        (80, "B", C.CYAN),
        (60, "C", C.YELLOW),
        (40, "D", C.YELLOW),
        (0, "F", C.RED),
    ]
    for threshold, letter, color in table:
        if score >= threshold:
            return letter, color
    return "F", C.RED


# ==============================================================================
# 2. HELPERS DE SISTEMA
# ==============================================================================
def run_cmd(args: list[str], timeout: int = 25) -> str | None:
    """Ejecuta un comando y devuelve stdout, o None si falla."""
    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            errors="replace",
        )
        return res.stdout.strip() if res.returncode == 0 else None
    except Exception:
        return None


def ps(command: str, timeout: int = 30) -> Any:
    """Ejecuta PowerShell devolviendo JSON parseado (o None)."""
    if not IS_WINDOWS:
        return None
    wrapped = f"$ProgressPreference='SilentlyContinue'; {command}"
    out = run_cmd(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", wrapped],
        timeout=timeout,
    )
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def ps_json(select: str, timeout: int = 30) -> list[dict]:
    """Atajo: devuelve siempre una lista de dicts."""
    data = ps(f"{select} | ConvertTo-Json -Depth 3 -Compress", timeout=timeout)
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def reg_read(hive: int, path: str, name: str) -> Any:
    if not IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(hive, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except (FileNotFoundError, OSError):
        return None


def reg_list_values(hive: int, path: str) -> dict[str, Any]:
    if not IS_WINDOWS:
        return {}
    out: dict[str, Any] = {}
    try:
        with winreg.OpenKey(hive, path) as key:
            count = winreg.QueryInfoKey(key)[1]
            for i in range(count):
                name, value, _ = winreg.EnumValue(key, i)
                out[name] = value
    except (FileNotFoundError, OSError):
        pass
    return out


def is_admin() -> bool:
    try:
        if IS_WINDOWS:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


# ==============================================================================
# 3. CARGAS DE TRABAJO DEL BENCHMARK
#    Trabajo FIJO, se mide el tiempo. Deterministas y sin dependencias.
# ==============================================================================
def work_sieve(limit: int) -> int:
    """Criba de Eratóstenes con marcado por bucle.

    Nota: la versión "elegante" con slicing (sieve[n*n::n] = ...) delega el
    marcado en C y acaba midiendo el ancho de banda del memset, no la CPU.
    El bucle explícito es lo que queremos aquí: enteros + salto de rama.
    """
    sieve = bytearray([1]) * (limit + 1)
    sieve[0] = sieve[1] = 0
    for n in range(2, int(limit**0.5) + 1):
        if sieve[n]:
            for m in range(n * n, limit + 1, n):
                sieve[m] = 0
    return sum(sieve)


def work_float(iterations: int) -> float:
    """Bucle de coma flotante + llamadas a libm."""
    acc = 0.0
    sin, cos, sqrt, log = math.sin, math.cos, math.sqrt, math.log
    for i in range(1, iterations + 1):
        x = i * 0.000173
        acc += sin(x) * cos(x) + sqrt(x + 1.0) - log(x + 1.0)
    return acc


def work_hash(megabytes: int) -> str:
    """SHA-256: mide la ruta criptográfica (SHA-EXT/AES-NI vía OpenSSL)."""
    block = bytes(range(256)) * 4096  # 1 MiB
    h = hashlib.sha256()
    for _ in range(megabytes):
        h.update(block)
    return h.hexdigest()


_CORPUS_CACHE: dict[int, bytes] = {}
_CORPUS_WORDS = [f"token{i:04d}".encode() for i in range(2000)]


def build_corpus(megabytes: int) -> bytes:
    """Corpus pseudotexto reproducible. Se cachea: construirlo no es la prueba."""
    if megabytes in _CORPUS_CACHE:
        return _CORPUS_CACHE[megabytes]
    rnd = random.Random(4242)
    target = megabytes * 1024 * 1024
    out = bytearray()
    while len(out) < target:
        out += b" ".join(rnd.choice(_CORPUS_WORDS) for _ in range(400)) + b"\n"
    data = bytes(out[:target])
    _CORPUS_CACHE[megabytes] = data
    return data


def work_compress(megabytes: int) -> int:
    """Compresión zlib nivel 6 sobre datos tipo texto (el caso realista y
    más costoso: datos aleatorios se almacenan sin comprimir y falsean la medida)."""
    return len(zlib.compress(build_corpus(megabytes), 6))


def work_memcpy(mb: int, passes: int) -> float:
    """Ancho de banda de memoria (copias grandes). Devuelve GB/s."""
    size = mb * 1024 * 1024
    unit = 8 * 1024 * 1024
    src = bytearray(os.urandom(min(size, unit))) * max(1, size // unit)
    dst = bytearray(len(src))
    view_src, view_dst = memoryview(src), memoryview(dst)
    view_dst[:] = view_src  # calentar páginas
    t0 = time.perf_counter()
    for _ in range(passes):
        view_dst[:] = view_src
    elapsed = time.perf_counter() - t0
    return ((len(src) * passes) / 1e9) / elapsed if elapsed > 0 else 0.0


def _mp_unit(_seed: int) -> float:
    """Unidad de trabajo para el test multinúcleo (debe ser top-level por pickle)."""
    t0 = time.perf_counter()
    work_sieve(1_200_000)
    work_float(700_000)
    return time.perf_counter() - t0


# ==============================================================================
# 4. VALORES DE REFERENCIA
#    Calibrados sobre un equipo de gama media reciente (Ryzen 5 5600 / i5-12400,
#    32 GB DDR4-3200 dual channel, NVMe PCIe 3.0). Ese equipo puntúa ~100.
#    Son heurísticos: sirven para comparar y detectar cuellos de botella.
# ==============================================================================
REFERENCE = {
    "sieve_s": 0.50,          # segundos · criba con límite 4.000.000
    "float_s": 0.47,          # segundos · 2.500.000 iteraciones
    "hash_s": 0.42,           # segundos · 512 MiB SHA-256 (~1,2 GB/s)
    "compress_s": 0.55,       # segundos · 12 MiB zlib nivel 6
    "mem_gbs": 10.0,          # GB/s de copia monohilo
    "mp_tps": 34.0,           # unidades de trabajo por segundo (multihilo)
    "disk_write_mbs": 850.0,
    "disk_read_mbs": 1700.0,
    "disk_iops_4k": 22000.0,
}

# El intérprete influye mucho: Python 3.11+ es un 25-40% más rápido que 3.10 en
# código puro. Sin este ajuste, un equipo potente con Python 3.9 puntuaría bajo
# por culpa del intérprete, no del hardware.
PY_ADJUST = 1.0 if sys.version_info >= (3, 11) else 1.35
CPU_KEYS = ("sieve_s", "float_s", "compress_s")

# Techo por componente al agregar la nota global: evita que un disco RAM o una
# lectura servida desde caché disparen la puntuación total.
SCORE_CAP = 250.0

# Pesos para la nota global
WEIGHTS = {"cpu_single": 0.26, "cpu_multi": 0.24, "memory": 0.16, "disk": 0.34}


# ==============================================================================
# 5. INVENTARIO DEL SISTEMA
# ==============================================================================
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


# ==============================================================================
# 6. MOTOR DE BENCHMARK
# ==============================================================================
@dataclass
class BenchResult:
    name: str
    unit: str
    raw: float
    score: float
    detail: str = ""


class Benchmark:
    def __init__(self, quick: bool = False, disk_size_mb: int = 512, skip_disk: bool = False,
                 target_dir: str | None = None):
        self.quick = quick
        self.disk_size_mb = 192 if quick else disk_size_mb
        self.skip_disk = skip_disk
        self.target_dir = target_dir or tempfile.gettempdir()
        self.results: dict[str, BenchResult] = {}
        self.thermal_samples: list[float] = []
        self.freq_samples: list[float] = []
        self.disk_on_ram = False
        self.scaling_efficiency: float | None = None

    # -- helpers ----------------------------------------------------------------
    def _timed(self, fn: Callable, *args, runs: int | None = None) -> float:
        """Ejecuta y devuelve el MEJOR tiempo (menos ruido del scheduler)."""
        runs = runs or (1 if self.quick else 3)
        times = []
        for _ in range(runs):
            t0 = time.perf_counter()
            fn(*args)
            times.append(time.perf_counter() - t0)
            self._sample_sensors()
        return min(times)

    def _sample_sensors(self) -> None:
        try:
            freq = psutil.cpu_freq()
            if freq and freq.current:
                self.freq_samples.append(freq.current)
        except Exception:
            pass
        temp = read_cpu_temperature()
        if temp:
            self.thermal_samples.append(temp)

    def _register(self, key: str, name: str, unit: str, raw: float, score: float,
                  detail: str = "") -> None:
        self.results[key] = BenchResult(name, unit, raw, max(0.0, score), detail)

    def _target_fstype(self) -> str:
        """Sistema de ficheros del directorio de pruebas (para detectar ramdisks)."""
        best, best_len = "", -1
        try:
            target = os.path.realpath(self.target_dir)
            for part in psutil.disk_partitions(all=True):
                mp = os.path.realpath(part.mountpoint)
                if target.startswith(mp) and len(mp) > best_len:
                    best, best_len = part.fstype.lower(), len(mp)
        except Exception:
            return ""
        return best

    # -- pruebas ---------------------------------------------------------------
    def run_cpu_single(self) -> None:
        sub_scores = []

        spinner_step("CPU 1T · criba de primos".ljust(38))
        t = self._timed(work_sieve, 4_000_000)
        s = REFERENCE["sieve_s"] * PY_ADJUST / t * 100
        sub_scores.append(s)
        spinner_done(f"{t:.3f} s  → {s:.0f} pts")

        spinner_step("CPU 1T · coma flotante".ljust(38))
        t = self._timed(work_float, 2_500_000)
        s = REFERENCE["float_s"] * PY_ADJUST / t * 100
        sub_scores.append(s)
        spinner_done(f"{t:.3f} s  → {s:.0f} pts")

        mib = 256 if self.quick else 512
        spinner_step(f"CPU 1T · SHA-256 ({mib} MiB)".ljust(38))
        t = self._timed(work_hash, mib)
        s = REFERENCE["hash_s"] * (mib / 512) / t * 100   # sin PY_ADJUST: corre en C
        sub_scores.append(s)
        spinner_done(f"{t:.3f} s  → {s:.0f} pts  ({mib / t:.0f} MB/s)")

        spinner_step("CPU 1T · preparando corpus".ljust(38))
        build_corpus(12)
        spinner_done("12 MiB")
        spinner_step("CPU 1T · compresión zlib (12 MiB)".ljust(38))
        t = self._timed(work_compress, 12, runs=1 if self.quick else 2)
        s = REFERENCE["compress_s"] / t * 100
        sub_scores.append(s)
        spinner_done(f"{t:.3f} s  → {s:.0f} pts")

        geo = statistics.geometric_mean([max(1.0, x) for x in sub_scores])
        self._register("cpu_single", "CPU monohilo", "pts", geo, geo,
                       f"media geométrica de {len(sub_scores)} subtests")

    def run_cpu_multi(self) -> None:
        threads = psutil.cpu_count(logical=True) or 1
        tasks = threads * (2 if self.quick else 4)
        spinner_step(f"CPU {threads}T · carga paralela ({tasks} uds)".ljust(38))
        t0 = time.perf_counter()
        try:
            with Pool(processes=threads) as pool:
                per_task = pool.map(_mp_unit, range(tasks))
        except Exception as exc:  # entornos sin fork/spawn disponible
            spinner_done(f"no disponible ({type(exc).__name__})", ok=False)
            return
        wall = time.perf_counter() - t0
        tps = tasks / wall
        score = tps / REFERENCE["mp_tps"] * 100
        single_task = statistics.median(per_task)
        ideal_tps = threads / single_task
        efficiency = min(100.0, tps / ideal_tps * 100) if ideal_tps else 0.0
        self._register("cpu_multi", "CPU multihilo", "uds/s", tps, score,
                       f"{threads} hilos · escalado {efficiency:.0f}%")
        self.scaling_efficiency = efficiency
        spinner_done(f"{tps:.2f} uds/s → {score:.0f} pts  (escalado {efficiency:.0f}%)")

    def run_memory(self) -> None:
        spinner_step("RAM · ancho de banda".ljust(38))
        mb = 64 if self.quick else 128
        passes = 6 if self.quick else 12
        gbs = max(work_memcpy(mb, passes) for _ in range(1 if self.quick else 2))
        score = gbs / REFERENCE["mem_gbs"] * 100
        self._register("memory", "Memoria", "GB/s", gbs, score, f"copias de {mb} MiB")
        spinner_done(f"{gbs:.2f} GB/s → {score:.0f} pts")

    def run_disk(self) -> None:
        if self.skip_disk:
            return
        fstype = self._target_fstype()
        if fstype in ("tmpfs", "ramfs", "devtmpfs"):
            print(f"  {C.YELLOW}⚠ {self.target_dir} está en {fstype} (memoria RAM). "
                  f"Los resultados de disco NO serán reales.{C.RESET}")
            print(f"    {C.DIM}Usa --disk-path para apuntar a una carpeta del disco "
                  f"que quieras medir.{C.RESET}")
            self.disk_on_ram = True

        path = Path(self.target_dir) / f".pcbench_{os.getpid()}.tmp"
        size = self.disk_size_mb * 1024 * 1024
        block = os.urandom(1024 * 1024)
        try:
            free = shutil.disk_usage(self.target_dir).free
            if free < size * 2.5:
                spinner_step("Disco · test".ljust(38))
                spinner_done("omitido: espacio libre insuficiente", ok=False)
                return

            # --- Escritura secuencial ---
            spinner_step(f"Disco · escritura secuencial ({self.disk_size_mb} MB)".ljust(38))
            fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
            t0 = time.perf_counter()
            written = 0
            while written < size:
                written += os.write(fd, block)
            os.fsync(fd)
            wt = time.perf_counter() - t0
            os.close(fd)
            wmbs = (written / 1e6) / wt
            wscore = wmbs / REFERENCE["disk_write_mbs"] * 100
            self._register("disk_write", "Disco · escritura", "MB/s", wmbs, wscore)
            spinner_done(f"{wmbs:.0f} MB/s → {wscore:.0f} pts")

            # --- Lectura secuencial ---
            spinner_step("Disco · lectura secuencial".ljust(38))
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            fd = os.open(str(path), flags)
            t0 = time.perf_counter()
            read_total = 0
            while True:
                chunk = os.read(fd, 4 * 1024 * 1024)
                if not chunk:
                    break
                read_total += len(chunk)
            rt = time.perf_counter() - t0
            rmbs = (read_total / 1e6) / rt
            rscore = rmbs / REFERENCE["disk_read_mbs"] * 100
            self._register("disk_read", "Disco · lectura", "MB/s", rmbs, rscore,
                           "puede estar influido por la caché del SO")
            spinner_done(f"{rmbs:.0f} MB/s → {rscore:.0f} pts")

            # --- IOPS aleatorias 4K ---
            spinner_step("Disco · IOPS aleatorias 4K".ljust(38))
            ops = 4000 if self.quick else 12000
            max_off = max(0, size - 4096)
            rnd = random.Random(7)
            offsets = [rnd.randrange(0, max_off, 4096) for _ in range(ops)]
            t0 = time.perf_counter()
            for off in offsets:
                os.lseek(fd, off, os.SEEK_SET)
                os.read(fd, 4096)
            it = time.perf_counter() - t0
            os.close(fd)
            iops = ops / it
            iscore = iops / REFERENCE["disk_iops_4k"] * 100
            self._register("disk_iops", "Disco · IOPS 4K", "IOPS", iops, iscore,
                           "cifras muy altas (>200k) indican que el fichero cabía en la caché "
                           "del SO: usa --disk-size 2048 o mayor para medir el disco de verdad")
            spinner_done(f"{iops:,.0f} IOPS → {iscore:.0f} pts")
        except Exception as exc:
            spinner_done(f"error: {exc}", ok=False)
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def run_all(self) -> None:
        section("Benchmark en ejecución")
        print(f"  {C.DIM}Cierra el resto de aplicaciones para obtener medidas fiables.{C.RESET}\n")
        self.run_cpu_single()
        self.run_cpu_multi()
        self.run_memory()
        self.run_disk()

    # -- agregación ------------------------------------------------------------
    def component_scores(self) -> dict[str, float]:
        disk_parts = [self.results[k].score for k in ("disk_write", "disk_read", "disk_iops")
                      if k in self.results]
        comp = {}
        if "cpu_single" in self.results:
            comp["cpu_single"] = self.results["cpu_single"].score
        if "cpu_multi" in self.results:
            comp["cpu_multi"] = self.results["cpu_multi"].score
        if "memory" in self.results:
            comp["memory"] = self.results["memory"].score
        if disk_parts:
            # IOPS pesa más: es lo que define la sensación de fluidez
            weights = {"disk_write": 0.25, "disk_read": 0.3, "disk_iops": 0.45}
            total_w = sum(w for k, w in weights.items() if k in self.results)
            comp["disk"] = sum(self.results[k].score * w for k, w in weights.items()
                               if k in self.results) / total_w
        return comp

    def overall(self) -> float:
        """Nota global ponderada. Cada componente se limita a SCORE_CAP para que
        un disco RAM o una lectura desde caché no inflen el resultado."""
        comp = self.component_scores()
        if not comp:
            return 0.0
        total_w = sum(WEIGHTS[k] for k in comp)
        return sum(min(comp[k], SCORE_CAP) * WEIGHTS[k] for k in comp) / total_w


def read_cpu_temperature() -> float | None:
    """Temperatura de CPU (°C) por el método disponible en la plataforma."""
    try:
        temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
        for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
            if key in temps and temps[key]:
                return max(t.current for t in temps[key] if t.current)
        for entries in temps.values():
            vals = [t.current for t in entries if t.current]
            if vals:
                return max(vals)
    except (AttributeError, OSError):
        pass
    if IS_WINDOWS:
        data = ps_json('Get-CimInstance -Namespace "root/WMI" -ClassName MSAcpi_ThermalZoneTemperature '
                       '-ErrorAction SilentlyContinue | Select-Object CurrentTemperature', timeout=12)
        for d in data:
            raw = d.get("CurrentTemperature")
            if raw:
                celsius = (int(raw) / 10.0) - 273.15
                if 10 < celsius < 120:
                    return round(celsius, 1)
    return None


# ==============================================================================
# 7. AUDITORÍA DEL SISTEMA
# ==============================================================================
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


# ==============================================================================
# 8. PROYECCIÓN DE MEJORA
# ==============================================================================
def combine_gains(gains: list[float]) -> float:
    """Combina mejoras con rendimientos decrecientes: 1 - Π(1 - g)."""
    remaining = 1.0
    for g in gains:
        remaining *= (1 - max(0.0, min(0.9, g)))
    return 1 - remaining


def project_improvement(bench: Benchmark | None, findings: list[Finding]) -> dict[str, Any]:
    by_component: dict[str, list[float]] = {}
    by_category: dict[str, list[float]] = {}
    for f in findings:
        if f.gain <= 0:
            continue
        by_component.setdefault(f.component, []).append(f.gain)
        by_category.setdefault(f.category, []).append(f.gain)

    component_gain = {k: combine_gains(v) for k, v in by_component.items()}
    category_gain = {k: combine_gains(v) for k, v in by_category.items()}

    result: dict[str, Any] = {
        "component_gain": component_gain,
        "category_gain": category_gain,
        "system_gain": component_gain.get("system", 0.0),
    }

    if bench:
        current = bench.component_scores()
        projected = {}
        for key, score in current.items():
            gain = component_gain.get(key, 0.0)
            projected[key] = score * (1 + gain)
        cur_overall = bench.overall()
        total_w = sum(WEIGHTS[k] for k in projected) or 1.0
        # Mismo techo que en overall(): sin él, un componente con una medida
        # anómala (caché, ramdisk) convierte la proyección en un número absurdo.
        proj_overall = sum(min(projected[k], SCORE_CAP) * WEIGHTS[k]
                           for k in projected) / total_w
        # Las mejoras de "system" no aparecen en los scores sintéticos pero sí en la experiencia
        experiential = proj_overall * (1 + component_gain.get("system", 0.0) * 0.5)
        result.update({
            "current_components": current,
            "projected_components": projected,
            "current_overall": cur_overall,
            "projected_overall": proj_overall,
            "projected_experiential": experiential,
            "headroom_pct": (proj_overall / cur_overall - 1) * 100 if cur_overall else 0.0,
            "experiential_pct": (experiential / cur_overall - 1) * 100 if cur_overall else 0.0,
        })
    return result


def priority_rank(f: Finding) -> tuple:
    effort_w = {"bajo": 0, "medio": 1, "alto": 2}.get(f.effort, 1)
    return (-f.gain / (1 + effort_w * 0.6), SEVERITY_ORDER.get(f.severity, 9))


# ==============================================================================
# 8b. FICHA POR COMPONENTE
#     Une, pieza a pieza, el inventario + la nota medida + las mejoras que le
#     corresponden. Es la vista que responde a «¿qué tengo, qué tal va y qué
#     puedo hacer con ello?» sin tener que cruzar tres secciones a mano.
# ==============================================================================
# (clave, etiqueta, componentes de los hallazgos, claves del benchmark)
COMPONENT_GROUPS: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    ("cpu", "Procesador", ("cpu_single", "cpu_multi"), ("cpu_single", "cpu_multi")),
    ("memory", "Memoria RAM", ("memory",), ("memory",)),
    ("disk", "Almacenamiento", ("disk",), ("disk_write", "disk_read", "disk_iops")),
    ("gpu", "Gráfica", (), ()),
    ("system", "Sistema y software", ("system",), ()),
]

COMPONENT_TO_GROUP = {"cpu_single": "cpu", "cpu_multi": "cpu", "memory": "memory",
                      "disk": "disk", "system": "system"}

# El campo `component` de un hallazgo existe para la proyección: dice a qué score
# sintético se aplica la ganancia. gpu_driver es "system" porque no hay ninguna
# medida de GPU que corregir. En la ficha, en cambio, el sitio natural para leerlo
# es junto a la gráfica, así que se reasigna por id sin tocar la proyección.
FINDING_GROUP_OVERRIDES = {"gpu_driver": "gpu"}


def finding_group(f: Finding) -> str:
    return FINDING_GROUP_OVERRIDES.get(f.id) or COMPONENT_TO_GROUP.get(f.component, "system")


@dataclass
class ComponentCard:
    key: str
    label: str
    specs: list[tuple[str, str]] = field(default_factory=list)
    tests: list[BenchResult] = field(default_factory=list)
    measurable: bool = False   # ¿tiene nota sintética cuando el benchmark corre?
    score: float | None = None
    letter: str = "—"
    findings: list[Finding] = field(default_factory=list)
    gain: float = 0.0
    projected_score: float | None = None


def _component_specs(key: str, si: SystemInfo, bench: Benchmark | None,
                     auditor: Auditor) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    if key == "cpu":
        out.append(("Modelo", si.cpu_name or "Desconocido"))
        out.append(("Núcleos / hilos", f"{si.cpu_cores} / {si.cpu_threads}"))
        if si.cpu_max_mhz:
            out.append(("Frecuencia máxima", f"{si.cpu_max_mhz:.0f} MHz"))
        samples = bench.freq_samples if bench else []
        if samples:
            sustained = statistics.median(samples)
            pct = f" ({sustained / si.cpu_max_mhz * 100:.0f}% del máximo)" if si.cpu_max_mhz else ""
            out.append(("Frecuencia sostenida", f"{sustained:.0f} MHz{pct}"))
        temps = bench.thermal_samples if bench else []
        out.append(("Temperatura bajo carga", f"{max(temps):.0f} °C" if temps else "no disponible"))
        if bench and bench.scaling_efficiency is not None:
            out.append(("Escalado multihilo", f"{bench.scaling_efficiency:.0f}%"))

    elif key == "memory":
        total_gb = si.ram_total / 1024**3
        out.append(("Capacidad total", f"{total_gb:.1f} GB"))
        if si.ram_total:
            used_pct = (1 - si.ram_available / si.ram_total) * 100
            out.append(("Libre al medir", f"{human_bytes(si.ram_available)} "
                                          f"({used_pct:.0f}% en uso)"))
        if si.ram_speed_mhz:
            out.append(("Velocidad", f"{si.ram_speed_mhz} MT/s"))
        sticks = [s for s in si.ram_sticks if s["capacity"] > 0]
        if sticks:
            out.append(("Módulos instalados",
                        f"{len(sticks)}" + (" (single channel)" if len(sticks) == 1 else "")))
            for s in sticks:
                desc = human_bytes(s["capacity"])
                if s["speed"]:
                    desc += f" @ {s['speed']} MT/s"
                extra = " ".join(x for x in (s["vendor"], s["part"]) if x)
                out.append((f"  {s['slot']}", f"{desc}{'  ·  ' + extra if extra else ''}"))
        else:
            out.append(("Módulos instalados", "sin datos (requiere Windows)"))

    elif key == "disk":
        out.append(("Unidad de sistema", f"{si.system_drive}  ·  {si.system_drive_media}"))
        seen: set[tuple] = set()
        for d in si.disks:
            for c in d.get("candidates", []):
                ident = (c.get("name"), c.get("media"), c.get("health"))
                if ident in seen or not c.get("name"):
                    continue
                seen.add(ident)
                media = c.get("media")
                out.append(("Disco físico", f"{c['name']}  ·  {media or 'tipo n/d'}"
                                            f"  ·  salud {c.get('health') or 'n/d'}"))
        for d in si.disks:
            if d["total"] > 5 * 1024**3:
                out.append((f"Volumen {d['mount']}",
                            f"{human_bytes(d['free'])} libres de {human_bytes(d['total'])} "
                            f"({100 - d['percent']:.0f}% libre)  [{d['fstype']}]"))

    elif key == "gpu":
        for g in si.gpus:
            out.append(("Adaptador", str(g.get("name"))))
            age = g.get("driver_age_days")
            out.append(("  Driver", f"{g.get('driver')}  ·  {g.get('driver_date') or 'fecha n/d'}"
                                    + (f"  ({age} días)" if age is not None else "")))
            if g.get("vram"):
                out.append(("  VRAM declarada", human_bytes(g["vram"])))
            if g.get("resolution"):
                out.append(("  Modo de pantalla", str(g["resolution"])))

    elif key == "system":
        out.append(("Sistema operativo", si.os_name))
        out.append(("Versión", si.os_build))
        if si.os_install_date:
            out.append(("Instalado el", f"{si.os_install_date}  "
                                        f"({(si.os_age_days or 0) / 365.25:.1f} años)"))
        if si.bios_date:
            out.append(("BIOS", si.bios_date))
        out.append(("Tiempo encendido", f"{si.uptime_hours:.1f} h"))
        out.append(("Formato", "portátil" if si.is_laptop else "sobremesa"))
        startup = getattr(auditor, "startup_items", [])
        if startup:
            out.append(("Programas de inicio", str(len(startup))))
        top = getattr(auditor, "top_processes", [])
        if top:
            out.append(("Procesos más pesados",
                        ", ".join(f"{p['name']} ({human_bytes(p['rss'])})" for p in top[:3])))
        out.append(("Privilegios", "administrador" if si.is_admin else "usuario estándar"))
        out.append(("Python", si.python_version))

    return out


def build_component_cards(si: SystemInfo, bench: Benchmark | None,
                          auditor: Auditor) -> list[ComponentCard]:
    comp_scores = bench.component_scores() if bench else {}
    cards: list[ComponentCard] = []
    for key, label, comps, bench_keys in COMPONENT_GROUPS:
        card = ComponentCard(key=key, label=label)
        card.measurable = any(k in WEIGHTS for k in comps)
        card.specs = _component_specs(key, si, bench, auditor)
        if bench:
            card.tests = [bench.results[k] for k in bench_keys if k in bench.results]

        scored = {k: comp_scores[k] for k in comps if k in comp_scores}
        if scored:
            # Mismo techo y pesos que la nota global, para que las notas por
            # componente sumen exactamente la puntuación total.
            total_w = sum(WEIGHTS[k] for k in scored)
            card.score = sum(min(v, SCORE_CAP) * WEIGHTS[k] for k, v in scored.items()) / total_w
            card.letter = grade(card.score)[0]

        card.findings = sorted([f for f in auditor.findings if finding_group(f) == key],
                               key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.gain))
        card.gain = combine_gains([f.gain for f in card.findings if f.gain > 0])
        if card.score is not None:
            card.projected_score = card.score * (1 + card.gain)

        if card.specs or card.tests or card.findings:
            cards.append(card)
    return cards


# ==============================================================================
# 9. INFORMES
# ==============================================================================
def _no_score_text(card: ComponentCard) -> str:
    """Distingue «no medido en esta ejecución» de «no tiene medida posible»."""
    return "sin medir en esta ejecución" if card.measurable else "sin nota sintética"


def print_component_cards(cards: list[ComponentCard]) -> None:
    section("Ficha por componente")
    print(f"  {C.DIM}Cada pieza con su inventario, la nota que ha sacado y las mejoras "
          f"que le corresponden.{C.RESET}\n")
    for card in cards:
        if card.score is not None:
            letter, color = grade(card.score)
            head = f"{color}{C.BOLD}{card.score:>5.0f} pts  ·  {letter.ljust(2)}{C.RESET} " \
                   f"{bar(card.score, 18)}"
        else:
            head = f"{C.GREY}{_no_score_text(card)}{C.RESET}"
        print(f"  {C.BOLD}{card.label.upper().ljust(22)}{C.RESET} {head}")

        for k, v in card.specs:
            print(f"      {C.GREY}{str(k).ljust(24, '.')}{C.RESET} {v}")

        if card.tests:
            print(f"      {C.CYAN}Pruebas medidas:{C.RESET}")
            for r in card.tests:
                measure = f"{r.raw:,.0f} {r.unit}" if r.unit == "IOPS" else f"{r.raw:,.2f} {r.unit}"
                letter, color = grade(r.score)
                print(f"        {r.name.ljust(22)}{measure.rjust(16)}   "
                      f"{color}{r.score:>5.0f} pts  {letter}{C.RESET}")

        if card.findings:
            total = f"  ·  {C.GREEN}+{card.gain * 100:.0f}% combinado{C.RESET}" if card.gain > 0.005 else ""
            print(f"      {C.CYAN}Mejoras aplicables ({len(card.findings)}){C.RESET}{total}")
            for f in card.findings:
                tag = (f"{C.GREEN}+{f.gain * 100:>3.0f}%{C.RESET}" if f.gain > 0
                       else f"{C.GREY} n/a{C.RESET}")
                print(f"        {tag}  {f.title}")
                print(f"              {sev_label(f.severity)}  {C.DIM}·  esfuerzo {f.effort}"
                      f"  ·  riesgo {f.risk}{C.RESET}")
            if card.projected_score is not None and card.gain > 0.005:
                print(f"        {C.DIM}→ tras aplicarlas: {C.RESET}{C.GREEN}"
                      f"{card.projected_score:.0f} pts{C.RESET}")
        else:
            print(f"      {C.GREEN}Sin mejoras pendientes.{C.RESET}")
        print()


def print_report(si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                 projection: dict[str, Any]) -> None:
    # --- Inventario ---
    section("Inventario del equipo")
    kv("Equipo", f"{si.hostname}{'  (portátil)' if si.is_laptop else ''}")
    kv("Sistema operativo", f"{si.os_name} · {si.os_build}")
    if si.os_install_date:
        kv("Instalado el", f"{si.os_install_date}  ({(si.os_age_days or 0) / 365.25:.1f} años)")
    kv("Tiempo encendido", f"{si.uptime_hours:.1f} h")
    kv("CPU", si.cpu_name)
    kv("Núcleos / hilos", f"{si.cpu_cores} / {si.cpu_threads}"
                          + (f"  ·  máx {si.cpu_max_mhz:.0f} MHz" if si.cpu_max_mhz else ""))
    ram_desc = f"{si.ram_total / 1024**3:.1f} GB"
    if si.ram_speed_mhz:
        ram_desc += f" @ {si.ram_speed_mhz} MT/s"
    if si.ram_channels:
        ram_desc += f" · {si.ram_channels} módulo(s)"
    kv("Memoria", ram_desc)
    for g in si.gpus:
        kv("GPU", f"{g['name']}  ·  driver {g.get('driver')} ({g.get('driver_date') or '?'})")
    kv("Disco de sistema", f"{si.system_drive}  ·  {si.system_drive_media}")
    for d in si.disks:
        if d["total"] > 5 * 1024**3:
            kv(f"  {d['mount']}",
               f"{human_bytes(d['free'])} libres de {human_bytes(d['total'])}  "
               f"({100 - d['percent']:.0f}% libre)  [{d['fstype']}]")
    kv("Privilegios", "administrador" if si.is_admin else
       f"{C.YELLOW}usuario estándar (algunas comprobaciones limitadas){C.RESET}")

    # --- Resultados del benchmark ---
    if bench and bench.results:
        section("Resultados del benchmark")
        print(f"  {C.DIM}Escala: 100 pts = equipo de gama media de referencia "
              f"(Ryzen 5 5600 / i5-12400, DDR4-3200 dual channel, NVMe PCIe 3.0){C.RESET}")
        note = f"Python {platform.python_version()}"
        if PY_ADJUST != 1.0:
            note += f" · factor de compensación de intérprete ×{PY_ADJUST}"
        print(f"  {C.DIM}{note}{C.RESET}\n")
        if getattr(bench, "disk_on_ram", False):
            print(f"  {C.YELLOW}Los resultados de disco se midieron sobre un sistema de "
                  f"ficheros en RAM: ignóralos.{C.RESET}\n")
        print(f"  {'PRUEBA'.ljust(24)}{'MEDIDA'.rjust(16)}   {'PTS'.rjust(5)}  NOTA")
        print(f"  {C.GREY}{'─' * (BOX_W - 2)}{C.RESET}")
        for res in bench.results.values():
            letter, color = grade(res.score)
            measure = f"{res.raw:,.2f} {res.unit}" if res.unit != "IOPS" else f"{res.raw:,.0f} IOPS"
            print(f"  {res.name.ljust(24)}{measure.rjust(16)}   "
                  f"{color}{res.score:>5.0f}{C.RESET}  {color}{letter.ljust(3)}{C.RESET}"
                  f"{bar(res.score, 20)}")
            if res.detail:
                print(f"    {C.GREY}↳ {res.detail}{C.RESET}")

        overall = bench.overall()
        letter, color = grade(overall)
        print(f"\n  {C.BOLD}PUNTUACIÓN GLOBAL{C.RESET}  "
              f"{color}{C.BOLD}{overall:.0f} pts   ·   nota {letter}{C.RESET}   {bar(overall, 24)}")

        comp = bench.component_scores()
        weakest = min(comp, key=lambda k: comp[k]) if comp else None
        if weakest:
            label = {"cpu_single": "CPU monohilo", "cpu_multi": "CPU multihilo",
                     "memory": "memoria", "disk": "almacenamiento"}[weakest]
            print(f"  {C.YELLOW}▸ Cuello de botella principal: {label} "
                  f"({comp[weakest]:.0f} pts){C.RESET}")

    # --- Ficha por componente ---
    print_component_cards(build_component_cards(si, bench, auditor))

    # --- Hallazgos ---
    section("Hallazgos de la auditoría")
    findings = sorted(auditor.findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.gain))
    if not findings:
        print(f"  {C.GREEN}✓ No se han detectado problemas de configuración relevantes. "
              f"El sistema está bien ajustado.{C.RESET}")
    else:
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        summary = "  ".join(f"{sev_label(s)}: {n}" for s, n in
                            sorted(counts.items(), key=lambda x: SEVERITY_ORDER[x[0]]))
        print(f"  {auditor.checks_run} comprobaciones · {len(findings)} hallazgos     {summary}\n")
        for i, f in enumerate(findings, 1):
            sev = sev_label(f.severity)
            print(f"  {C.BOLD}{i}. {f.title}{C.RESET}")
            print(f"     {sev}  {C.GREY}·{C.RESET}  categoría: {f.category}  "
                  f"{C.GREY}·{C.RESET}  esfuerzo: {f.effort}  "
                  f"{C.GREY}·{C.RESET}  riesgo: {f.risk}")
            if f.gain > 0:
                print(f"     {C.GREEN}Mejora estimada: +{f.gain * 100:.0f}%{C.RESET} "
                      f"{C.DIM}({f.gain_note}){C.RESET}")
            for line in _wrap(f.detail, 70):
                print(f"     {C.DIM}{line}{C.RESET}")
            if f.steps:
                print(f"     {C.CYAN}Cómo solucionarlo:{C.RESET}")
                for s in f.steps:
                    for j, line in enumerate(_wrap(s, 66)):
                        print(f"       {'•' if j == 0 else ' '} {line}")
            print()

    # --- Proyección ---
    section("Proyección de mejora")
    if projection.get("current_overall"):
        cur = projection["current_overall"]
        proj = projection["projected_overall"]
        exp = projection["projected_experiential"]
        print(f"  {'Puntuación actual'.ljust(34)} {C.BOLD}{cur:>6.0f} pts{C.RESET}  {bar(cur, 22)}")
        print(f"  {'Tras optimizar (sintética)'.ljust(34)} {C.GREEN}{C.BOLD}{proj:>6.0f} pts{C.RESET}"
              f"  {bar(proj, 22)}   {C.GREEN}+{projection['headroom_pct']:.0f}%{C.RESET}")
        print(f"  {'Fluidez percibida estimada'.ljust(34)} {C.GREEN}{C.BOLD}{exp:>6.0f} pts{C.RESET}"
              f"  {bar(exp, 22)}   {C.GREEN}+{projection['experiential_pct']:.0f}%{C.RESET}")
        print()
        labels = {"cpu_single": "CPU monohilo", "cpu_multi": "CPU multihilo",
                  "memory": "Memoria", "disk": "Almacenamiento"}
        for key, score in projection.get("current_components", {}).items():
            gain = projection["component_gain"].get(key, 0.0)
            if gain > 0.005:
                print(f"    {labels.get(key, key).ljust(20)} {score:>5.0f} → "
                      f"{C.GREEN}{projection['projected_components'][key]:>5.0f} pts "
                      f"(+{gain * 100:.0f}%){C.RESET}")
        sysgain = projection.get("system_gain", 0.0)
        if sysgain > 0.005:
            print(f"    {'Arranque / fluidez'.ljust(20)} {C.GREY}sin métrica sintética{C.RESET} → "
                  f"{C.GREEN}+{sysgain * 100:.0f}% estimado{C.RESET}")

    if projection.get("category_gain"):
        print(f"\n  {C.BOLD}Margen por área:{C.RESET}")
        for cat, gain in sorted(projection["category_gain"].items(), key=lambda x: -x[1]):
            print(f"    {cat.capitalize().ljust(18)} {C.GREEN}+{gain * 100:>5.0f}%{C.RESET} "
                  f"{bar(min(100, gain * 100), 20)}")

    # --- Plan de acción ---
    section("Plan de acción priorizado")
    actionable = [f for f in auditor.findings if f.gain > 0]
    if not actionable:
        print(f"  {C.GREEN}Nada que priorizar: no hay acciones con retorno estimado.{C.RESET}")
    else:
        print(f"  {C.DIM}Ordenado por retorno estimado dividido por esfuerzo. "
              f"Aplica de arriba hacia abajo y vuelve a medir tras cada bloque.{C.RESET}\n")
        for i, f in enumerate(sorted(actionable, key=priority_rank), 1):
            tag = {"bajo": C.GREEN, "medio": C.YELLOW, "alto": C.RED}.get(f.effort, "")
            print(f"  {C.BOLD}{i:>2}.{C.RESET} {f.title}")
            print(f"      {C.GREEN}+{f.gain * 100:>3.0f}%{C.RESET}  "
                  f"esfuerzo {tag}{f.effort}{C.RESET}  ·  riesgo {f.risk}  ·  {f.category}")
        print(f"\n  {C.DIM}Recuerda: mide siempre antes y después. Aplicar diez cambios a la vez "
              f"impide saber cuál funcionó.{C.RESET}")

    if auditor.notes:
        print(f"\n  {C.YELLOW}Notas:{C.RESET}")
        for n in auditor.notes:
            for line in _wrap(n, 72):
                print(f"    {C.DIM}{line}{C.RESET}")

    # --- Veredicto ---
    section("Veredicto")
    verdict, extra = build_verdict(si, bench, auditor, projection)
    for line in _wrap(verdict, 74):
        print(f"  {line}")
    for e in extra:
        print(f"  {C.CYAN}▸{C.RESET} {e}")

    print(f"\n{C.GREY}{'─' * BOX_W}{C.RESET}")
    print(f"{C.MAGENTA}{APP_NAME} v{APP_VERSION}{C.RESET}  ·  {AUTHOR}  ·  "
          f"{C.CYAN}{WEBSITE_URL}{C.RESET}")
    print(f"{C.DIM}Informe generado el {datetime.now():%d/%m/%Y %H:%M}{C.RESET}\n")


def build_verdict(si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                  projection: dict[str, Any]) -> tuple[str, list[str]]:
    ids = {f.id for f in auditor.findings}
    extra: list[str] = []

    if "smart_warn" in ids:
        return ("Antes de cualquier optimización: hay un disco con salud degradada. Haz copia de "
                "seguridad ahora. Optimizar un equipo con un disco a punto de fallar es perder "
                "el tiempo en el mejor caso y perder datos en el peor.", extra)

    if "hdd_system" in ids:
        extra.append("Prioridad absoluta: migrar el sistema a un SSD. Todo lo demás es secundario.")
        return ("El cuello de botella es físico, no de configuración. Con el sistema en un disco "
                "mecánico, los ajustes de registro y la limpieza de inicio aportarán mejoras "
                "marginales. Un SSD de 500 GB cuesta poco y multiplica la respuesta del equipo.",
                extra)

    if "thermal_critical" in ids or "freq_low" in ids:
        extra.append("Empieza por la refrigeración: limpieza y pasta térmica antes de tocar software.")
        return ("El equipo no está entregando el rendimiento de su hardware por motivos térmicos o "
                "de límites de potencia. Reinstalar Windows no cambiaría nada aquí.", extra)

    headroom = projection.get("headroom_pct", 0.0)
    exp = projection.get("experiential_pct", 0.0)
    critical = sum(1 for f in auditor.findings if f.severity in ("critical", "high"))

    if critical == 0 and headroom < 8:
        extra.append("Mantenimiento: limpieza física anual y drivers al día. Nada más que hacer.")
        return ("El equipo está bien ajustado. El margen de mejora por software es residual: si "
                "necesitas más rendimiento, el camino es hardware (RAM, SSD más rápido, CPU/GPU), "
                "no optimización.", extra)

    if "os_stale" in ids and critical >= 2:
        extra.append("Orden recomendado: 1) limpieza de inicio y espacio  2) medir de nuevo  "
                     "3) reinstalación limpia solo si sigue sin ir bien.")
        return (f"Hay margen real de mejora (aproximadamente +{exp:.0f}% en fluidez percibida). La "
                "instalación es antigua y está cargada, pero prueba primero los cambios reversibles: "
                "una reinstalación cuesta varias horas y conviene reservarla para cuando lo barato "
                "ya se ha agotado.", extra)

    extra.append("Aplica el plan de acción de arriba hacia abajo y vuelve a ejecutar este script "
                 "para verificar cada cambio.")
    return (f"Hay margen de mejora sin tocar hardware: se estima en torno a un +{headroom:.0f}% en "
            f"puntuación sintética y +{exp:.0f}% en fluidez percibida. La mayoría son cambios de "
            "esfuerzo bajo y reversibles.", extra)


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


# ==============================================================================
# 10. EXPORTACIÓN
# ==============================================================================
def export_json(path: Path, si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                projection: dict[str, Any]) -> None:
    payload = {
        "meta": {
            "tool": APP_NAME, "version": APP_VERSION, "author": AUTHOR, "website": WEBSITE_URL,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "system": asdict(si),
        "reference_baseline": REFERENCE,
        "benchmark": {k: asdict(v) for k, v in (bench.results.items() if bench else [])},
        "scores": {
            "components": bench.component_scores() if bench else {},
            "overall": bench.overall() if bench else None,
        },
        "components": [asdict(c) for c in build_component_cards(si, bench, auditor)],
        "findings": [asdict(f) for f in auditor.findings],
        "projection": projection,
        "top_processes": getattr(auditor, "top_processes", []),
        "startup_items": getattr(auditor, "startup_items", []),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Informe de rendimiento · {hostname}</title>
<style>
:root{{--bg:#0d1117;--card:#161b22;--line:#262d38;--txt:#e6edf3;--dim:#8b949e;
--acc:#58a6ff;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--brand:#bc8cff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1000px;margin:0 auto;padding:32px 20px 64px}}
header{{border-bottom:1px solid var(--line);padding-bottom:24px;margin-bottom:32px}}
h1{{margin:0 0 4px;font-size:26px;letter-spacing:-.4px}}
.brand{{color:var(--brand);font-weight:600}}
.brand a{{color:var(--brand);text-decoration:none}}
.meta{{color:var(--dim);font-size:13px}}
h2{{font-size:13px;text-transform:uppercase;letter-spacing:1.4px;color:var(--acc);
margin:40px 0 14px;font-weight:700}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;margin-bottom:14px}}
.hero{{display:flex;gap:16px;flex-wrap:wrap}}
.hero .box{{flex:1;min-width:190px;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:20px;text-align:center}}
.hero .n{{font-size:38px;font-weight:800;letter-spacing:-1.5px;line-height:1.1}}
.hero .l{{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-top:6px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{text-align:left;color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;
letter-spacing:1px;padding:8px 10px;border-bottom:1px solid var(--line)}}
td{{padding:9px 10px;border-bottom:1px solid var(--line)}}
tr:last-child td{{border-bottom:none}}
.track{{height:7px;background:#21262d;border-radius:4px;overflow:hidden;min-width:110px}}
.fill{{height:100%;border-radius:4px}}
.badge{{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;
text-transform:uppercase;letter-spacing:.6px}}
.b-critical{{background:rgba(248,81,73,.16);color:var(--bad)}}
.b-high{{background:rgba(248,81,73,.12);color:var(--bad)}}
.b-medium{{background:rgba(210,153,34,.16);color:var(--warn)}}
.b-low{{background:rgba(88,166,255,.14);color:var(--acc)}}
.b-info{{background:rgba(139,148,158,.16);color:var(--dim)}}
.gain{{color:var(--ok);font-weight:700}}
.finding h3{{margin:0 0 8px;font-size:16px}}
.finding .tags{{color:var(--dim);font-size:12px;margin-bottom:10px}}
.finding p{{margin:0 0 12px;color:#c9d1d9}}
.finding ul{{margin:0;padding-left:20px;color:#c9d1d9;font-size:14px}}
.finding li{{margin-bottom:5px}}
code{{background:#21262d;padding:2px 6px;border-radius:4px;font-size:13px;
font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}
.verdict{{border-left:3px solid var(--acc);background:rgba(88,166,255,.06)}}
footer{{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);
color:var(--dim);font-size:12px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}}
.kvs{{display:grid;grid-template-columns:180px 1fr;gap:6px 16px;font-size:14px}}
.kvs .k{{color:var(--dim)}}
.chead{{display:flex;justify-content:space-between;align-items:baseline;gap:14px;
flex-wrap:wrap;border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:14px}}
.chead h3{{margin:0;font-size:17px}}
.chead .note{{font-size:15px;font-weight:700;white-space:nowrap}}
.sub{{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:1px;
margin:18px 0 8px;font-weight:700}}
.imp{{list-style:none;margin:0;padding:0;font-size:14px}}
.imp li{{padding:7px 0;border-bottom:1px solid var(--line)}}
.imp li:last-child{{border-bottom:none}}
.imp .tags{{color:var(--dim);font-size:12px}}
</style></head><body><div class="wrap">
<header>
<h1>Informe de rendimiento y optimización</h1>
<div class="meta">{hostname} · {os_name} · generado el {date}</div>
<div class="brand" style="margin-top:10px">{author} — <a href="{url}">{website}</a></div>
</header>
{body}
<footer><span>{app} v{version} · escala de referencia: 100 pts = gama media reciente</span>
<span class="brand"><a href="{url}">{website}</a></span></footer>
</div></body></html>"""


def _html_bar(pct: float) -> str:
    pct = max(0, min(100, pct))
    color = "var(--ok)" if pct >= 85 else "var(--warn)" if pct >= 55 else "var(--bad)"
    return (f'<div class="track"><div class="fill" style="width:{pct:.0f}%;'
            f'background:{color}"></div></div>')


def _html_component_cards(cards: list[ComponentCard]) -> str:
    parts = ["<h2>Ficha por componente</h2>"]
    for card in cards:
        if card.score is not None:
            color = ("var(--ok)" if card.score >= 95 else
                     "var(--warn)" if card.score >= 60 else "var(--bad)")
            note = (f'<span class="note" style="color:{color}">{card.score:.0f} pts '
                    f'· nota {card.letter}</span>')
        else:
            note = f'<span class="note" style="color:var(--dim)">{_no_score_text(card)}</span>'

        specs = "".join(f'<div class="k">{k}</div><div>{v}</div>' for k, v in card.specs)
        block = [f'<div class="card"><div class="chead"><h3>{card.label}</h3>{note}</div>']
        if specs:
            block.append(f'<div class="kvs">{specs}</div>')

        if card.tests:
            trs = ""
            for r in card.tests:
                measure = f"{r.raw:,.0f}" if r.unit == "IOPS" else f"{r.raw:,.2f}"
                letter, _ = grade(r.score)
                trs += (f"<tr><td>{r.name}</td><td>{measure} {r.unit}</td>"
                        f"<td><b>{r.score:.0f}</b></td><td>{letter}</td>"
                        f"<td>{_html_bar(r.score)}</td></tr>")
            block.append('<div class="sub">Pruebas medidas</div><table>' + trs + "</table>")

        if card.findings:
            head = "Mejoras aplicables"
            if card.gain > 0.005:
                head += f' — <span class="gain">+{card.gain * 100:.0f}% combinado</span>'
                if card.projected_score is not None:
                    head += (f' <span style="color:var(--dim)">'
                             f'({card.score:.0f} → {card.projected_score:.0f} pts)</span>')
            items = ""
            for f in card.findings:
                gain = (f'<span class="gain">+{f.gain * 100:.0f}%</span>' if f.gain
                        else '<span style="color:var(--dim)">sin ganancia directa</span>')
                items += (f'<li>{gain} &nbsp; {f.title}<div class="tags">'
                          f'<span class="badge b-{f.severity}">{f.severity}</span> &nbsp; '
                          f'{f.category} · esfuerzo {f.effort} · riesgo {f.risk} · '
                          f'{f.gain_note}</div></li>')
            block.append(f'<div class="sub">{head}</div><ul class="imp">{items}</ul>')
        else:
            block.append('<div class="sub">Mejoras aplicables</div>'
                         '<p style="margin:0;color:var(--ok)">Sin mejoras pendientes.</p>')

        block.append("</div>")
        parts.append("".join(block))
    return "".join(parts)


def export_html(path: Path, si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                projection: dict[str, Any]) -> None:
    parts: list[str] = []

    # Hero
    if bench and bench.results:
        overall = bench.overall()
        letter, _ = grade(overall)
        proj = projection.get("projected_overall", overall)
        exp_pct = projection.get("experiential_pct", 0.0)
        color = "var(--ok)" if overall >= 95 else "var(--warn)" if overall >= 60 else "var(--bad)"
        parts.append(f'''<div class="hero">
<div class="box"><div class="n" style="color:{color}">{overall:.0f}</div>
<div class="l">Puntuación actual · nota {letter}</div></div>
<div class="box"><div class="n" style="color:var(--ok)">{proj:.0f}</div>
<div class="l">Tras optimizar</div></div>
<div class="box"><div class="n" style="color:var(--brand)">+{exp_pct:.0f}%</div>
<div class="l">Fluidez percibida estimada</div></div>
<div class="box"><div class="n">{len(auditor.findings)}</div>
<div class="l">Hallazgos en {auditor.checks_run} pruebas</div></div>
</div>''')

    # Inventario
    rows = [
        ("Sistema operativo", f"{si.os_name} · {si.os_build}"),
        ("Instalación", f"{si.os_install_date or 'n/d'}"
                        + (f" ({(si.os_age_days or 0) / 365.25:.1f} años)" if si.os_age_days else "")),
        ("CPU", f"{si.cpu_name} — {si.cpu_cores}C/{si.cpu_threads}T"),
        ("Memoria", f"{si.ram_total / 1024**3:.1f} GB"
                    + (f" @ {si.ram_speed_mhz} MT/s" if si.ram_speed_mhz else "")
                    + (f" · {si.ram_channels} módulo(s)" if si.ram_channels else "")),
        ("Disco de sistema", f"{si.system_drive} · {si.system_drive_media}"),
    ]
    for g in si.gpus:
        rows.append(("GPU", f"{g['name']} · driver {g.get('driver')} "
                            f"({g.get('driver_date') or 'fecha n/d'})"))
    kvs = "".join(f'<div class="k">{k}</div><div>{v}</div>' for k, v in rows)
    parts.append(f'<h2>Inventario</h2><div class="card"><div class="kvs">{kvs}</div></div>')

    # Benchmark
    if bench and bench.results:
        trs = ""
        for r in bench.results.values():
            letter, _ = grade(r.score)
            measure = f"{r.raw:,.0f}" if r.unit == "IOPS" else f"{r.raw:,.2f}"
            trs += (f"<tr><td>{r.name}</td><td>{measure} {r.unit}</td>"
                    f"<td><b>{r.score:.0f}</b></td><td>{letter}</td>"
                    f"<td>{_html_bar(r.score)}</td></tr>")
        parts.append("<h2>Benchmark</h2><div class=\"card\"><table>"
                     "<tr><th>Prueba</th><th>Medida</th><th>Puntos</th><th>Nota</th>"
                     "<th>Relativo a la referencia</th></tr>" + trs + "</table></div>")

    # Ficha por componente
    parts.append(_html_component_cards(build_component_cards(si, bench, auditor)))

    # Proyección
    if projection.get("current_components"):
        labels = {"cpu_single": "CPU monohilo", "cpu_multi": "CPU multihilo",
                  "memory": "Memoria", "disk": "Almacenamiento"}
        trs = ""
        for k, cur in projection["current_components"].items():
            gain = projection["component_gain"].get(k, 0.0)
            proj = projection["projected_components"][k]
            trs += (f"<tr><td>{labels.get(k, k)}</td><td>{cur:.0f} pts</td>"
                    f"<td>{proj:.0f} pts</td>"
                    f"<td class=\"gain\">{'+' + format(gain * 100, '.0f') + '%' if gain else '—'}</td>"
                    f"<td>{_html_bar(proj)}</td></tr>")
        sysgain = projection.get("system_gain", 0.0)
        if sysgain:
            trs += (f"<tr><td>Arranque / fluidez</td><td colspan=2>sin métrica sintética</td>"
                    f"<td class=\"gain\">+{sysgain * 100:.0f}%</td><td>{_html_bar(sysgain * 100)}</td></tr>")
        parts.append("<h2>Proyección de mejora</h2><div class=\"card\"><table>"
                     "<tr><th>Componente</th><th>Ahora</th><th>Optimizado</th>"
                     "<th>Ganancia</th><th></th></tr>" + trs + "</table></div>")

    # Plan
    actionable = sorted([f for f in auditor.findings if f.gain > 0], key=priority_rank)
    if actionable:
        trs = ""
        for i, f in enumerate(actionable, 1):
            trs += (f"<tr><td>{i}</td><td>{f.title}</td>"
                    f"<td class=\"gain\">+{f.gain * 100:.0f}%</td>"
                    f"<td>{f.effort}</td><td>{f.risk}</td>"
                    f"<td><span class=\"badge b-{f.severity}\">{f.severity}</span></td></tr>")
        parts.append("<h2>Plan de acción priorizado</h2><div class=\"card\"><table>"
                     "<tr><th>#</th><th>Acción</th><th>Ganancia est.</th><th>Esfuerzo</th>"
                     "<th>Riesgo</th><th>Severidad</th></tr>" + trs + "</table></div>")

    # Hallazgos
    findings = sorted(auditor.findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.gain))
    if findings:
        parts.append("<h2>Hallazgos en detalle</h2>")
        for f in findings:
            steps = "".join(f"<li>{s}</li>" for s in f.steps)
            gain = (f'<span class="gain">Mejora estimada +{f.gain * 100:.0f}%</span> '
                    f'<span style="color:var(--dim)">({f.gain_note})</span>') if f.gain else ""
            parts.append(f'''<div class="card finding">
<h3>{f.title}</h3>
<div class="tags"><span class="badge b-{f.severity}">{f.severity}</span>
&nbsp; {f.category} · esfuerzo {f.effort} · riesgo {f.risk} &nbsp; {gain}</div>
<p>{f.detail}</p>
{'<ul>' + steps + '</ul>' if steps else ''}</div>''')

    # Veredicto
    verdict, extra = build_verdict(si, bench, auditor, projection)
    extras = "".join(f"<li>{e}</li>" for e in extra)
    parts.append(f'<h2>Veredicto</h2><div class="card verdict"><p>{verdict}</p>'
                 f'{"<ul>" + extras + "</ul>" if extras else ""}</div>')

    html = HTML_TEMPLATE.format(
        hostname=si.hostname, os_name=si.os_name, date=f"{datetime.now():%d/%m/%Y %H:%M}",
        author=AUTHOR, website=WEBSITE, url=WEBSITE_URL, app=APP_NAME, version=APP_VERSION,
        body="\n".join(parts),
    )
    path.write_text(html, encoding="utf-8")


PLAN_HEADER = """# ==============================================================================
#  Plan de optimizacion generado por {app} v{version}
#  {author} - {url}
#  Generado: {date}
# ------------------------------------------------------------------------------
#  LEE ESTO ANTES DE EJECUTAR
#  1. Este script NO se ejecuta solo. Cada bloque pide confirmacion.
#     Revisalo linea por linea y responde 's' solo a lo que entiendas.
#  2. El bloque 0 crea un punto de restauracion y exporta el registro. Hazlo.
#  3. Aplica los cambios por bloques y vuelve a medir entre bloques: si aplicas
#     diez cosas de golpe no sabras cual funciono.
#  4. Ejecutar como Administrador:
#       powershell -ExecutionPolicy Bypass -File plan_optimizacion.ps1
# ==============================================================================

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    Write-Host "Ejecuta este script como Administrador." -ForegroundColor Red
    exit 1
}}

function Confirmar($texto) {{
    $r = Read-Host "$texto  [s/N]"
    return ($r -eq 's' -or $r -eq 'S')
}}

# --- BLOQUE 0: RED DE SEGURIDAD -----------------------------------------------
if (Confirmar "Crear un punto de restauracion del sistema antes de empezar?") {{
    try {{
        Enable-ComputerRestore -Drive "$env:SystemDrive\\"
        Checkpoint-Computer -Description "Antes de PCBench" -RestorePointType MODIFY_SETTINGS
        Write-Host "Punto de restauracion creado." -ForegroundColor Green
    }} catch {{
        Write-Host "No se pudo crear el punto de restauracion: $_" -ForegroundColor Red
    }}
}}

if (Confirmar "Exportar copia de seguridad del registro (HKLM y HKCU) al escritorio?") {{
    $dest = Join-Path ([Environment]::GetFolderPath('Desktop')) "backup_registro_$(Get-Date -f yyyyMMdd_HHmm)"
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    reg export HKLM "$dest\\HKLM.reg" /y | Out-Null
    reg export HKCU "$dest\\HKCU.reg" /y | Out-Null
    Write-Host "Copia guardada en $dest" -ForegroundColor Green
}}
"""

PLAN_FOOTER = f"""
# --- BLOQUE FINAL: MANTENIMIENTO SEGURO (recomendado siempre) ------------------
if (Confirmar "Ejecutar comprobacion de integridad del sistema (DISM + SFC)?") {{
    Write-Host "Esto puede tardar 15-30 minutos..." -ForegroundColor Yellow
    DISM /Online /Cleanup-Image /RestoreHealth
    sfc /scannow
}}

if (Confirmar "Limpiar archivos temporales?") {{
    Remove-Item "$env:TEMP\\*" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "$env:WINDIR\\Temp\\*" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Temporales limpiados." -ForegroundColor Green
}}

if (Confirmar "Ejecutar el asistente de Liberador de espacio en disco?") {{ cleanmgr /d $env:SystemDrive }}

Write-Host ""
Write-Host "Terminado. Reinicia y vuelve a ejecutar el benchmark para comparar." -ForegroundColor Cyan
Write-Host "{AUTHOR} - {WEBSITE_URL}" -ForegroundColor Magenta
"""

# Comandos de remediación automatizables (solo los seguros y reversibles)
PLAN_ACTIONS: dict[str, tuple[str, str]] = {
    "power_plan": (
        "Activar el plan de energia de Alto rendimiento",
        'powercfg /setactive SCHEME_MIN\n'
        '    Write-Host "Plan de energia = Alto rendimiento" -ForegroundColor Green',
    ),
    "trim_off": (
        "Reactivar TRIM en el SSD y forzar un pase de TRIM",
        'fsutil behavior set DisableDeleteNotify 0\n'
        '    Optimize-Volume -DriveLetter $env:SystemDrive.TrimEnd(":") -ReTrim -Verbose',
    ),
    "visual_fx": (
        "Ajustar efectos visuales para maximo rendimiento (manteniendo suavizado de fuentes)",
        'Set-ItemProperty "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects" '
        'VisualFXSetting 2\n'
        '    Set-ItemProperty "HKCU:\\Control Panel\\Desktop" UserPreferencesMask '
        '([byte[]](0x90,0x12,0x03,0x80,0x10,0x00,0x00,0x00))\n'
        '    Set-ItemProperty "HKCU:\\Control Panel\\Desktop" MenuShowDelay "0"\n'
        '    Set-ItemProperty "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize" '
        'EnableTransparency 0\n'
        '    Write-Host "Reinicia el explorador o la sesion para aplicar." -ForegroundColor Yellow',
    ),
    "game_dvr": (
        "Desactivar la grabacion en segundo plano de Game Bar (deja el Modo de juego activo)",
        'Set-ItemProperty "HKCU:\\System\\GameConfigStore" GameDVR_Enabled 0\n'
        '    New-Item "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\GameDVR" -Force | Out-Null\n'
        '    Set-ItemProperty "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\GameDVR" '
        'AppCaptureEnabled 0',
    ),
    "pagefile_off": (
        "Volver a dejar el archivo de paginacion en modo automatico",
        '$cs = Get-CimInstance Win32_ComputerSystem\n'
        '    Set-CimInstance $cs -Property @{AutomaticManagedPagefile=$true}',
    ),
    "fast_startup": (
        "Activar hibernacion e inicio rapido",
        'powercfg /hibernate on\n'
        '    Set-ItemProperty "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Power" '
        'HiberbootEnabled 1',
    ),
    "sysmain": (
        "Desactivar SysMain (Superfetch) - reversible con 'sc config SysMain start=auto'",
        'Stop-Service SysMain -Force -ErrorAction SilentlyContinue\n'
        '    Set-Service SysMain -StartupType Disabled\n'
        '    Write-Host "Si notas peor respuesta, reactivalo: Set-Service SysMain -StartupType Automatic" '
        '-ForegroundColor Yellow',
    ),
    "wsearch_hdd": (
        "Desactivar el indexador de Windows Search (las busquedas seran mas lentas)",
        'Stop-Service WSearch -Force -ErrorAction SilentlyContinue\n'
        '    Set-Service WSearch -StartupType Disabled',
    ),
    "defrag_hdd": (
        "Desfragmentar y optimizar el disco mecanico",
        'Optimize-Volume -DriveLetter $env:SystemDrive.TrimEnd(":") -Defrag -Verbose',
    ),
    "fs_dirty": (
        "Programar CHKDSK en el proximo reinicio",
        'chkdsk $env:SystemDrive /f /r',
    ),
    "startup_bloat": (
        "Listar los programas de inicio para que decidas cuales desactivar",
        'Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location | Format-Table -Auto\n'
        '    Write-Host "Desactivalos en Administrador de tareas > Inicio (ordenado por Impacto)." '
        '-ForegroundColor Yellow',
    ),
}


def _plan_component_summary(cards: list[ComponentCard]) -> str:
    lines = ["\n# ==============================================================================",
             "#  RESUMEN POR COMPONENTE (nota medida y margen de mejora agrupado)",
             "# =============================================================================="]
    for card in cards:
        note = (f"{card.score:.0f} pts (nota {card.letter})" if card.score is not None
                else "sin medir" if card.measurable else "sin nota sintetica")
        margin = f"margen +{card.gain * 100:.0f}%" if card.gain > 0.005 else "sin margen estimado"
        lines.append(f"#  {card.label}: {note} | {margin} | {len(card.findings)} hallazgo(s)")
        for f in card.findings:
            gain = f"+{f.gain * 100:.0f}%" if f.gain > 0 else "n/a"
            auto = "automatizado abajo" if f.id in PLAN_ACTIONS else "manual"
            lines.append(f"#      - [{gain}] {f.title}  ({auto})")
    return "\n".join(lines) + "\n"


def export_plan(path: Path, si: SystemInfo, bench: Benchmark | None, auditor: Auditor) -> int:
    blocks: list[str] = [_plan_component_summary(build_component_cards(si, bench, auditor))]
    n = 0
    for f in sorted([x for x in auditor.findings if x.gain > 0], key=priority_rank):
        action = PLAN_ACTIONS.get(f.id)
        if not action:
            continue
        n += 1
        desc, code = action
        gain = f"ganancia estimada +{f.gain * 100:.0f}% ({f.gain_note})"
        blocks.append(
            f"\n# --- BLOQUE {n}: {desc} ---\n"
            f"# Hallazgo: {f.title}\n"
            f"# Impacto: {gain} | esfuerzo {f.effort} | riesgo {f.risk}\n"
            f'if (Confirmar "{desc}?") {{\n    {code}\n}}\n'
        )

    manual = [f for f in auditor.findings
              if f.gain > 0 and f.id not in PLAN_ACTIONS]
    if manual:
        lines = ["\n# ==============================================================================",
                 "#  ACCIONES QUE NO SE PUEDEN AUTOMATIZAR (requieren decision o hardware)",
                 "# =============================================================================="]
        for f in manual:
            lines.append(f"#  · {f.title}  (+{f.gain * 100:.0f}%)")
            for s in f.steps:
                lines.append(f"#      - {s}")
        blocks.append("\n".join(lines) + "\n")

    content = (PLAN_HEADER.format(date=f"{datetime.now():%d/%m/%Y %H:%M}", app=APP_NAME,
                                  version=APP_VERSION, author=AUTHOR, url=WEBSITE_URL)
               + "".join(blocks) + PLAN_FOOTER)
    path.write_text(content, encoding="utf-8")
    return n


# ==============================================================================
# 11. CLI
# ==============================================================================
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pcbench",
        description=f"{APP_NAME} — benchmark, auditoría y estimación de mejora. "
                    f"{AUTHOR} · {WEBSITE_URL}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Ejemplos:\n"
               "  python pcbench.py\n"
               "  python pcbench.py --quick --no-color\n"
               "  python pcbench.py --disk-size 1024 --html informe.html --export-plan\n",
    )
    p.add_argument("--quick", action="store_true", help="benchmark rápido (menor precisión)")
    p.add_argument("--no-bench", action="store_true", help="solo auditoría, sin benchmark")
    p.add_argument("--no-disk", action="store_true", help="omitir las pruebas de disco")
    p.add_argument("--disk-size", type=int, default=512, metavar="MB",
                   help="tamaño del fichero de prueba de disco (por defecto 512)")
    p.add_argument("--disk-path", metavar="RUTA", default=None,
                   help="carpeta donde hacer el test de disco (por defecto: temporal del sistema)")
    p.add_argument("--json", metavar="FICHERO", nargs="?", const="pcbench_datos.json",
                   help="exportar los datos crudos a JSON")
    p.add_argument("--html", metavar="FICHERO", nargs="?", const="pcbench_informe.html",
                   help="generar informe HTML")
    p.add_argument("--export-plan", metavar="FICHERO", nargs="?", const="plan_optimizacion.ps1",
                   help="generar script PowerShell de optimización (solo Windows)")
    p.add_argument("--no-color", action="store_true", help="desactivar colores ANSI")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    enable_ansi()
    if args.no_color:
        C.disable()

    banner()
    if not is_admin():
        print(f"\n  {C.YELLOW}⚠ Sin permisos de administrador: algunas comprobaciones "
              f"(SMART, TRIM, servicios) pueden no estar disponibles.{C.RESET}")
        print(f"  {C.DIM}Para un análisis completo, abre la terminal como administrador.{C.RESET}")

    section("Recopilando información del sistema")
    spinner_step("Inventario de hardware y SO".ljust(38))
    t0 = time.perf_counter()
    si = collect_system_info()
    spinner_done(f"{time.perf_counter() - t0:.1f} s")

    bench: Benchmark | None = None
    if not args.no_bench:
        bench = Benchmark(quick=args.quick, disk_size_mb=args.disk_size,
                          skip_disk=args.no_disk, target_dir=args.disk_path)
        try:
            bench.run_all()
        except KeyboardInterrupt:
            print(f"\n  {C.YELLOW}Benchmark interrumpido; se continúa con lo medido.{C.RESET}")

    auditor = Auditor(si, bench)
    try:
        auditor.run()
    except KeyboardInterrupt:
        print(f"\n  {C.YELLOW}Auditoría interrumpida.{C.RESET}")

    projection = project_improvement(bench, auditor.findings)
    print_report(si, bench, auditor, projection)

    # --- Exportaciones ---
    outputs: list[str] = []
    if args.json:
        pj = Path(args.json)
        export_json(pj, si, bench, auditor, projection)
        outputs.append(f"JSON  → {pj.resolve()}")
    if args.html:
        ph = Path(args.html)
        export_html(ph, si, bench, auditor, projection)
        outputs.append(f"HTML  → {ph.resolve()}")
    if args.export_plan:
        if not IS_WINDOWS:
            print(f"  {C.YELLOW}--export-plan solo está disponible en Windows.{C.RESET}")
        else:
            pp = Path(args.export_plan)
            count = export_plan(pp, si, bench, auditor)
            outputs.append(f"PLAN  → {pp.resolve()}  ({count} bloques automatizables)")
    if outputs:
        section("Ficheros generados")
        for o in outputs:
            print(f"  {C.GREEN}✓{C.RESET} {o}")
        if args.export_plan and IS_WINDOWS:
            print(f"\n  {C.YELLOW}Revisa el script antes de ejecutarlo. Cada bloque pide "
                  f"confirmación y el primero crea un punto de restauración.{C.RESET}")
        print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Cancelado por el usuario.{C.RESET}\n")
        sys.exit(130)