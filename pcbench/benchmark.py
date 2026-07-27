"""Motor de benchmark: ejecuta las cargas, puntua y agrega la nota global."""

from __future__ import annotations

import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Callable

import psutil

from .console import C, section, spinner_done, spinner_step
from .const import IS_WINDOWS
from .platform_utils import ps_json
from .workloads import (_mp_unit, build_corpus, work_compress, work_float,
                        work_hash, work_memcpy, work_sieve)


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
