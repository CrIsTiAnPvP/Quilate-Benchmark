"""Velocidad de RAM: la que corre, no la que el módulo dice soportar.

En SMBIOS, Speed es la velocidad máxima del módulo y ConfiguredClockSpeed la
real. Sin XMP/EXPO activado difieren, y leer la primera es dar por bueno un
rendimiento que el equipo no tiene.
"""

import unittest

from quilate.sysinfo import SystemInfo
from tests.support import FixtureCase, load


def sticks(configurada, nominal, n=2):
    return [{"capacity": 8 * 1024**3, "speed": configurada, "rated_speed": nominal}] * n


class CapturaConXmpActivo(FixtureCase):
    def test_ddr4_3200_bien_configurada(self):
        fx = load("memoria_ddr4_xmp_activo")
        modulos = fx["physical_memory"]
        configurada = max(m["ConfiguredClockSpeed"] for m in modulos)
        nominal = max(m["Speed"] for m in modulos)
        esperado = fx["esperado"]
        self.assertEqual(configurada, esperado["ram_speed_mhz"])
        self.assertEqual(nominal, esperado["ram_speed_rated_mhz"])

        si = SystemInfo()
        si.cpu_name = "AMD Ryzen 5 5600X 6-Core Processor"
        si.ram_speed_mhz, si.ram_speed_rated_mhz = configurada, nominal
        si.ram_sticks = sticks(configurada, nominal)
        si.ram_total = 16 * 1024**3
        a = self.auditor(si)
        a.check_ram_channels()
        self.assertEqual([f for f in a.findings if f.id == "ram_slow"], [])


class PerfilSinActivar(FixtureCase):
    """Casos SINTÉTICOS, no capturados: no se dispone de un equipo sin XMP."""

    def _auditar(self, cpu, configurada, nominal):
        si = SystemInfo()
        si.cpu_name = cpu
        si.ram_speed_mhz, si.ram_speed_rated_mhz = configurada, nominal
        si.ram_sticks = sticks(configurada, nominal)
        si.ram_total = 16 * 1024**3
        a = self.auditor(si)
        resumen = a.check_ram_channels()
        return a, resumen

    def test_ddr5_sin_expo(self):
        # El umbral fijo anterior (2400) nunca lo veía: la base JEDEC de DDR5
        # son 4800, muy por encima de ese corte.
        a, resumen = self._auditar("AMD Ryzen 7 7800X3D 8-Core Processor", 4800, 6000)
        hallazgo = next(f for f in a.findings if f.id == "ram_slow")
        self.assertIn("4800", hallazgo.title)
        self.assertIn("6000", hallazgo.title)
        self.assertIn("4800", resumen)

    def test_ddr4_sin_xmp(self):
        a, _ = self._auditar("AMD Ryzen 5 3600 6-Core Processor", 2133, 3200)
        self.assertTrue([f for f in a.findings if f.id == "ram_slow"])

    def test_intel_tambien_se_detecta_con_menos_ganancia(self):
        # Antes solo se miraba en AMD. En Intel el impacto es menor, no nulo.
        amd, _ = self._auditar("AMD Ryzen 7 7800X3D 8-Core Processor", 4800, 6000)
        intel, _ = self._auditar("Intel(R) Core(TM) i7-13700K", 4800, 6000)
        ganancia_amd = next(f.gain for f in amd.findings if f.id == "ram_slow")
        ganancia_intel = next(f.gain for f in intel.findings if f.id == "ram_slow")
        self.assertGreater(ganancia_amd, ganancia_intel)
        self.assertGreater(ganancia_intel, 0)

    def test_diferencia_minima_no_dispara(self):
        # Los SMBIOS redondean; un 5% de margen evita el falso positivo.
        a, _ = self._auditar("Intel(R) Core(TM) i7-13700K", 3200, 3300)
        self.assertEqual([f for f in a.findings if f.id == "ram_slow"], [])


class CasosLimite(FixtureCase):
    def test_un_solo_modulo_es_single_channel(self):
        si = SystemInfo()
        si.ram_sticks = sticks(3200, 3200, n=1)
        si.ram_total = 8 * 1024**3
        si.ram_speed_mhz = si.ram_speed_rated_mhz = 3200
        a = self.auditor(si)
        self.assertIn("single channel", a.check_ram_channels())

    def test_sin_datos_de_modulos(self):
        self.assertIn("sin datos", self.auditor().check_ram_channels())

    def test_sin_velocidad_nominal(self):
        si = SystemInfo()
        si.ram_sticks = sticks(3200, 0)
        si.ram_speed_mhz, si.ram_speed_rated_mhz = 3200, None
        si.ram_total = 16 * 1024**3
        a = self.auditor(si)
        a.check_ram_channels()
        self.assertEqual([f for f in a.findings if f.id == "ram_slow"], [])


if __name__ == "__main__":
    unittest.main()
