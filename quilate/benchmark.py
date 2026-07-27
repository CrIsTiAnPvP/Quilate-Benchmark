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
from typing import Any, Callable

import psutil

from .console import C, section, spinner_done, spinner_step
from .const import IS_WINDOWS
from .rawio import DiskIO
from .sensors import (cpu_frequency_mhz, cpu_temperature, gpu_telemetry,
                      temperature_source)
from .workloads import (_mp_noop, _mp_unit, build_corpus, memcpy_bandwidth,
                        work_compress, work_float, work_hash, work_memcpy, work_sieve)


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

# Por debajo de esta latencia no hay almacenamiento que valga: eso es memoria.
# Un NVMe ronda los 100 µs, un SATA los 200 y un disco mecánico los miles.
CACHE_LATENCY_US = 20.0

# Dispersión relativa a partir de la cual una cifra deja de ser comparable.
# Una medida sola nunca delata que está mal: el test de disco daba 205.000 IOPS
# con total aplomo mientras medía la caché del sistema operativo. Repartir el
# mismo trabajo en tramos y mirar cuánto varían entre sí es lo que convierte un
# número en un número con margen de error.
UNSTABLE_SPREAD_PCT = 25.0

# Porcentaje de CPU ajena en reposo por encima del cual la sesión no es
# comparable con otra: no se está midiendo el equipo, se está midiendo el
# equipo mientras hace otra cosa.
BUSY_CPU_PCT = 20.0


def cache_served(latency_us: float, direct: bool) -> bool:
    """Si la lectura salió de la caché de páginas en vez del disco.

    `direct` es cierto cuando la E/S ya esquiva la caché (sin buffer en Windows,
    caché descartada en Linux) y entonces no hay nada que sospechar.
    """
    return not direct and latency_us < CACHE_LATENCY_US

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
        # Frecuencia real con todos los núcleos cargados, y de dónde salió. Es la
        # única cifra de frecuencia que significa algo: la «máxima» que publica
        # Win32_Processor es el reloj base, no el boost.
        self.freq_under_load: float | None = None
        self.freq_source: str = ""
        self.disk_on_ram = False
        # La medida de disco solo vale si esquiva la caché de páginas del SO.
        self.disk_unbuffered = False
        self.disk_cache_suspect = False
        self.scaling_efficiency: float | None = None
        # Métricas que no puntúan pero explican el porqué de la puntuación.
        self.metrics: dict[str, dict] = {}
        self.memory_hierarchy: list[dict] = []
        self.load_snapshots: list[dict] = []   # antes / después de la carga pesada
        # Cuánto varió cada medida consigo misma, y qué estaba haciendo el
        # equipo mientras tanto. Sin esto, una cifra contaminada es
        # indistinguible de una buena.
        self.dispersion: dict[str, dict] = {}
        self.ambient_load: dict[str, Any] = {}

    # -- helpers ----------------------------------------------------------------
    def _timed(self, fn: Callable, *args, runs: int | None = None,
               key: str = "", label: str = "") -> float:
        """Ejecuta y devuelve el MEJOR tiempo (menos ruido del scheduler).

        Se queda con el mejor porque es la cifra menos contaminada por el
        planificador, pero anota la dispersión de todas: el mejor tiempo de una
        tanda que varía un 40% no significa lo mismo que el de una estable.
        """
        runs = runs or (1 if self.quick else 3)
        times = []
        for _ in range(runs):
            t0 = time.perf_counter()
            fn(*args)
            times.append(time.perf_counter() - t0)
            self._sample_sensors()
        if key:
            self._spread(key, label or key, times)
        return min(times)

    def _spread(self, key: str, label: str, muestras: list[float]) -> None:
        """Anota cuánto varía una medida entre repeticiones, en % de la mediana."""
        muestras = [m for m in muestras if m > 0]
        if len(muestras) < 2:
            return
        mediana = statistics.median(muestras)
        if mediana <= 0:
            return
        pct = (max(muestras) - min(muestras)) / mediana * 100
        self.dispersion[key] = {
            "label": label,
            "runs": len(muestras),
            "median": round(mediana, 5),
            "spread_pct": round(pct, 1),
            "stable": pct <= UNSTABLE_SPREAD_PCT,
        }

    def unstable(self) -> list[dict]:
        """Medidas cuya dispersión las hace poco comparables."""
        return [d for d in self.dispersion.values() if not d["stable"]]

    def _measure_ambient_load(self, moment: str) -> None:
        """Carga ajena con el benchmark parado.

        Se mide justo antes y justo después, cuando Quilate no está haciendo
        nada: lo que salga es de otros. Es la única forma barata de saber si la
        sesión es comparable con otra, y evita atribuir al hardware una nota
        que en realidad se explica por lo que había abierto.
        """
        # El contador por proceso es diferencial: la primera llamada siempre
        # devuelve 0.0. Hay que cebarlo ANTES de la ventana de medición y leerlo
        # después, o la lista de culpables sale siempre vacía.
        vivos = []
        try:
            for p in psutil.process_iter(["name"]):
                try:
                    p.cpu_percent()
                    vivos.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            pct = psutil.cpu_percent(interval=0.4)
        except Exception:
            return
        ocupados = []
        nucleos = psutil.cpu_count(logical=True) or 1
        for p in vivos:
            try:
                # El PID 0 es el proceso inactivo del sistema: consume por
                # definición todo lo que nadie usa, y encabezaría la lista siempre.
                if p.pid in (0, os.getpid()):
                    continue
                # cpu_percent va sobre un núcleo; se normaliza al total para
                # que sume en la misma escala que la cifra del sistema.
                uso = p.cpu_percent() / nucleos
                if uso >= 1.0:
                    ocupados.append((p.name(), round(uso, 1)))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self.ambient_load[moment] = {
            "cpu_pct": round(pct, 1),
            "top": sorted(ocupados, key=lambda x: -x[1])[:5],
        }

    def busy_during_run(self) -> float:
        """El peor porcentaje de CPU ajena visto con el benchmark parado."""
        return max((v.get("cpu_pct", 0.0) for v in self.ambient_load.values()), default=0.0)

    def _sample_sensors(self) -> None:
        # En Windows psutil.cpu_freq() devuelve la frecuencia NOMINAL leída del
        # registro, no la real: en un 5900X marca 3701 MHz esté como esté el
        # equipo. Sirve en Linux, donde sale de cpufreq. La frecuencia real de
        # Windows se toma en los snapshots, que van por contador de rendimiento
        # y cuestan demasiado para muestrearlos aquí.
        if IS_WINDOWS:
            return
        try:
            freq = psutil.cpu_freq()
            if freq and freq.current:
                self.freq_samples.append(freq.current)
        except Exception:
            pass
        temp = cpu_temperature()
        if temp:
            self.thermal_samples.append(temp)

    def _register(self, key: str, name: str, unit: str, raw: float, score: float,
                  detail: str = "") -> None:
        self.results[key] = BenchResult(name, unit, raw, max(0.0, score), detail)

    def _metric(self, key: str, label: str, value: Any, unit: str = "", note: str = "") -> None:
        """Dato medido que no entra en la nota pero sí en el diagnóstico."""
        self.metrics[key] = {"label": label, "value": value, "unit": unit, "note": note}

    def _snapshot(self, moment: str) -> dict:
        """Foto de frecuencia, temperatura y GPU en un instante de la sesión."""
        mhz, freq_source = cpu_frequency_mhz()
        gpu = gpu_telemetry()
        snap = {
            "moment": moment,
            "cpu_mhz": mhz,
            "cpu_mhz_source": freq_source,
            "cpu_temp": cpu_temperature(),
            "gpu_temp": max((g["temperature"] for g in gpu if g.get("temperature")), default=None),
            "gpu_power_w": max((g["power_w"] for g in gpu if g.get("power_w")), default=None),
        }
        self.load_snapshots.append(snap)
        return snap

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
        t = self._timed(work_sieve, 4_000_000, key="sieve", label="Criba de primos")
        s = REFERENCE["sieve_s"] * PY_ADJUST / t * 100
        sub_scores.append(s)
        spinner_done(f"{t:.3f} s  → {s:.0f} pts")

        spinner_step("CPU 1T · coma flotante".ljust(38))
        t = self._timed(work_float, 2_500_000, key="float", label="Coma flotante")
        s = REFERENCE["float_s"] * PY_ADJUST / t * 100
        sub_scores.append(s)
        spinner_done(f"{t:.3f} s  → {s:.0f} pts")

        mib = 256 if self.quick else 512
        spinner_step(f"CPU 1T · SHA-256 ({mib} MiB)".ljust(38))
        t = self._timed(work_hash, mib, key="hash", label="SHA-256")
        s = REFERENCE["hash_s"] * (mib / 512) / t * 100   # sin PY_ADJUST: corre en C
        sub_scores.append(s)
        spinner_done(f"{t:.3f} s  → {s:.0f} pts  ({mib / t:.0f} MB/s)")

        spinner_step("CPU 1T · preparando corpus".ljust(38))
        build_corpus(12)
        spinner_done("12 MiB")
        spinner_step("CPU 1T · compresión zlib (12 MiB)".ljust(38))
        t = self._timed(work_compress, 12, runs=1 if self.quick else 2,
                        key="compress", label="Compresión zlib")
        s = REFERENCE["compress_s"] / t * 100
        sub_scores.append(s)
        spinner_done(f"{t:.3f} s  → {s:.0f} pts")

        geo = statistics.geometric_mean([max(1.0, x) for x in sub_scores])
        self._register("cpu_single", "CPU monohilo", "pts", geo, geo,
                       f"media geométrica de {len(sub_scores)} subtests")

    def run_cpu_multi(self) -> None:
        threads = psutil.cpu_count(logical=True) or 1
        cores = psutil.cpu_count(logical=False) or threads
        tasks = threads * (2 if self.quick else 4)
        spinner_step(f"CPU {threads}T · carga paralela ({tasks} uds)".ljust(38))

        # Referencia para el escalado: una unidad a solas, sin contención. Medirla
        # con todos los núcleos ocupados —como se hacía— la degrada en la misma
        # proporción que al resultado, y la eficiencia se cancela sola: salía un
        # 64% donde el escalado real era del 27%.
        solo = min(_mp_unit(0) for _ in range(1 if self.quick else 2))

        before = self._snapshot("antes de la carga")
        try:
            with Pool(processes=threads) as pool:
                # Crear el Pool arranca un intérprete por proceso. Dentro del
                # cronómetro, eso mide el arranque de Python y no la CPU: pesa un
                # 7% desde el código fuente y bastante más desde el .exe, donde
                # cada hijo descomprime el intérprete empaquetado.
                pool.map(_mp_noop, range(threads * 4))
                t0 = time.perf_counter()
                per_task = pool.map(_mp_unit, range(tasks))
                wall = time.perf_counter() - t0
        except Exception as exc:  # entornos sin fork/spawn disponible
            spinner_done(f"no disponible ({type(exc).__name__})", ok=False)
            return
        after = self._snapshot("con todos los núcleos cargados")
        self._register_thermal_behaviour(before, after, per_task)
        # El margen se saca por tandas, no tarea a tarea: con 4 tareas por hilo
        # el recorrido entre las 96 individuales recoge el arranque y la cola
        # del pool y sale un ±50% que no significa nada. Promediar cada tanda
        # —tantas tareas como hilos— compara trabajos equivalentes.
        tandas = [statistics.mean(per_task[i:i + threads])
                  for i in range(0, len(per_task), threads)
                  if len(per_task[i:i + threads]) == threads]
        self._spread("cpu_multi", "Tandas de trabajo en paralelo", tandas)
        tps = tasks / wall
        score = tps / REFERENCE["mp_tps"] * 100
        # Escalado real: cuánto trabajo en paralelo por unidad de trabajo en serie.
        # Se compara con los núcleos físicos, no con los hilos: con SMT, 24 hilos
        # sobre 12 núcleos no pueden dar 24× ni en el mejor de los casos, así que
        # dividir entre 24 haría parecer defectuoso a un equipo perfectamente sano.
        speedup = tasks * solo / wall if wall else 0.0
        efficiency = speedup / cores * 100 if cores else 0.0
        detail = (f"{threads} hilos sobre {cores} núcleos · {speedup:.1f}× frente a "
                  f"un solo núcleo · escalado {efficiency:.0f}%")
        self._register("cpu_multi", "CPU multihilo", "uds/s", tps, score, detail)
        self.scaling_efficiency = efficiency
        self._metric("mp_speedup", "Aceleración multinúcleo", round(speedup, 1), "×",
                     f"trabajo en paralelo frente a una unidad a solas ({solo:.3f} s); "
                     f"el techo práctico son los {cores} núcleos físicos")
        spinner_done(f"{tps:.2f} uds/s → {score:.0f} pts  ({speedup:.1f}× · escalado "
                     f"{efficiency:.0f}%)")

    def _register_thermal_behaviour(self, before: dict, after: dict,
                                    per_task: list[float]) -> None:
        """Comportamiento bajo carga sostenida.

        Es la métrica que más explica el rendimiento real de un equipo ya usado:
        dos máquinas idénticas puntúan igual en una ráfaga corta y muy distinto
        cuando una de ellas baja de frecuencia a los treinta segundos. Comparar
        las primeras unidades de trabajo con las últimas lo detecta sin
        necesidad de sensores: si las últimas tardan más, algo está limitando.
        """
        if len(per_task) >= 4:
            quarter = max(1, len(per_task) // 4)
            first = statistics.mean(per_task[:quarter])
            last = statistics.mean(per_task[-quarter:])
            if first > 0:
                retention = min(100.0, first / last * 100) if last else 100.0
                self._metric("sustained", "Rendimiento sostenido", round(retention, 1), "%",
                             "trabajo del último cuarto frente al primero; por debajo de "
                             "~90% indica límite térmico o de potencia")
        if after.get("cpu_mhz"):
            self.freq_under_load = float(after["cpu_mhz"])
            self.freq_source = str(after.get("cpu_mhz_source") or "")
        if before.get("cpu_mhz") and after.get("cpu_mhz"):
            note = f"en reposo marcaba {before['cpu_mhz']:.0f} MHz"
            if "nominal" in after.get("cpu_mhz_source", ""):
                note += " · dato nominal: este sistema no expone la frecuencia real"
            self._metric("freq_under_load", "Frecuencia con todos los núcleos",
                         round(after["cpu_mhz"]), "MHz", note)
        if after.get("cpu_temp"):
            delta = ""
            if before.get("cpu_temp"):
                delta = f"subió {after['cpu_temp'] - before['cpu_temp']:+.0f} °C durante la carga"
            self._metric("cpu_temp_load", "Temperatura de CPU bajo carga",
                         round(after["cpu_temp"], 1), "°C", delta)
        if after.get("gpu_temp"):
            self._metric("gpu_temp", "Temperatura de GPU", round(after["gpu_temp"], 1), "°C",
                         "leída del propio driver (nvidia-smi)")
        if after.get("gpu_power_w"):
            self._metric("gpu_power", "Consumo de GPU", round(after["gpu_power_w"], 1), "W",
                         "en reposo durante el test de CPU")

    def run_memory_hierarchy(self) -> None:
        """Recorre la jerarquía de cachés hasta la RAM."""
        spinner_step("RAM · jerarquía de caché".ljust(38))
        budget = 0.15 if self.quick else 0.3
        levels = [("L1/L2", 16 * 1024), ("L2", 192 * 1024),
                  ("L3", 4 * 1024**2), ("RAM", 64 * 1024**2)]
        for label, size in levels:
            gbs = memcpy_bandwidth(size, budget)
            self.memory_hierarchy.append({"level": label, "size": size, "gbs": round(gbs, 2)})
        best = max(self.memory_hierarchy, key=lambda x: x["gbs"])
        dram = self.memory_hierarchy[-1]
        ratio = best["gbs"] / dram["gbs"] if dram["gbs"] else 0
        spinner_done(" · ".join(f"{lv['level']} {lv['gbs']:.0f}" for lv in self.memory_hierarchy)
                     + " GB/s")
        self._metric("cache_ratio", "Caché frente a RAM", round(ratio, 1), "×",
                     "cuánto más rápida es la caché que la memoria principal")

    def run_memory(self) -> None:
        spinner_step("RAM · ancho de banda".ljust(38))
        mb = 64 if self.quick else 128
        passes = 6 if self.quick else 12
        # Siempre al menos dos pasadas, también en modo rápido: el ancho de
        # banda de memoria es de las medidas que más bailan entre ejecuciones
        # —se han visto 21 y 13 GB/s con medio minuto de diferencia— y sin una
        # segunda pasada no hay forma de saber cuál de las dos creerse.
        medidas = [work_memcpy(mb, passes) for _ in range(2 if self.quick else 3)]
        self._spread("memory", "Ancho de banda de memoria", medidas)
        gbs = max(medidas)
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

        path = Path(self.target_dir) / f".quilate_{os.getpid()}.tmp"
        size = self.disk_size_mb * 1024 * 1024
        block_size = 1024 * 1024
        chunk_size = 4 * 1024 * 1024
        try:
            free = shutil.disk_usage(self.target_dir).free
            if free < size * 2.5:
                spinner_step("Disco · test".ljust(38))
                spinner_done("omitido: espacio libre insuficiente", ok=False)
                return

            # --- Escritura secuencial ---
            spinner_step(f"Disco · escritura secuencial ({self.disk_size_mb} MB)".ljust(38))
            writer = DiskIO(str(path), write=True, block=block_size)
            writer.fill_random()
            # Se cronometra por tramos del mismo trabajo, sin repetirlo: es
            # gratis y es justo donde se ve la caché SLC agotarse a mitad de
            # escritura, que a una sola cifra le pasa desapercibido.
            tramos_w: list[float] = []
            corte = max(block_size, size // 3)
            t0 = time.perf_counter()
            written = 0
            marca, escrito_tramo = t0, 0
            while written < size:
                n = writer.write(block_size)
                written += n
                escrito_tramo += n
                if escrito_tramo >= corte:
                    ahora = time.perf_counter()
                    tramos_w.append((escrito_tramo / 1e6) / (ahora - marca))
                    marca, escrito_tramo = ahora, 0
            # El último tramo se cierra ANTES del sync: el vaciado del búfer es
            # un coste único de la prueba, no de ese tramo, y metérselo dentro
            # lo haría parecer lentísimo e inventaría dispersión que no existe.
            if escrito_tramo:
                tramos_w.append((escrito_tramo / 1e6) / max(1e-9, time.perf_counter() - marca))
            writer.sync()
            wt = time.perf_counter() - t0
            self._spread("disk_write", "Escritura secuencial", tramos_w)
            self.disk_unbuffered = writer.unbuffered
            dropped = writer.drop_cache() if not writer.unbuffered else False
            writer.close()
            wmbs = (written / 1e6) / wt
            wscore = wmbs / REFERENCE["disk_write_mbs"] * 100
            self._register("disk_write", "Disco · escritura", "MB/s", wmbs, wscore)
            spinner_done(f"{wmbs:.0f} MB/s → {wscore:.0f} pts")

            # Si la lectura no esquiva la caché de páginas, lo que se mide es la
            # RAM: el fichero acaba de escribirse y sigue entero en memoria. Esos
            # números no pueden puntuar. El disco pesa un 34% de la nota global y
            # se pegaba al techo en cualquier equipo con RAM de sobra, midiera lo
            # que midiera el disco de verdad.
            direct = self.disk_unbuffered or dropped
            source = ("sin caché del SO" if self.disk_unbuffered
                      else "caché descartada" if dropped else "con caché del SO")
            # Una sonda de unas pocas lecturas aleatorias basta para saberlo, y
            # decide por igual la lectura secuencial y las IOPS: leen el mismo
            # fichero, así que o las dos salen del disco o ninguna. Comparar la
            # lectura contra la escritura no vale: la escritura también puede ir
            # inflada por la caché SLC del SSD y entonces la razón no delata nada.
            cached = False
            if not direct:   # con E/S directa no hay nada que sondear
                cached = cache_served(self._cache_probe(path, size), direct=False)

            # --- Lectura secuencial ---
            spinner_step("Disco · lectura secuencial".ljust(38))
            reader = DiskIO(str(path), write=False, block=chunk_size)
            tramos_r: list[float] = []
            corte_r = max(chunk_size, size // 3)
            t0 = time.perf_counter()
            read_total = 0
            marca, leido_tramo = t0, 0
            while read_total < size:
                got = reader.read(chunk_size)
                if not got:
                    break
                read_total += got
                leido_tramo += got
                if leido_tramo >= corte_r:
                    ahora = time.perf_counter()
                    tramos_r.append((leido_tramo / 1e6) / (ahora - marca))
                    marca, leido_tramo = ahora, 0
            rt = time.perf_counter() - t0
            if leido_tramo:
                tramos_r.append((leido_tramo / 1e6) / max(1e-9, time.perf_counter() - marca))
            reader.close()
            self._spread("disk_read", "Lectura secuencial", tramos_r)
            rmbs = (read_total / 1e6) / rt
            if cached:
                self.disk_cache_suspect = True
                spinner_done(f"{rmbs:,.0f} MB/s: servido desde la caché del SO, no puntúa",
                             ok=False)
            else:
                rscore = rmbs / REFERENCE["disk_read_mbs"] * 100
                self._register("disk_read", "Disco · lectura", "MB/s", rmbs, rscore, source)
                spinner_done(f"{rmbs:.0f} MB/s → {rscore:.0f} pts")

            # --- IOPS aleatorias 4K ---
            spinner_step("Disco · IOPS aleatorias 4K".ljust(38))
            ops = 4000 if self.quick else 12000
            max_off = max(0, size - 4096)
            rnd = random.Random(7)
            offsets = [rnd.randrange(0, max_off, 4096) for _ in range(ops)]
            iop_reader = DiskIO(str(path), write=False, block=4096)
            tramos_i: list[float] = []
            por_tramo = max(1, ops // 3)
            t0 = time.perf_counter()
            marca, hechas = t0, 0
            for off in offsets:
                iop_reader.seek(off)
                iop_reader.read(4096)
                hechas += 1
                if hechas >= por_tramo:
                    ahora = time.perf_counter()
                    tramos_i.append(hechas / (ahora - marca))
                    marca, hechas = ahora, 0
            it = time.perf_counter() - t0
            if hechas:
                tramos_i.append(hechas / max(1e-9, time.perf_counter() - marca))
            iop_reader.close()
            self._spread("disk_iops", "IOPS aleatorias 4K", tramos_i)
            iops = ops / it
            latency_us = it / ops * 1e6
            if cached:
                self.disk_cache_suspect = True
                spinner_done(f"{iops:,.0f} IOPS · {latency_us:.1f} µs: caché del SO, no puntúa",
                             ok=False)
            else:
                iscore = iops / REFERENCE["disk_iops_4k"] * 100
                self._register("disk_iops", "Disco · IOPS 4K", "IOPS", iops, iscore, source)
                spinner_done(f"{iops:,.0f} IOPS → {iscore:.0f} pts")
                # La latencia por operación es lo que se percibe al abrir programas:
                # más representativa que las IOPS agregadas, y comparable entre equipos.
                self._metric("disk_latency", "Latencia media 4K", round(latency_us, 1), "µs",
                             "tiempo de una lectura aleatoria con cola 1: <100 µs NVMe, "
                             f"~200 µs SATA, >5.000 µs disco mecánico · medido {source}")

            if self.disk_cache_suspect:
                self._metric("disk_cached", "Medida de disco incompleta", "caché del SO", "",
                             "este sistema no permite E/S sin buffer y no se ha podido descartar "
                             "la caché de páginas, así que parte del test medía RAM y se ha "
                             "excluido de la nota. Usa --disk-path en otra unidad o un "
                             "--disk-size mayor que la RAM libre")
        except Exception as exc:
            spinner_done(f"error: {exc}", ok=False)
        finally:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _cache_probe(self, path: Path, size: int) -> float:
        """Latencia mediana de unas pocas lecturas aleatorias de 4K, en µs.

        Por debajo de 20 µs no hay almacenamiento que valga: eso es memoria. Un
        NVMe ronda los 100 µs, un SATA los 200 y un disco mecánico los miles.
        """
        rnd = random.Random(11)
        offsets = [rnd.randrange(0, max(4096, size - 4096), 4096) for _ in range(64)]
        probe = DiskIO(str(path), write=False, block=4096)
        times = []
        try:
            for off in offsets:
                t0 = time.perf_counter()
                probe.seek(off)
                probe.read(4096)
                times.append((time.perf_counter() - t0) * 1e6)
        finally:
            probe.close()
        return statistics.median(times) if times else 0.0

    def run_all(self) -> None:
        section("Benchmark en ejecución")
        print(f"  {C.DIM}Cierra el resto de aplicaciones para obtener medidas fiables.{C.RESET}\n")
        self._measure_ambient_load("antes")
        self.run_cpu_single()
        self.run_cpu_multi()
        self.run_memory()
        self.run_memory_hierarchy()
        self.run_disk()
        self._measure_ambient_load("después")
        self._finish()

    def _finish(self) -> None:
        """Cierra la sesión anotando de dónde salió (o no) cada dato sensible."""
        inestables = self.unstable()
        if inestables:
            peor = max(inestables, key=lambda d: d["spread_pct"])
            self._metric("dispersion", "Dispersión máxima entre tramos",
                         peor["spread_pct"], "%",
                         f"en «{peor['label']}»: la misma prueba varió eso consigo misma. "
                         "Las cifras de esta sesión valen como orden de magnitud, no para "
                         "comparar con otra ejecución")
            print(f"\n  {C.YELLOW}⚠ Medidas poco estables: "
                  + ", ".join(f"{d['label']} (±{d['spread_pct']:.0f}%)" for d in inestables)
                  + f".{C.RESET}")
            print(f"    {C.DIM}La misma prueba ha dado resultados distintos entre tramos. "
                  f"Suele ser carga de fondo, límite térmico o caché del disco agotándose.{C.RESET}")

        ocupada = self.busy_during_run()
        if ocupada:
            culpables = []
            for datos in self.ambient_load.values():
                culpables += [n for n, _ in datos.get("top", [])]
            nota = "medido con el benchmark parado, así que ese consumo es de otros procesos"
            if culpables:
                nota += " (" + ", ".join(dict.fromkeys(culpables)) + ")"
            self._metric("ambient_cpu", "CPU ajena en reposo", ocupada, "%", nota)
            if ocupada >= BUSY_CPU_PCT:
                print(f"\n  {C.YELLOW}⚠ El equipo no estaba en reposo: {ocupada:.0f}% de CPU "
                      f"la consumían otros procesos.{C.RESET}")
                print(f"    {C.DIM}La puntuación de esta sesión no es comparable con otra "
                      f"hecha con el equipo libre.{C.RESET}")

        source = temperature_source()
        if source:
            self._metric("temp_source", "Fuente de temperatura de CPU", source)
        elif self.thermal_samples:
            self._metric("temp_source", "Fuente de temperatura de CPU", "sensores del sistema")
        for g in gpu_telemetry():
            if g.get("utilization") is not None:
                self._metric("gpu_idle_load", "GPU en reposo durante el test",
                             round(g["utilization"]), "%",
                             f"{g['name']} · {g['clock_mhz']:.0f} MHz" if g.get("clock_mhz") else "")
            break

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
