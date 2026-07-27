"""Cada cifra del benchmark viene con cuánto varió consigo misma.

Una medida sola nunca delata que está mal. El test de disco llegó a informar de
205.000 IOPS con tres cifras significativas mientras medía la caché del sistema
operativo, y nada en el informe permitía sospecharlo. Aquí se comprueba que el
margen se calcula, que distingue estable de inestable, y que la carga ajena
durante la sesión queda registrada.
"""

from __future__ import annotations

import unittest

from quilate.benchmark import BUSY_CPU_PCT, UNSTABLE_SPREAD_PCT, Benchmark


class CalculoDelMargen(unittest.TestCase):
    def setUp(self):
        self.b = Benchmark(quick=True, skip_disk=True)

    def test_una_sola_muestra_no_tiene_margen(self):
        # Con un dato no hay dispersión que medir, y fingir un ±0% sería peor
        # que no decir nada: parecería una medida especialmente fiable.
        self.b._spread("x", "X", [1.0])
        self.assertEqual(self.b.dispersion, {})

    def test_el_margen_es_el_recorrido_sobre_la_mediana(self):
        self.b._spread("x", "X", [1.0, 1.1, 1.2])
        d = self.b.dispersion["x"]
        self.assertAlmostEqual(d["median"], 1.1)
        self.assertAlmostEqual(d["spread_pct"], (1.2 - 1.0) / 1.1 * 100, places=1)
        self.assertEqual(d["runs"], 3)

    def test_medidas_iguales_dan_margen_cero(self):
        self.b._spread("x", "X", [2.0, 2.0, 2.0])
        self.assertEqual(self.b.dispersion["x"]["spread_pct"], 0.0)
        self.assertTrue(self.b.dispersion["x"]["stable"])

    def test_los_ceros_no_cuentan_como_muestra(self):
        # Un tramo de duración cero es un fallo de medida, no un resultado
        # instantáneo, y arrastraría la mediana a la mitad.
        self.b._spread("x", "X", [0.0, 2.0, 2.0])
        self.assertEqual(self.b.dispersion["x"]["runs"], 2)

    def test_el_umbral_separa_estable_de_inestable(self):
        self.b._spread("justo", "Justo", [100.0, 100.0 + UNSTABLE_SPREAD_PCT * 0.9])
        self.b._spread("pasado", "Pasado", [100.0, 200.0])
        self.assertTrue(self.b.dispersion["justo"]["stable"])
        self.assertFalse(self.b.dispersion["pasado"]["stable"])
        self.assertEqual([d["label"] for d in self.b.unstable()], ["Pasado"])


class CasoRealDelDisco(unittest.TestCase):
    """Tramos reales de una escritura de 512 MB en un SSD de este proyecto."""

    TRAMOS = [2366.0, 1971.0, 2197.0, 2043.0, 2031.0, 2305.0]     # sesión estable
    TRAMOS_MALOS = [487.0, 521.0, 1155.0, 2289.0, 1636.0, 649.0]  # misma máquina

    def test_la_sesion_estable_no_se_marca(self):
        b = Benchmark(quick=True, skip_disk=True)
        b._spread("disk_write", "Escritura secuencial", self.TRAMOS)
        self.assertTrue(b.dispersion["disk_write"]["stable"],
                        f"margen {b.dispersion['disk_write']['spread_pct']}%")

    def test_la_sesion_contaminada_si_se_marca(self):
        b = Benchmark(quick=True, skip_disk=True)
        b._spread("disk_write", "Escritura secuencial", self.TRAMOS_MALOS)
        d = b.dispersion["disk_write"]
        self.assertFalse(d["stable"])
        self.assertGreater(d["spread_pct"], 100)

    def test_la_media_de_los_malos_parece_perfectamente_creible(self):
        # El motivo de todo esto: la cifra agregada de la sesión mala no tiene
        # nada de sospechoso mirándola sola.
        media = sum(self.TRAMOS_MALOS) / len(self.TRAMOS_MALOS)
        self.assertTrue(500 < media < 1500)


class CargaAjena(unittest.TestCase):
    def test_sin_medidas_no_se_afirma_que_estaba_libre(self):
        b = Benchmark(quick=True, skip_disk=True)
        self.assertEqual(b.busy_during_run(), 0.0)
        self.assertEqual(b.ambient_load, {})

    def test_se_queda_con_el_peor_momento(self):
        b = Benchmark(quick=True, skip_disk=True)
        b.ambient_load = {"antes": {"cpu_pct": 4.0, "top": []},
                          "después": {"cpu_pct": 31.0, "top": [("algo.exe", 25.0)]}}
        self.assertEqual(b.busy_during_run(), 31.0)
        self.assertGreaterEqual(b.busy_during_run(), BUSY_CPU_PCT)

    def test_medicion_real(self):
        # Cuesta 0,4 s y comprueba que la lectura del sistema es plausible:
        # entre 0 y 100, y con el proceso inactivo fuera de la lista.
        b = Benchmark(quick=True, skip_disk=True)
        b._measure_ambient_load("prueba")
        datos = b.ambient_load["prueba"]
        self.assertGreaterEqual(datos["cpu_pct"], 0.0)
        self.assertLessEqual(datos["cpu_pct"], 100.0)
        nombres = [n.lower() for n, _ in datos["top"]]
        self.assertNotIn("system idle process", nombres)


if __name__ == "__main__":
    unittest.main()
