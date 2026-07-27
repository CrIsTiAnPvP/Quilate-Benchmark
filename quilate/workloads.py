"""Cargas de trabajo del benchmark.

Trabajo FIJO, se mide el tiempo. Deterministas y sin dependencias externas para
que dos ejecuciones en la misma maquina sean comparables.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import time
import zlib


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


def memcpy_bandwidth(size_bytes: int, budget: float = 0.25) -> float:
    """Ancho de banda de copia para un tamano concreto de bloque, en GB/s.

    Repetido con tamanos crecientes dibuja la jerarquia de memoria: mientras el
    par origen+destino cabe en un nivel de cache la cifra se mantiene alta, y cae
    en cada salto. Es la forma mas directa de ver si el equipo esta limitado por
    la RAM (caida temprana y pronunciada) o no.
    """
    src = bytearray(size_bytes)
    dst = bytearray(size_bytes)
    view_src, view_dst = memoryview(src), memoryview(dst)
    view_dst[:] = view_src  # calentar páginas y cargar cache

    copies = 0
    t0 = time.perf_counter()
    while True:
        for _ in range(8):     # amortiza el coste del reloj entre copias
            view_dst[:] = view_src
        copies += 8
        elapsed = time.perf_counter() - t0
        if elapsed >= budget:
            break
    return ((copies * size_bytes) / 1e9) / elapsed if elapsed > 0 else 0.0


def _mp_unit(_seed: int) -> float:
    """Unidad de trabajo para el test multinúcleo (debe ser top-level por pickle)."""
    t0 = time.perf_counter()
    work_sieve(1_200_000)
    work_float(700_000)
    return time.perf_counter() - t0
