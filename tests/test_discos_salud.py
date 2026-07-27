"""Desgaste y errores de disco, más allá del binario sano/no sano.

`HealthStatus` sigue diciendo «Healthy» en un SSD al 95% de vida consumida, y
también en uno que ya ha perdido datos por errores no corregidos.
"""

import unittest

from quilate import audit
from quilate.sysinfo import SystemInfo
from tests.support import FixtureCase


def disco(nombre="Disco", **kw):
    base = {"number": 0, "name": nombre, "media": "SSD", "bus": "NVMe",
            "size": 1024**4, "health": "Healthy", "rpm": None,
            "wear": None, "temperature": None, "power_on_hours": None,
            "read_errors": None, "write_errors": None}
    base.update(kw)
    return base


class SinContadores(FixtureCase):
    def test_ausencia_no_es_disco_nuevo(self):
        # Sin privilegios los contadores no se leen. Callar es correcto; decir
        # que está sano, no.
        si = SystemInfo()
        si.physical_disks = [disco()]
        a = self.auditor(si)
        resumen = a._check_disk_wear()
        self.assertIn("requiere administrador", resumen)
        self.assertEqual(a.findings, [])


class Desgaste(FixtureCase):
    def _auditar(self, **kw):
        si = SystemInfo()
        si.physical_disks = [disco(**kw)]
        a = self.auditor(si)
        a._check_disk_wear()
        return a

    def test_disco_nuevo_no_genera_hallazgo(self):
        a = self._auditar(wear=0, power_on_hours=120)
        self.assertEqual(a.findings, [])

    def test_severidad_por_tramos(self):
        for desgaste, severidad in ((65, "medium"), (85, "high"), (95, "critical")):
            with self.subTest(wear=desgaste):
                a = self._auditar(wear=desgaste)
                hallazgo = next(f for f in a.findings if f.id == "disk_wear")
                self.assertEqual(hallazgo.severity, severidad)
                self.assertIn(str(desgaste), hallazgo.title)

    def test_no_se_vende_como_optimizacion(self):
        # Cambiar un disco gastado no acelera nada: es vida restante.
        a = self._auditar(wear=95)
        hallazgo = next(f for f in a.findings if f.id == "disk_wear")
        self.assertEqual(hallazgo.gain, 0.0)

    def test_en_un_hdd_el_desgaste_no_significa_nada(self):
        # Es un contador de ciclos de escritura de celdas: en un disco mecánico
        # no aplica, y aun así el sistema devuelve un valor.
        a = self._auditar(media="HDD", wear=95)
        self.assertEqual([f for f in a.findings if f.id == "disk_wear"], [])

    def test_se_reporta_el_peor_de_varios(self):
        si = SystemInfo()
        si.physical_disks = [disco("Uno", number=0, wear=62),
                             disco("Dos", number=1, wear=91)]
        a = self.auditor(si)
        a._check_disk_wear()
        hallazgo = next(f for f in a.findings if f.id == "disk_wear")
        self.assertIn("Dos", hallazgo.title)
        self.assertEqual(hallazgo.severity, "critical")


class Errores(FixtureCase):
    def test_errores_no_corregidos_son_criticos(self):
        si = SystemInfo()
        si.physical_disks = [disco(wear=10, read_errors=3, write_errors=1)]
        a = self.auditor(si)
        a._check_disk_wear()
        hallazgo = next(f for f in a.findings if f.id == "disk_errors")
        self.assertEqual(hallazgo.severity, "critical")
        self.assertIn("4 errores", hallazgo.title)

    def test_cero_errores_no_dispara(self):
        si = SystemInfo()
        si.physical_disks = [disco(wear=10, read_errors=0, write_errors=0)]
        a = self.auditor(si)
        a._check_disk_wear()
        self.assertEqual([f for f in a.findings if f.id == "disk_errors"], [])


class Temperatura(FixtureCase):
    def test_nvme_caliente(self):
        si = SystemInfo()
        si.physical_disks = [disco(temperature=72)]
        a = self.auditor(si)
        a._check_disk_wear()
        self.assertTrue([f for f in a.findings if f.id == "disk_hot"])

    def test_los_usb_no_cuentan(self):
        # Las cajas USB suelen reportar temperaturas sin sentido.
        si = SystemInfo()
        si.physical_disks = [disco(temperature=80, bus="USB")]
        a = self.auditor(si)
        a._check_disk_wear()
        self.assertEqual([f for f in a.findings if f.id == "disk_hot"], [])

    def test_temperatura_normal(self):
        si = SystemInfo()
        si.physical_disks = [disco(temperature=42)]
        a = self.auditor(si)
        a._check_disk_wear()
        self.assertEqual([f for f in a.findings if f.id == "disk_hot"], [])


class Combinado(FixtureCase):
    def test_un_disco_puede_acumular_varios_problemas(self):
        si = SystemInfo()
        si.physical_disks = [disco(wear=93, temperature=71, read_errors=2)]
        a = self.auditor(si)
        resumen = a._check_disk_wear()
        self.assertEqual({f.id for f in a.findings},
                         {"disk_wear", "disk_hot", "disk_errors"})
        self.assertIn("desgaste máx 93%", resumen)
        self.assertIn("errores", resumen)


if __name__ == "__main__":
    unittest.main()
