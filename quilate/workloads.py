"""Cargas de trabajo del benchmark.

Trabajo FIJO, se mide el tiempo. Deterministas y sin dependencias externas para
que dos ejecuciones en la misma maquina sean comparables.
"""

from __future__ import annotations

import ctypes
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


# La cache mapea las direcciones por sus bits 6-11: dos bloques separados por un
# multiplo de 4096 caen en el mismo conjunto y se desalojan mutuamente.
_PERIODO_CONJUNTO = 4096
# Media vuelta: el punto mas lejos posible del solapamiento, por los dos lados.
_DESFASE_OBJETIVO = _PERIODO_CONJUNTO // 2
# Desplazamiento de reserva cuando no se puede consultar la direccion real.
_ANTIALIAS = _PERIODO_CONJUNTO + 64


def _direccion(vista: memoryview) -> int:
    return ctypes.addressof(ctypes.c_char.from_buffer(vista))


def _unaliased_pair(size_bytes: int) -> tuple[memoryview, memoryview]:
    """Origen y destino que no compiten por los mismos conjuntos de cache.

    Dos bytearray consecutivos quedan separados por el tamaño mas los 16 bytes
    de cabecera del objeto, o sea 16 bytes justos modulo 4096. Con esa
    separacion cada linea del destino desaloja a la del origen y la copia se
    pasa la vida fallando: 16 KiB median 4,3 GB/s en vez de 65, y un nivel de
    cache llegaba a aparecer mas lento que la RAM.

    Desplazar el destino una cantidad fija no basta: el desfase que se consigue
    depende de donde el asignador coloque cada reserva, y se han medido
    separaciones de 32 bytes —media linea de cache— con el desplazamiento
    puesto. Aqui se leen las direcciones reales y se elige el hueco que deja
    los dos bloques a media vuelta del periodo, que es el punto mas lejano del
    solapamiento se coloquen donde se coloquen.
    """
    src = bytearray(size_bytes + _PERIODO_CONJUNTO)
    dst = bytearray(size_bytes + 2 * _PERIODO_CONJUNTO)
    vista_src = memoryview(src)[:size_bytes]
    hueco = _ANTIALIAS
    try:
        diferencia = _direccion(memoryview(dst)) - _direccion(memoryview(src))
        hueco = (_DESFASE_OBJETIVO - diferencia) % _PERIODO_CONJUNTO
    except (TypeError, ValueError):    # plataforma sin acceso a la direccion
        pass
    return vista_src, memoryview(dst)[hueco:hueco + size_bytes]


def work_memcpy(mb: int, passes: int) -> float:
    """Ancho de banda de memoria (copias grandes). Devuelve GB/s."""
    size = mb * 1024 * 1024
    unit = 8 * 1024 * 1024
    src = bytearray(os.urandom(min(size, unit))) * max(1, size // unit)
    # Mismo motivo que en memcpy_bandwidth: el destino se coloca a media vuelta
    # del periodo de conjuntos para no desalojar al origen linea a linea.
    dst = bytearray(len(src) + 2 * _PERIODO_CONJUNTO)
    view_src = memoryview(src)
    hueco = _ANTIALIAS
    try:
        hueco = (_DESFASE_OBJETIVO
                 - (_direccion(memoryview(dst)) - _direccion(view_src))) % _PERIODO_CONJUNTO
    except (TypeError, ValueError):
        pass
    view_dst = memoryview(dst)[hueco:hueco + len(src)]
    view_dst[:] = view_src  # calentar páginas
    t0 = time.perf_counter()
    for _ in range(passes):
        view_dst[:] = view_src
    elapsed = time.perf_counter() - t0
    return ((len(src) * passes) / 1e9) / elapsed if elapsed > 0 else 0.0


def memcpy_bandwidth(size_bytes: int, budget: float = 0.25) -> float:
    """Ancho de banda de copia para un tamaño concreto de bloque, en GB/s.

    Repetido con tamaños crecientes dibuja la jerarquia de memoria: mientras el
    par origen+destino cabe en un nivel de cache la cifra se mantiene alta, y cae
    en cada salto. Es la forma mas directa de ver si el equipo esta limitado por
    la RAM (caida temprana y pronunciada) o no.
    """
    view_src, view_dst = _unaliased_pair(size_bytes)
    # Varias pasadas de calentamiento: la primera paga el fallo de pagina de la
    # reserva recien hecha, y con una sola el primer tamano medido salia hasta a
    # la mitad de velocidad que en las repeticiones siguientes.
    for _ in range(3):
        view_dst[:] = view_src

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


def _mp_noop(_seed: int) -> int:
    """Tarea vacía. Sirve para que los procesos del Pool arranquen e importen el
    módulo antes de poner el cronómetro en marcha."""
    return 0


def _mp_unit(_seed: int) -> float:
    """Unidad de trabajo para el test multinúcleo (debe ser top-level por pickle)."""
    t0 = time.perf_counter()
    work_sieve(1_200_000)
    work_float(700_000)
    return time.perf_counter() - t0
