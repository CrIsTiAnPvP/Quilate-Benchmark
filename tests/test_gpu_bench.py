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
import unittest

from quilate.benchmark import REFERENCE, WEIGHTS, Benchmark
from quilate.gpu_bench import (GPUNoDisponible, _FLOPS_POR_ITERACION, elegir_dispositivo,
                               medir_gpu)


@contextlib.contextmanager
def sin_ruido():
    """El benchmark escribe con caracteres que la consola del runner no siempre
    sabe codificar; aquí solo interesan los resultados, no la presentación."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def disponible() -> tuple[bool, str]:
    try:
        with sin_ruido():
            medir_gpu(rapido=True)
        return True, ""
    except GPUNoDisponible as exc:
        return False, str(exc)
    except Exception as exc:            # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


HAY_GPU, MOTIVO = disponible()


class EleccionDeDispositivo(unittest.TestCase):
    """En un equipo con dedicada e integrada aparecen las dos. Medir la que no
    trabaja sería repetir el error que ya se arregló con el driver."""

    def test_gana_la_de_mas_capacidad_de_calculo(self):
        integrada = {"name": "iGPU", "compute_units": 7, "clock_mhz": 1900, "vram": 2 << 30}
        dedicada = {"name": "dGPU", "compute_units": 28, "clock_mhz": 1777, "vram": 12 << 30}
        for orden in ([integrada, dedicada], [dedicada, integrada]):
            with self.subTest(orden=[d["name"] for d in orden]):
                self.assertEqual(elegir_dispositivo(orden)["name"], "dGPU")

    def test_a_igual_calculo_desempata_la_memoria(self):
        a = {"name": "A", "compute_units": 10, "clock_mhz": 1000, "vram": 2 << 30}
        b = {"name": "B", "compute_units": 10, "clock_mhz": 1000, "vram": 8 << 30}
        self.assertEqual(elegir_dispositivo([a, b])["name"], "B")

    def test_una_sola(self):
        sola = {"name": "Única", "compute_units": 4, "clock_mhz": 800, "vram": 1 << 30}
        self.assertEqual(elegir_dispositivo([sola])["name"], "Única")


class Escala(unittest.TestCase):
    def test_la_gpu_pesa_en_la_nota_global(self):
        self.assertIn("gpu", WEIGHTS)
        self.assertGreater(WEIGHTS["gpu"], 0.1)

    def test_sin_gpu_el_resto_se_reparte_su_peso(self):
        # Un equipo sin GPU medible no puede salir penalizado por no tenerla.
        b = Benchmark(quick=True, skip_disk=True, skip_gpu=True)
        b._register("cpu_single", "CPU", "pts", 100.0, 100.0)
        self.assertAlmostEqual(b.overall(), 100.0)

    def test_las_referencias_de_gpu_existen(self):
        for clave in ("gpu_gflops", "gpu_vram_gbs", "gpu_pcie_gbs"):
            self.assertGreater(REFERENCE[clave], 0)

    def test_dos_fma_son_cuatro_operaciones(self):
        # Si alguien toca el kernel y cambia el número de FMA por vuelta, esta
        # constante hay que cambiarla con él o los GFLOPS mienten.
        self.assertEqual(_FLOPS_POR_ITERACION, 4)


class SinGPU(unittest.TestCase):
    def test_el_benchmark_no_revienta_y_lo_explica(self):
        b = Benchmark(quick=True, skip_disk=True)
        with sin_ruido():
            b.run_gpu()
        if HAY_GPU:
            self.assertIn("gpu_compute", b.results)
            self.assertEqual(b.gpu_unavailable, "")
        else:
            self.assertNotIn("gpu_compute", b.results)
            self.assertTrue(b.gpu_unavailable, "sin GPU hay que decir por qué")

    def test_se_puede_omitir_a_proposito(self):
        b = Benchmark(quick=True, skip_disk=True, skip_gpu=True)
        with sin_ruido():
            b.run_gpu()
        self.assertEqual(b.results, {})
        # Omitirla a propósito no es lo mismo que no poder medirla.
        self.assertEqual(b.gpu_unavailable, "")


@unittest.skipUnless(HAY_GPU, f"sin GPU con OpenCL en este equipo: {MOTIVO}")
class MedidaReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.datos = medir_gpu(rapido=True)

    def test_las_tres_medidas_son_creibles(self):
        self.assertGreater(self.datos["gflops"], 1.0)
        self.assertGreater(self.datos["vram_gbs"], 1.0)
        self.assertGreater(self.datos["pcie_gbs"], 0.1)

    def test_la_vram_va_por_delante_del_pcie(self):
        # La memoria de la tarjeta tiene que ser más rápida que el bus que la
        # conecta al sistema. Si sale al revés, se está midiendo otra cosa.
        self.assertGreater(self.datos["vram_gbs"], self.datos["pcie_gbs"])

    def test_el_dispositivo_viene_identificado(self):
        dev = self.datos["device"]
        self.assertTrue(dev["name"])
        self.assertGreater(dev["compute_units"], 0)

    def test_hay_varias_muestras_para_calcular_el_margen(self):
        for clave in ("gflops_samples", "vram_samples", "pcie_samples"):
            self.assertGreaterEqual(len(self.datos[clave]), 2, clave)

    def test_el_numero_de_vueltas_se_calibra(self):
        # Con una cifra fija, una tarjeta rápida despacha el kernel en 50 ms y
        # la dispersión se dispara; una integrada tardaría una eternidad.
        self.assertGreater(self.datos["compute_iters"], 64)

    def test_el_margen_del_computo_es_razonable(self):
        b = Benchmark(quick=True, skip_disk=True)
        with sin_ruido():
            b.run_gpu()
        margen = b.dispersion.get("gpu_compute")
        self.assertIsNotNone(margen)
        self.assertLess(margen["spread_pct"], 25.0,
                        "la medida de cómputo no es repetible")


if __name__ == "__main__":
    unittest.main()
