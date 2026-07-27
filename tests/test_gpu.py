"""Elección de la gráfica principal y auditoría de su driver.

Windows lista también la iGPU del procesador aunque el monitor vaya por la
tarjeta dedicada, y no siempre la deja en primer lugar. Auditar el adaptador con
el driver más antiguo —lo que se hacía— acababa colgando el hallazgo de la
integrada, que lleva años sin actualizarse porque no se usa.
"""

import unittest

from quilate.sysinfo import SystemInfo, gpu_label, primary_gpu, _is_integrated
from tests.support import FixtureCase, load


class DeteccionDeIntegradas(unittest.TestCase):
    def test_nombres_genericos_son_integradas(self):
        for nombre in ("AMD Radeon(TM) Graphics", "Intel(R) UHD Graphics 770",
                       "Intel(R) Iris(R) Xe Graphics", "AMD Radeon(TM) Vega 8 Graphics",
                       "Microsoft Basic Display Adapter"):
            with self.subTest(nombre=nombre):
                self.assertTrue(_is_integrated(nombre))

    def test_las_dedicadas_llevan_modelo(self):
        # El criterio es que las dedicadas nombran el modelo y las iGPU se quedan
        # en el genérico del fabricante. Arc y RX Vega son los casos que más se
        # parecen a un nombre de integrada.
        for nombre in ("NVIDIA GeForce RTX 5060 Ti", "AMD Radeon RX 7800 XT",
                       "Intel(R) Arc(TM) A770 Graphics", "AMD Radeon RX Vega 64",
                       "NVIDIA GeForce RTX 3060"):
            with self.subTest(nombre=nombre):
                self.assertFalse(_is_integrated(nombre))


class DosAdaptadores(FixtureCase):
    """Caso reconstruido: RTX 5060 Ti al día + Radeon integrada de 412 días."""

    def setUp(self):
        self.fx = load("gpu_dos_adaptadores_reconstruido")
        self.gpus = self.fx["gpus"]

    def test_elige_la_dedicada_en_cualquier_orden(self):
        esperado = self.fx["esperado"]["principal"]
        for orden in (self.gpus, list(reversed(self.gpus))):
            with self.subTest(orden=[g["name"] for g in orden]):
                self.assertEqual(primary_gpu(orden)["name"], esperado)

    def test_no_culpa_a_la_integrada_del_driver_viejo(self):
        si = SystemInfo()
        si.gpus = self.gpus
        a = self.auditor(si)
        resumen = a.check_gpu_drivers()
        self.assertEqual(resumen, self.fx["esperado"]["resumen"])
        self.assertEqual([f for f in a.findings if f.id == "gpu_driver"], [])

    def test_la_integrada_sale_etiquetada(self):
        etiquetas = [gpu_label(g) for g in self.gpus]
        self.assertEqual(etiquetas[0], "NVIDIA GeForce RTX 5060 Ti")
        self.assertEqual(etiquetas[1],
                         "AMD Radeon(TM) Graphics (integrada, sin pantalla conectada)")


class UnaSolaDedicada(FixtureCase):
    def test_captura_real(self):
        fx = load("gpu_una_dedicada")
        controladores = fx["video_controllers"]
        self.assertEqual(len(controladores), 1)
        gpu = {"name": controladores[0]["Name"],
               "integrated": _is_integrated(controladores[0]["Name"]),
               "active": bool(controladores[0]["CurrentHorizontalResolution"])}
        self.assertEqual(primary_gpu([gpu])["name"], fx["esperado"]["principal"])
        self.assertEqual(gpu["integrated"], fx["esperado"]["integrada"])
        self.assertEqual(gpu_label(gpu), fx["esperado"]["principal"])


class PortatilSoloConIntegrada(FixtureCase):
    """Sin dedicada, la integrada SÍ es la principal y su driver sí se audita."""

    def test_se_audita_la_integrada(self):
        si = SystemInfo()
        si.gpus = [{"name": "AMD Radeon(TM) Graphics", "integrated": True, "active": True,
                    "driver_date": "2025-06-10", "driver_age_days": 412,
                    "vram": 536870912}]
        a = self.auditor(si)
        resumen = a.check_gpu_drivers()
        self.assertIn("412", resumen)
        hallazgo = next(f for f in a.findings if f.id == "gpu_driver")
        self.assertIn("AMD Radeon(TM) Graphics", hallazgo.title)


class CasosLimite(FixtureCase):
    def test_sin_graficas(self):
        self.assertIsNone(primary_gpu([]))
        self.assertEqual(self.auditor().check_gpu_drivers(), "sin datos")

    def test_sin_fecha_de_driver(self):
        si = SystemInfo()
        si.gpus = [{"name": "X", "driver_age_days": None}]
        self.assertEqual(self.auditor(si).check_gpu_drivers(), "sin fecha de driver")

    def test_campos_ausentes_no_revientan(self):
        # Un JSON de una versión anterior no trae `active` ni `integrated`.
        antiguo = [{"name": "NVIDIA GeForce RTX 3060", "vram": 12884901888},
                   {"name": "AMD Radeon(TM) Graphics", "vram": 536870912}]
        self.assertEqual(primary_gpu(antiguo)["name"], "NVIDIA GeForce RTX 3060")


if __name__ == "__main__":
    unittest.main()
