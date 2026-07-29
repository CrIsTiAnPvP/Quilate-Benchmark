"""La vara de medir: contra que se comparan las cifras del benchmark.

Aqui no se mide nada. Estan los numeros con los que un resultado se convierte
en una nota, y las reglas que deciden cuando una cifra no significa lo que
parece: si se sirvio desde la cache del sistema, si vario demasiado consigo
misma, o si el equipo estaba ocupado haciendo otra cosa mientras se media.

Se separa del motor porque las dos cosas se revisan por motivos distintos y en
momentos distintos. El motor cambia cuando cambia COMO se mide; esto cambia
cuando cambia CONTRA QUE se compara —cada dos o tres anos, cuando la gama media
deja de ser la de entonces— y es lo que alguien de fuera querria leer para
discutir una nota sin tener que entender el codigo que la produce.

La escala lleva fecha a proposito: una escala sin fecha no envejece, se pudre.
"""

from __future__ import annotations

import sys
from datetime import date


# ------------------------------------------------------------------------------
# ESCALA DE REFERENCIA
# ------------------------------------------------------------------------------
# 100 puntos = el equipo descrito aquí abajo. Lleva fecha a propósito: una escala
# sin fecha no envejece, se pudre. «Gama media reciente» significaba una cosa en
# 2024 y otra en 2028, y la nota iría cambiando de significado sin que nadie
# tocara una línea de código. Con la fecha, Quilate puede avisar de que su propia
# vara de medir se ha quedado vieja en vez de seguir dando notas infladas.
REFERENCE_DATE = "2026-07"
REFERENCE_MACHINE = ("Ryzen 5 5600 / i5-12400 · DDR4-3200 dual channel · "
                    "NVMe PCIe 3.0 · GeForce RTX 3060 / Radeon RX 6600")
# Meses tras los cuales la escala deja de representar a la gama media.
REFERENCE_STALE_MONTHS = 30

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
    "gpu_gflops": 9000.0,     # FP32 encadenado · clase RTX 3060 / RX 6600
    "gpu_vram_gbs": 300.0,    # copia en VRAM (GDDR6 192 bits)
    "gpu_pcie_gbs": 11.0,     # ida y vuelta por PCIe con memoria paginable
}

# De dónde sale cada cifra, para que se pueda discutir y revisar.
REFERENCE_ORIGIN = {
    "gpu_gflops": "medido en una RTX 3060 (9.500-9.900 GFLOPS); se redondea a la baja",
    "gpu_vram_gbs": "medido en una RTX 3060 (318-325 GB/s), 86% de los 360 de catálogo",
    "gpu_pcie_gbs": "medido en PCIe 4.0 x16 con memoria paginable (11,5-12,2 GB/s)",
}


def reference_age_months(hoy: date | None = None) -> int:
    """Meses desde que se fijó la escala."""
    hoy = hoy or date.today()
    año, mes = (int(x) for x in REFERENCE_DATE.split("-"))
    return (hoy.year - año) * 12 + (hoy.month - mes)


def reference_is_stale(hoy: date | None = None) -> bool:
    return reference_age_months(hoy) >= REFERENCE_STALE_MONTHS

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

# Pesos para la nota global. `overall()` renormaliza con los componentes que haya,
# así que un equipo sin GPU medible no sale penalizado: se reparte su peso entre
# el resto. Lo que no puede pasar es que la pieza más cara del PC valga cero
# porque nadie la probó.
WEIGHTS = {"cpu_single": 0.20, "cpu_multi": 0.19, "memory": 0.12, "disk": 0.26, "gpu": 0.23}
