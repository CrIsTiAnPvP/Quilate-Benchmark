"""La temperatura que se juzga tiene que ser la del momento que se anuncia.

`check_thermals` titula sus hallazgos «bajo carga», pero en Windows la única
cifra que llegaba a mirar era la de después del benchmark, con el equipo ya
enfriándose: `_sample_sensors` se salta el muestreo entero en Windows —el
`return` está puesto por la frecuencia, que ahí es nominal, y se lleva por
delante la temperatura— así que `thermal_samples` queda siempre vacío y el pico
acababa siendo el reposo. Un equipo que toca 97 °C en carga y baja a 45 °C en
diez segundos no producía ningún hallazgo.

La cifra buena ya estaba medida: `_snapshot("con todos los núcleos cargados")`
la guarda al terminar la carga, y de ahí sale la que ya se pinta en el HTML y se
apunta en el histórico. Aquí se comprueba que el diagnóstico la prefiere al
reposo, y que cuando no hay más remedio que usar el reposo lo dice en el título
en vez de llamarlo «bajo carga».
"""

from __future__ import annotations

import unittest
from unittest import mock

from quilate import audit, benchmark
from quilate.audit import Auditor, SinDato
from quilate.sysinfo import SystemInfo
from tests.support import patched

ANTES = {"moment": "antes de la carga", "cpu_mhz": 4200.0, "cpu_temp": 45.0}
CARGA = {"moment": "con todos los núcleos cargados", "cpu_mhz": 3600.0, "cpu_temp": 97.0}


def _bench(samples=(), snapshots=()) -> benchmark.Benchmark:
    b = benchmark.Benchmark(quick=True, skip_disk=True)
    b.thermal_samples = list(samples)
    b.load_snapshots = [dict(s) for s in snapshots]
    return b


class MuestreoEnWindows(unittest.TestCase):
    """El origen: de dónde viene que `thermal_samples` esté vacío."""

    def test_windows_no_llega_a_muestrear_la_temperatura(self):
        b = _bench()
        with mock.patch.object(benchmark, "IS_WINDOWS", True), \
             mock.patch.object(benchmark, "cpu_temperature", return_value=97.0):
            b._sample_sensors()
        # No es un fallo del sensor: ni se le pregunta. El `return` de la
        # frecuencia corta antes.
        self.assertEqual(b.thermal_samples, [])

    def test_fuera_de_windows_si_se_muestrea(self):
        b = _bench()
        with mock.patch.object(benchmark, "IS_WINDOWS", False), \
             mock.patch.object(benchmark, "cpu_temperature", return_value=97.0):
            b._sample_sensors()
        self.assertEqual(b.thermal_samples, [97.0])


class TemperaturaQueSeJuzga(unittest.TestCase):
    def _auditar(self, bench, reposo, gpu=None):
        # Con `patched` y no con `mock.patch.object`: los sensores se resuelven
        # en el módulo donde vive `check_thermals`, no en la fachada del
        # paquete, y `patched` sabe alcanzar los submódulos. Así el test no
        # tiene que saber en cuál de ellos ha acabado la comprobación.
        aud = Auditor(SystemInfo(), bench)
        with patched(audit, cpu_temperature=lambda: reposo,
                     gpu_temperature=lambda: gpu,
                     temperature_source=lambda: "fuente"):
            return aud, aud.check_thermals()

    def _ids(self, aud):
        return [f.id for f in aud.findings]

    def test_el_pico_bajo_carga_manda_sobre_el_reposo(self):
        # El contrato del informe: 97 °C en carga y 45 °C en reposo son un
        # equipo con throttling, aunque para cuando se mira ya se haya enfriado.
        aud, resumen = self._auditar(_bench(snapshots=[ANTES, CARGA]), reposo=45.0)
        self.assertIn("thermal_critical", self._ids(aud))
        self.assertIn("97", resumen)

    def test_el_titulo_dice_bajo_carga_cuando_la_cifra_es_de_carga(self):
        aud, _ = self._auditar(_bench(snapshots=[ANTES, CARGA]), reposo=45.0)
        titulo = next(f.title for f in aud.findings if f.id == "thermal_critical")
        self.assertIn("bajo carga", titulo)

    def test_las_muestras_de_la_carga_tienen_prioridad_sobre_la_foto(self):
        # En Linux `thermal_samples` sigue existiendo y sigue mandando: es un
        # muestreo continuo, no una foto al final.
        aud, _ = self._auditar(_bench(samples=[80.0, 99.0], snapshots=[ANTES, CARGA]),
                               reposo=45.0)
        titulo = next(f.title for f in aud.findings if f.id == "thermal_critical")
        self.assertIn("99", titulo)

    def test_sin_foto_de_carga_no_se_usa_la_de_antes(self):
        # Si el Pool falla, `run_cpu_multi` sale sin tomar la segunda foto y solo
        # queda la de antes de la carga. Titular eso «bajo carga» sería peor que
        # el propio fallo: es una cifra en reposo con la etiqueta cambiada.
        aud, _ = self._auditar(_bench(snapshots=[dict(ANTES, cpu_temp=97.0)]), reposo=45.0)
        self.assertEqual(self._ids(aud), [])

    def test_una_foto_sin_sensor_no_cuenta_como_medida(self):
        # `_snapshot` guarda `cpu_temp: None` cuando no hay sensor. La foto
        # existe, el dato no.
        aud, _ = self._auditar(_bench(snapshots=[ANTES, dict(CARGA, cpu_temp=None)]),
                               reposo=96.0)
        titulo = next(f.title for f in aud.findings if f.id == "thermal_critical")
        self.assertIn("96", titulo)

    def test_el_reposo_no_se_disfraza_de_carga(self):
        aud, _ = self._auditar(_bench(), reposo=96.0)
        titulo = next(f.title for f in aud.findings if f.id == "thermal_critical")
        self.assertNotIn("bajo carga", titulo)
        self.assertIn("reposo", titulo)

    def test_sin_ninguna_fuente_no_se_inventa_nada(self):
        aud = Auditor(SystemInfo(), _bench())
        with patched(audit, cpu_temperature=lambda: None,
                     gpu_temperature=lambda: None,
                     temperature_report=lambda: []):
            with self.assertRaises(SinDato):
                aud.check_thermals()
        self.assertEqual(aud.findings, [])


if __name__ == "__main__":
    unittest.main()
