"""Lectura de sensores: temperatura de CPU y telemetria de GPU.

La temperatura de CPU en Windows es el dato mas dificil de obtener: leerla exige
hablar con el chip por MSR/SMBus, y eso solo lo puede hacer un driver en modo
kernel. Windows no expone nada equivalente a /sys/class/hwmon. Por eso aqui se
prueban, en orden, todas las fuentes que existen sin instalar un driver propio,
y se recuerda cual funciono para no repetir el coste en cada muestra.

Si ninguna responde, `cpu_temperature()` devuelve None y `temperature_report()`
explica que se intento y que hace falta instalar. Preferimos decirlo claramente
antes que inventar una cifra.
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Any

import psutil

from .const import IS_WINDOWS
from .platform_utils import ps_json, run_cmd

# Nombres de sensor que corresponden al paquete de CPU, de mas a menos fiable.
_CPU_SENSOR_HINTS = ("cpu package", "core (tctl/tdie)", "cpu total", "package id",
                     "tctl", "tdie", "cpu")

_LINUX_KEYS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz", "thinkpad")

# Estado de la cascada: se resuelve una vez y se reutiliza. None = aun sin probar.
_source: str | None = None
_source_resolved = False
_tried: list[tuple[str, str]] = []      # (fuente, resultado)
_gpu_cache: tuple[float, list[dict]] | None = None
_nvsmi_path: str | None | bool = False  # False = sin buscar todavia


# ==============================================================================
# Fuentes de temperatura de CPU
# ==============================================================================
def _from_psutil() -> float | None:
    try:
        temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None
    for key in _LINUX_KEYS:
        if temps.get(key):
            vals = [t.current for t in temps[key] if t.current]
            if vals:
                return max(vals)
    for entries in temps.values():
        vals = [t.current for t in entries if t.current]
        if vals:
            return max(vals)
    return None


def _from_hardware_monitor(namespace: str) -> float | None:
    """LibreHardwareMonitor / OpenHardwareMonitor publican sus sensores por WMI
    mientras estan abiertos. Es la via mas fiable en Windows y la unica que da
    la temperatura real del die en Ryzen."""
    rows = ps_json(f'Get-CimInstance -Namespace "{namespace}" -ClassName Sensor '
                   f'-ErrorAction Stop | Where-Object {{$_.SensorType -eq "Temperature"}} | '
                   f"Select-Object Name,Value,Identifier", timeout=10)
    best: float | None = None
    best_rank = len(_CPU_SENSOR_HINTS)
    for r in rows:
        name = str(r.get("Name") or "").lower()
        ident = str(r.get("Identifier") or "").lower()
        value = r.get("Value")
        if value is None or not ("cpu" in ident or "cpu" in name):
            continue
        try:
            celsius = float(value)
        except (TypeError, ValueError):
            continue
        if not 10 < celsius < 125:
            continue
        rank = next((i for i, h in enumerate(_CPU_SENSOR_HINTS) if h in name),
                    len(_CPU_SENSOR_HINTS) - 1)
        if rank < best_rank or best is None:
            best, best_rank = celsius, rank
    return best


def _from_acpi() -> float | None:
    """Zona termica ACPI. Muchas placas de sobremesa no la implementan (devuelven
    «Incompatible») y donde existe suele medir la placa, no el die."""
    rows = ps_json('Get-CimInstance -Namespace "root/WMI" -ClassName '
                   "MSAcpi_ThermalZoneTemperature -ErrorAction Stop | "
                   "Select-Object CurrentTemperature", timeout=10)
    for r in rows:
        raw = r.get("CurrentTemperature")
        if raw:
            celsius = (int(raw) / 10.0) - 273.15   # decimas de kelvin
            if 10 < celsius < 125:
                return round(celsius, 1)
    return None


def _from_perf_counter() -> float | None:
    """Contador «Thermal Zone Information». El nombre esta localizado, así que se
    prueba en ingles y en español."""
    for counter in ("\\Thermal Zone Information(*)\\Temperature",
                    "\\Información de zona térmica(*)\\Temperatura"):
        data = ps_json(f'(Get-Counter "{counter}" -ErrorAction Stop).CounterSamples | '
                       f"Select-Object CookedValue", timeout=12)
        for r in data:
            raw = r.get("CookedValue")
            try:
                kelvin = float(raw)
            except (TypeError, ValueError):
                continue
            celsius = kelvin - 273.15
            if 10 < celsius < 125:
                return round(celsius, 1)
    return None


def _from_temperature_probe() -> float | None:
    rows = ps_json("Get-CimInstance Win32_TemperatureProbe -ErrorAction Stop | "
                   "Select-Object CurrentReading", timeout=10)
    for r in rows:
        raw = r.get("CurrentReading")
        if raw:
            celsius = (int(raw) / 10.0) - 273.15
            if 10 < celsius < 125:
                return round(celsius, 1)
    return None


# Orden de preferencia: fiabilidad primero, coste después.
_SOURCES: list[tuple[str, str, Any]] = [
    ("psutil", "sensores del kernel (psutil)", _from_psutil),
    ("lhm", "LibreHardwareMonitor (WMI)", lambda: _from_hardware_monitor("root/LibreHardwareMonitor")),
    ("ohm", "OpenHardwareMonitor (WMI)", lambda: _from_hardware_monitor("root/OpenHardwareMonitor")),
    ("acpi", "zona térmica ACPI (MSAcpi)", _from_acpi),
    ("perf", "contador de rendimiento térmico", _from_perf_counter),
    ("probe", "Win32_TemperatureProbe", _from_temperature_probe),
]


def cpu_temperature() -> float | None:
    """Temperatura de CPU en °C, o None si el equipo no la expone.

    La primera llamada prueba todas las fuentes; las siguientes van directas a la
    que funciono. Si ninguna respondio, devuelve None sin volver a intentarlo:
    reintentar cuesta cientos de milisegundos por muestra y falsearía el
    benchmark que la está muestreando."""
    global _source, _source_resolved

    if _source_resolved:
        if _source is None:
            return None
        for key, _label, fn in _SOURCES:
            if key == _source:
                try:
                    return fn()
                except Exception:
                    return None
        return None

    for key, label, fn in _SOURCES:
        if not IS_WINDOWS and key != "psutil":
            continue
        try:
            value = fn()
        except Exception as exc:
            _tried.append((label, f"error: {type(exc).__name__}"))
            continue
        if value is not None:
            _tried.append((label, f"{value:.0f} °C"))
            _source, _source_resolved = key, True
            return value
        _tried.append((label, "sin datos"))

    _source, _source_resolved = None, True
    return None


def temperature_source() -> str | None:
    """Etiqueta legible de la fuente que funciono (tras llamar a cpu_temperature)."""
    for key, label, _fn in _SOURCES:
        if key == _source:
            return label
    return None


def temperature_report() -> list[tuple[str, str]]:
    """Que fuentes se probaron y con que resultado. Para el informe."""
    return list(_tried)


# ==============================================================================
# Frecuencia real de CPU
# ==============================================================================
def cpu_frequency_mhz() -> tuple[float | None, str]:
    """Frecuencia efectiva actual en MHz, y de donde sale.

    En Windows `psutil.cpu_freq().current` devuelve la frecuencia nominal de la
    CPU, no la real: siempre coincide con el maximo y no sirve para detectar ni
    turbo ni throttling. El contador PercentProcessorPerformance si es real —da el
    porcentaje sobre la base—, y por encima del 100% se ve el turbo trabajando.
    """
    base = None
    try:
        freq = psutil.cpu_freq()
        base = (freq.max or freq.current) if freq else None
    except Exception:
        base = None

    if IS_WINDOWS and base:
        rows = ps_json("Get-CimInstance Win32_PerfFormattedData_Counters_ProcessorInformation "
                       "-ErrorAction Stop | Where-Object {$_.Name -eq '_Total'} | "
                       "Select-Object PercentProcessorPerformance", timeout=12)
        for r in rows:
            pct = r.get("PercentProcessorPerformance")
            try:
                pct = float(pct)
            except (TypeError, ValueError):
                continue
            if pct > 0:
                return round(base * pct / 100.0, 1), "contador de rendimiento del procesador"

    if not IS_WINDOWS:
        try:
            freq = psutil.cpu_freq()
            if freq and freq.current:
                return float(freq.current), "psutil"
        except Exception:
            pass
    return (float(base) if base else None), "frecuencia nominal (no es la real)"


# ==============================================================================
# Telemetria de GPU
# ==============================================================================
def _find_nvidia_smi() -> str | None:
    global _nvsmi_path
    if _nvsmi_path is not False:
        return _nvsmi_path  # type: ignore[return-value]
    candidates = [shutil.which("nvidia-smi")]
    if IS_WINDOWS:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        candidates += [
            os.path.join(system_root, "System32", "nvidia-smi.exe"),
            r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
        ]
    _nvsmi_path = next((c for c in candidates if c and os.path.exists(c)), None)
    return _nvsmi_path  # type: ignore[return-value]


_NVSMI_FIELDS = ("name", "memory.total", "memory.used", "temperature.gpu",
                 "utilization.gpu", "clocks.sm", "power.draw")


def gpu_telemetry(max_age: float = 2.0) -> list[dict]:
    """Datos en vivo de las GPU NVIDIA vía nvidia-smi.

    Es la única forma fiable de saber la VRAM real y la temperatura del núcleo:
    Win32_VideoController.AdapterRAM es un entero de 32 bits y se satura en 4 GB,
    así que una 3060 de 12 GB aparece ahí como 4 GB.
    """
    global _gpu_cache
    now = time.monotonic()
    if _gpu_cache and now - _gpu_cache[0] < max_age:
        return _gpu_cache[1]

    exe = _find_nvidia_smi()
    if not exe:
        _gpu_cache = (now, [])
        return []

    out = run_cmd([exe, f"--query-gpu={','.join(_NVSMI_FIELDS)}",
                   "--format=csv,noheader,nounits"], timeout=12)
    gpus: list[dict] = []
    for line in (out or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != len(_NVSMI_FIELDS):
            continue

        def num(text: str) -> float | None:
            try:
                return float(text)
            except ValueError:
                return None

        vram_mb = num(parts[1])
        used_mb = num(parts[2])
        gpus.append({
            "name": parts[0],
            "vram": int(vram_mb * 1024 * 1024) if vram_mb else 0,
            "vram_used": int(used_mb * 1024 * 1024) if used_mb else 0,
            "temperature": num(parts[3]),
            "utilization": num(parts[4]),
            "clock_mhz": num(parts[5]),
            "power_w": num(parts[6]),
            "source": "nvidia-smi",
        })
    _gpu_cache = (now, gpus)
    return gpus


def gpu_temperature() -> float | None:
    """Temperatura de la GPU más caliente, si hay alguna que la exponga."""
    temps = [g["temperature"] for g in gpu_telemetry() if g.get("temperature")]
    return max(temps) if temps else None


# Compatibilidad con el nombre anterior.
def read_cpu_temperature() -> float | None:
    return cpu_temperature()
