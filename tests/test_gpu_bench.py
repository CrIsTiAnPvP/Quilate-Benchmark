"""Medida de la GPU por OpenCL.

Quilate auditaba el driver de la gráfica y nunca la ponía a trabajar: en un
equipo con tarjeta dedicada, la pieza más cara no aparecía en la puntuación.

La biblioteca la instala el propio driver, así que en una máquina de
integración continua sin GPU no hay nada que medir. Eso no es un fallo del
test: es el caso que hay que cubrir bien, porque «no hay GPU» y «no se ha
mirado» tienen que seguir siendo cosas distintas.
"""

from __future__ import annotations

import contextlib
import io

import pytest

from quilate.benchmark import REFERENCE, WEIGHTS, Benchmark
from quilate.gpu_bench import (GPUNoDisponible, _FLOPS_POR_ITERACION, elegir_dispositivo,
                               medir_gpu)


@contextlib.contextmanager
def sin_ruido():
    """El benchmark escribe con caracteres que la consola del runner no siempre
    sabe codificar; aquí solo interesan los resultados, no la presentación."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def disponible() -> tuple:
    try:
        with sin_ruido():
            medir_gpu(rapido=True)
        return True, ""
    except GPUNoDisponible as exc:
        return False, str(exc)
    except Exception as exc:            # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


HAY_GPU, MOTIVO = disponible()

# El motivo se calcula una vez y se enseña tal cual en el informe de pytest, que
# es la única forma de que «no hay GPU» siga distinguiéndose de «no se ha
# mirado»: un skip mudo diría lo segundo pareciendo lo primero.
necesita_gpu = pytest.mark.skipif(
    not HAY_GPU, reason=f"sin GPU con OpenCL en este equipo: {MOTIVO}")

# Y su complementaria, para la rama contraria: qué hace el benchmark cuando no
# hay nada que medir. Es el caso que corre en CI y el que más fácil se rompe sin
# que nadie se entere, porque en la máquina de quien desarrolla nunca se ejecuta.
sin_gpu = pytest.mark.skipif(
    HAY_GPU, reason="este equipo sí tiene GPU con OpenCL: la rama de «no se "
                    "puede medir» solo se comprueba donde de verdad no la hay")


# --- Elección de dispositivo -------------------------------------------------
# En un equipo con dedicada e integrada aparecen las dos. Medir la que no
# trabaja sería repetir el error que ya se arregló con el driver.

_INTEGRADA = {"name": "iGPU", "compute_units": 7, "clock_mhz": 1900, "vram": 2 << 30}
_DEDICADA = {"name": "dGPU", "compute_units": 28, "clock_mhz": 1777, "vram": 12 << 30}


@pytest.mark.parametrize("orden", [
    [_INTEGRADA, _DEDICADA],
    [_DEDICADA, _INTEGRADA],
], ids=["integrada-primero", "dedicada-primero"])
def test_gana_la_de_mas_capacidad_de_calculo(orden):
    assert elegir_dispositivo(orden)["name"] == "dGPU"


def test_a_igual_calculo_desempata_la_memoria():
    a = {"name": "A", "compute_units": 10, "clock_mhz": 1000, "vram": 2 << 30}
    b = {"name": "B", "compute_units": 10, "clock_mhz": 1000, "vram": 8 << 30}
    assert elegir_dispositivo([a, b])["name"] == "B"


def test_una_sola():
    sola = {"name": "Única", "compute_units": 4, "clock_mhz": 800, "vram": 1 << 30}
    assert elegir_dispositivo([sola])["name"] == "Única"


# --- La GPU dentro de la nota ------------------------------------------------

def test_la_gpu_pesa_en_la_nota_global():
    assert "gpu" in WEIGHTS
    assert WEIGHTS["gpu"] > 0.1


def test_sin_gpu_el_resto_se_reparte_su_peso():
    # Un equipo sin GPU medible no puede salir penalizado por no tenerla.
    b = Benchmark(quick=True, skip_disk=True, skip_gpu=True)
    b._register("cpu_single", "CPU", "pts", 100.0, 100.0)
    assert b.overall() == pytest.approx(100.0)


@pytest.mark.parametrize("clave", ["gpu_gflops", "gpu_vram_gbs", "gpu_pcie_gbs"])
def test_las_referencias_de_gpu_existen(clave):
    assert REFERENCE[clave] > 0


def test_dos_fma_son_cuatro_operaciones():
    # Si alguien toca el kernel y cambia el número de FMA por vuelta, esta
    # constante hay que cambiarla con él o los GFLOPS mienten.
    assert _FLOPS_POR_ITERACION == 4


# --- Qué pasa cuando no se puede medir ---------------------------------------

def _benchmark_con_gpu_intentada():
    b = Benchmark(quick=True, skip_disk=True)
    with sin_ruido():
        b.run_gpu()
    return b


# Antes esto era un solo test con un `if HAY_GPU: ... else: ...` dentro. Pasaba
# en las dos clases de máquina, pero comprobaba cosas distintas en cada una y el
# informe no decía cuál: un nombre, dos criterios y ninguna forma de saber desde
# fuera si la rama que importaba llegó a ejecutarse. Partido en dos, cada
# entorno declara en su informe qué comprobó y qué se saltó.

@necesita_gpu
def test_con_gpu_se_mide_y_no_se_declara_excusa():
    b = _benchmark_con_gpu_intentada()
    assert "gpu_compute" in b.results
    assert b.gpu_unavailable == ""


@sin_gpu
def test_sin_gpu_el_benchmark_no_revienta_y_lo_explica():
    b = _benchmark_con_gpu_intentada()
    assert "gpu_compute" not in b.results
    assert b.gpu_unavailable, "sin GPU hay que decir por qué"


def test_se_puede_omitir_a_proposito():
    b = Benchmark(quick=True, skip_disk=True, skip_gpu=True)
    with sin_ruido():
        b.run_gpu()
    assert b.results == {}
    # Omitirla a propósito no es lo mismo que no poder medirla.
    assert b.gpu_unavailable == ""


# --- Medida real, solo donde hay tarjeta -------------------------------------

@pytest.fixture(scope="module")
def datos():
    """Una sola medida real compartida por todo el módulo.

    Era `setUpClass`: medir la GPU de verdad en cada test multiplicaría por seis
    el tiempo del único job que llega a ejecutarlos.
    """
    return medir_gpu(rapido=True)


@necesita_gpu
def test_las_tres_medidas_son_creibles(datos):
    assert datos["gflops"] > 1.0
    assert datos["vram_gbs"] > 1.0
    assert datos["pcie_gbs"] > 0.1


@necesita_gpu
def test_la_vram_va_por_delante_del_pcie(datos):
    # La memoria de la tarjeta tiene que ser más rápida que el bus que la
    # conecta al sistema. Si sale al revés, se está midiendo otra cosa.
    assert datos["vram_gbs"] > datos["pcie_gbs"]


@necesita_gpu
def test_el_dispositivo_viene_identificado(datos):
    dev = datos["device"]
    assert dev["name"]
    assert dev["compute_units"] > 0


@necesita_gpu
@pytest.mark.parametrize("clave", ["gflops_samples", "vram_samples", "pcie_samples"])
def test_hay_varias_muestras_para_calcular_el_margen(datos, clave):
    assert len(datos[clave]) >= 2, clave


@necesita_gpu
def test_el_numero_de_vueltas_se_calibra(datos):
    # Con una cifra fija, una tarjeta rápida despacha el kernel en 50 ms y
    # la dispersión se dispara; una integrada tardaría una eternidad.
    assert datos["compute_iters"] > 64


@necesita_gpu
def test_el_margen_del_computo_es_razonable():
    b = Benchmark(quick=True, skip_disk=True)
    with sin_ruido():
        b.run_gpu()
    margen = b.dispersion.get("gpu_compute")
    assert margen is not None
    assert margen["spread_pct"] < 25.0, "la medida de cómputo no es repetible"
