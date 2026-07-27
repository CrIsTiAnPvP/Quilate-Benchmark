"""Telemetría de gráficas AMD e Intel vía LibreHardwareMonitor.

nvidia-smi solo cubre NVIDIA: sin esto, quien tenga una Radeon o una Arc se
queda sin temperatura de GPU. Los sensores de LHM van por identificador
(`/amdgpu/0/temperature/0`) y el nombre comercial está en otra clase.

Las filas de estos tests son SINTÉTICAS: no hay ninguna máquina a mano con GPU
AMD o Intel y LibreHardwareMonitor abierto, así que prueban el parseo y no que
el esquema real coincida.
"""

import unittest

from quilate import sensors
from tests.support import patched


def sensor(ident, nombre, tipo, valor):
    return {"Identifier": ident, "Name": nombre, "SensorType": tipo, "Value": valor}


AMD = [
    sensor("/amdgpu/0/temperature/0", "GPU Core", "Temperature", 61.0),
    sensor("/amdgpu/0/load/0", "GPU Core", "Load", 37.0),
    sensor("/amdgpu/0/power/0", "GPU Package", "Power", 112.5),
    sensor("/amdgpu/0/clock/0", "GPU Core", "Clock", 2100.0),
    sensor("/amdgpu/0/smalldata/1", "GPU Memory Used", "SmallData", 2048.0),
    sensor("/amdgpu/0/smalldata/2", "GPU Memory Total", "SmallData", 16384.0),
    # Ruido que no debe colarse: la CPU también publica sensores.
    sensor("/amdcpu/0/temperature/0", "Core (Tctl/Tdie)", "Temperature", 55.0),
]
HARDWARE_AMD = [{"Identifier": "/amdgpu/0", "Name": "AMD Radeon RX 7800 XT"}]


def responder(sensores, hardware):
    def _ps(query, *a, **k):
        if "ClassName Hardware" in query:
            return hardware
        if "ClassName Sensor" in query:
            return sensores
        return []
    return _ps


class BaseSensores(unittest.TestCase):
    def setUp(self):
        # El namespace detectado se cachea a nivel de módulo; sin reiniciarlo,
        # un test contaminaría al siguiente.
        self._previo = sensors._hw_monitor_ns
        sensors._hw_monitor_ns = False
        self.addCleanup(lambda: setattr(sensors, "_hw_monitor_ns", self._previo))


class LecturaDeAmd(BaseSensores):
    def _leer(self, sensores=AMD, hardware=HARDWARE_AMD, exclude=()):
        with patched(sensors, wmi=responder(sensores, hardware)):
            return sensors._gpu_from_hardware_monitor(list(exclude))

    def test_agrupa_los_sensores_de_la_tarjeta(self):
        tarjetas = self._leer()
        self.assertEqual(len(tarjetas), 1)
        gpu = tarjetas[0]
        self.assertEqual(gpu["temperature"], 61.0)
        self.assertEqual(gpu["utilization"], 37.0)
        self.assertEqual(gpu["power_w"], 112.5)
        self.assertEqual(gpu["clock_mhz"], 2100.0)

    def test_usa_el_nombre_comercial_no_el_identificador(self):
        # Es el que hay que casar con Win32_VideoController.
        self.assertEqual(self._leer()[0]["name"], "AMD Radeon RX 7800 XT")

    def test_sin_clase_hardware_cae_al_identificador(self):
        self.assertEqual(self._leer(hardware=[])[0]["name"], "amdgpu/0")

    def test_la_vram_se_convierte_a_bytes(self):
        gpu = self._leer()[0]
        self.assertEqual(gpu["vram"], 16384 * 1024 * 1024)
        self.assertEqual(gpu["vram_used"], 2048 * 1024 * 1024)

    def test_los_sensores_de_cpu_no_se_cuelan(self):
        # /amdcpu/0 no es una gráfica aunque el fabricante coincida.
        nombres = [g["name"] for g in self._leer()]
        self.assertNotIn("amdcpu/0", nombres)

    def test_no_duplica_lo_que_ya_dio_nvidia_smi(self):
        hardware = [{"Identifier": "/nvidiagpu/0", "Name": "NVIDIA GeForce RTX 3060"}]
        sensores = [sensor("/nvidiagpu/0/temperature/0", "GPU Core", "Temperature", 50.0)]
        vacio = self._leer(sensores, hardware, exclude=["nvidia geforce rtx 3060"])
        self.assertEqual(vacio, [])


class CasosLimite(BaseSensores):
    def _leer(self, sensores, hardware=()):
        with patched(sensors, wmi=responder(sensores, list(hardware))):
            return sensors._gpu_from_hardware_monitor([])

    def test_sin_monitor_abierto(self):
        self.assertEqual(self._leer([]), [])

    def test_valores_no_numericos_se_ignoran(self):
        filas = [sensor("/amdgpu/0/temperature/0", "GPU Core", "Temperature", "n/d"),
                 sensor("/amdgpu/0/load/0", "GPU Core", "Load", 20.0)]
        self.assertEqual(self._leer(filas)[0]["utilization"], 20.0)

    def test_una_tarjeta_sin_ningun_dato_util_no_se_incluye(self):
        # Solo un sensor de ventilador: no aporta nada de lo que se informa.
        filas = [sensor("/amdgpu/0/fan/0", "GPU Fan", "Fan", 1200.0)]
        self.assertEqual(self._leer(filas), [])

    def test_identificador_malformado(self):
        self.assertEqual(self._leer([sensor("gpu", "X", "Temperature", 50.0)]), [])

    def test_dos_tarjetas_se_separan(self):
        filas = [sensor("/amdgpu/0/temperature/0", "GPU Core", "Temperature", 60.0),
                 sensor("/amdgpu/1/temperature/0", "GPU Core", "Temperature", 70.0)]
        self.assertEqual(len(self._leer(filas)), 2)


class CosteDeNoTenerlo(BaseSensores):
    """Lo normal es no tener LibreHardwareMonitor abierto, y eso tiene que salir
    gratis: cada consulta fallida es un proceso de PowerShell."""

    def _contar_consultas(self, respuesta):
        llamadas = []

        def _ps(query, *a, **k):
            llamadas.append(query)
            return respuesta
        return llamadas, _ps

    def test_sin_monitor_solo_se_pregunta_una_vez(self):
        llamadas, _ps = self._contar_consultas([])
        with patched(sensors, wmi=_ps):
            self.assertEqual(sensors._gpu_from_hardware_monitor([]), [])
            primera = len(llamadas)
            for _ in range(5):
                sensors._gpu_from_hardware_monitor([])
        self.assertGreater(primera, 0)
        self.assertEqual(len(llamadas), primera, "no debe reintentar tras descartarlo")

    def test_con_monitor_no_se_reprueban_los_dos_namespaces(self):
        llamadas, _ps = self._contar_consultas(AMD)
        with patched(sensors, wmi=_ps):
            sensors._gpu_from_hardware_monitor([])
            llamadas.clear()
            sensors._gpu_from_hardware_monitor([])
        # Solo el namespace que funcionó: sensores + hardware.
        self.assertEqual(len(llamadas), 2)
        self.assertTrue(all("LibreHardwareMonitor" in q for q in llamadas))


if __name__ == "__main__":
    unittest.main()
