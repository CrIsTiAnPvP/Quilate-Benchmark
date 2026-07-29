"""Las consultas del inventario, fusionadas sin perder la honestidad.

Cada `ps_json` levantaba un powershell.exe entero, y arrancar PowerShell 5.1
cuesta entre 200 y 500 ms antes de ejecutar nada. Diez de ellas eran el mayor
coste fijo de la herramienta, y hoy son once: la del blob SMART entró aquí en
vez de abrir su propio proceso.

Lo que no se podía perder al meterlas en un solo proceso es la granularidad de
`PSResult.ok`: hoy el informe distingue «esta consulta falló» de «se ejecutó y
no devolvió nada», y de ahí sale la sección «Sin comprobar». Si un
`Get-PhysicalDisk` denegado tumbara también la lectura de la BIOS, la fusión
habría cambiado un informe honesto por uno más rápido.
"""

from __future__ import annotations

import json
import unittest

from quilate import sysinfo
from quilate.sysinfo import (SystemInfo, _CONSULTAS_INVENTARIO, _bloque,
                             _inventario_windows)
from tests.support import patched


def respuesta(**bloques) -> dict:
    """Lo que devuelve el PowerShell fusionado, en su forma cruda."""
    salida = {}
    for clave in _CONSULTAS_INVENTARIO:
        salida[clave] = bloques.get(clave, {"ok": True, "filas": []})
    return salida


class VolcarCadaPieza(unittest.TestCase):
    """Las seis piezas del inventario de Windows, una a una.

    `_collect_windows_info` eran cien líneas seguidas que nadie ejecutaba en la
    suite: no hay forma de llamarlas sin WMI de verdad. Repartidas en seis
    funciones que reciben ya sus filas, sí se pueden probar — y de paso queda
    cubierto lo que cada bloque hace de no evidente, que es bastante: la
    velocidad real de la RAM frente a la nominal, el entero de 32 bits que
    satura la VRAM en 4 GB, y el adaptador que está pero no pinta nada.
    """

    def setUp(self):
        self.si = SystemInfo()

    # --- sistema operativo ---
    def test_el_sistema_operativo_y_su_antiguedad(self):
        sysinfo._volcar_os(self.si, [{"Caption": "Microsoft Windows 11 Pro",
                                      "Version": "10.0.26100", "BuildNumber": "26100",
                                      "InstallDate": "20240115000000.000000+060"}])
        self.assertEqual(self.si.os_name, "Microsoft Windows 11 Pro")
        self.assertIn("build 26100", self.si.os_build)
        self.assertEqual(self.si.os_install_date, "2024-01-15")
        self.assertGreater(self.si.os_age_days, 0)

    def test_sin_filas_no_se_pisa_lo_que_ya_habia(self):
        self.si.os_name = "lo de antes"
        sysinfo._volcar_os(self.si, [])
        self.assertEqual(self.si.os_name, "lo de antes")

    def test_una_fecha_de_instalacion_ilegible_no_revienta(self):
        sysinfo._volcar_os(self.si, [{"Caption": "Windows", "InstallDate": "vaya"}])
        self.assertIsNone(self.si.os_install_date)

    # --- CPU ---
    def test_la_cpu_llega_sin_espacios_de_sobra(self):
        sysinfo._volcar_cpu(self.si, [{"Name": "  AMD Ryzen 9 5900X  ",
                                       "MaxClockSpeed": 3700}])
        self.assertEqual(self.si.cpu_name, "AMD Ryzen 9 5900X")
        self.assertEqual(self.si.cpu_max_mhz, 3700.0)

    # --- memoria ---
    def test_la_ram_distingue_la_velocidad_real_de_la_nominal(self):
        # Es la diferencia entre XMP activado y no: informar de la nominal sería
        # dar por bueno un rendimiento que el equipo no tiene.
        sysinfo._volcar_memoria(self.si, [
            {"DeviceLocator": "DIMM0", "Capacity": 8 * 1024**3,
             "ConfiguredClockSpeed": 2133, "Speed": 3600},
            {"DeviceLocator": "DIMM1", "Capacity": 8 * 1024**3,
             "ConfiguredClockSpeed": 2133, "Speed": 3600}])
        self.assertEqual(self.si.ram_speed_mhz, 2133)
        self.assertEqual(self.si.ram_speed_rated_mhz, 3600)
        self.assertEqual(self.si.ram_channels, 2)

    def test_una_ranura_vacia_no_cuenta_como_canal(self):
        sysinfo._volcar_memoria(self.si, [
            {"DeviceLocator": "DIMM0", "Capacity": 8 * 1024**3, "Speed": 3200},
            {"DeviceLocator": "DIMM1", "Capacity": 0, "Speed": 0}])
        self.assertEqual(self.si.ram_channels, 1)

    # --- GPU ---
    def _volcar_gpus(self, gpus, registro=None, telemetria=()):
        with patched(sysinfo,
                     _vram_from_registry=lambda: registro or {},
                     gpu_telemetry=lambda *a, **k: list(telemetria)):
            sysinfo._volcar_gpus(self.si, gpus)
        return self.si.gpus

    def test_la_vram_del_registro_gana_al_entero_de_32_bits(self):
        # `AdapterRAM` se satura en 4 GB, así que una tarjeta de 12 aparece
        # siempre como 4. El tamaño real está en el registro.
        gpus = self._volcar_gpus(
            [{"Name": "NVIDIA GeForce RTX 3060", "AdapterRAM": 4 * 1024**3,
              "CurrentHorizontalResolution": 2560}],
            registro={"nvidia geforce rtx 3060": 12 * 1024**3})
        self.assertEqual(gpus[0]["vram"], 12 * 1024**3)
        self.assertIn("registro", gpus[0]["vram_source"])

    def test_nvidia_smi_gana_al_registro(self):
        gpus = self._volcar_gpus(
            [{"Name": "NVIDIA GeForce RTX 3060", "AdapterRAM": 4 * 1024**3}],
            registro={"nvidia geforce rtx 3060": 12 * 1024**3},
            telemetria=[{"name": "NVIDIA GeForce RTX 3060", "vram": 12884901888,
                         "temperature": 44}])
        self.assertEqual(gpus[0]["vram_source"], "nvidia-smi")
        self.assertEqual(gpus[0]["temperature"], 44)

    def test_la_telemetria_casa_aunque_el_nombre_no_sea_identico(self):
        # LibreHardwareMonitor y Win32_VideoController no siempre escriben igual
        # el nombre de la tarjeta.
        gpus = self._volcar_gpus(
            [{"Name": "NVIDIA GeForce RTX 3060", "AdapterRAM": 0}],
            telemetria=[{"name": "GeForce RTX 3060", "temperature": 51}])
        self.assertEqual(gpus[0]["temperature"], 51)

    def test_un_adaptador_sin_resolucion_esta_pero_no_pinta(self):
        # Típico de la iGPU cuando el monitor va por la tarjeta dedicada.
        gpus = self._volcar_gpus([{"Name": "Intel UHD Graphics 770", "AdapterRAM": 0}])
        self.assertFalse(gpus[0]["active"])
        self.assertTrue(gpus[0]["integrated"])
        self.assertIsNone(gpus[0]["resolution"])

    # --- BIOS y chasis ---
    def test_la_fecha_de_la_bios(self):
        sysinfo._volcar_bios(self.si, [{"ReleaseDate": "20220310000000.000000+000"}])
        self.assertEqual(self.si.bios_date, "2022-03-10")

    def test_el_chasis_dice_si_es_portatil(self):
        sysinfo._volcar_chasis(self.si, [{"ChassisTypes": [10]}])
        self.assertTrue(self.si.is_laptop)

    def test_un_chasis_que_llega_como_entero_suelto(self):
        # `ConvertTo-Json` colapsa las listas de un elemento en el elemento.
        sysinfo._volcar_chasis(self.si, [{"ChassisTypes": 9}])
        self.assertTrue(self.si.is_laptop)

    def test_una_torre_no_es_un_portatil(self):
        sysinfo._volcar_chasis(self.si, [{"ChassisTypes": [3]}])
        self.assertFalse(self.si.is_laptop)


class UnBloque(unittest.TestCase):
    """`_bloque` traduce cada trozo del JSON a lo que devolvía `ps_json`."""

    def test_filas_normales(self):
        res = _bloque({"ok": True, "filas": [{"a": 1}, {"b": 2}]})
        self.assertTrue(res.ok)
        self.assertIsNone(res.error)
        self.assertEqual(list(res), [{"a": 1}, {"b": 2}])

    def test_una_sola_fila_llega_como_diccionario(self):
        # PowerShell 5.1 deshace las listas de un elemento al serializar, así
        # que un equipo con un solo módulo de RAM llegaba como objeto suelto.
        res = _bloque({"ok": True, "filas": {"Capacity": 8}})
        self.assertEqual(list(res), [{"Capacity": 8}])

    def test_ejecutada_y_sin_resultados(self):
        # `ok` sin filas: la consulta se hizo y no había nada. No es un fallo.
        res = _bloque({"ok": True, "filas": []})
        self.assertTrue(res.ok)
        self.assertEqual(list(res), [])

    def test_la_consulta_fallo(self):
        res = _bloque({"ok": False, "error": "Acceso denegado"})
        self.assertFalse(res.ok)
        self.assertEqual(res.error, "Acceso denegado")

    def test_un_error_larguisimo_se_recorta(self):
        res = _bloque({"ok": False, "error": "x" * 500})
        self.assertLessEqual(len(res.error), 120)

    def test_un_bloque_que_no_llego(self):
        # Una versión futura de PowerShell que devuelva otra cosa no puede
        # colarse como «se ejecutó y no había nada».
        for crudo in (None, [], "texto", 42):
            with self.subTest(crudo=crudo):
                res = _bloque(crudo)
                self.assertFalse(res.ok)
                self.assertTrue(res.error)

    def test_las_filas_que_no_son_diccionarios_se_descartan(self):
        res = _bloque({"ok": True, "filas": [{"a": 1}, "basura", None]})
        self.assertEqual(list(res), [{"a": 1}])


class GranularidadPorConsulta(unittest.TestCase):
    """Un bloque que falla no puede llevarse por delante a los otros nueve."""

    def _inventario(self, crudo, error=None):
        original = sysinfo._ps_raw
        sysinfo._ps_raw = lambda *a, **k: (crudo, error)
        try:
            return _inventario_windows()
        finally:
            sysinfo._ps_raw = original

    def test_estan_las_nueve_claves(self):
        inventario = self._inventario(respuesta())
        self.assertEqual(set(inventario), set(_CONSULTAS_INVENTARIO))
        self.assertEqual(len(inventario), 9)

    def test_aqui_no_queda_ninguna_que_pida_permisos(self):
        # Las dos que los necesitan se fueron al lote elevado. Volver a meter
        # una aquí no daría error: devolvería «Acceso denegado» en silencio.
        for consulta in _CONSULTAS_INVENTARIO.values():
            with self.subTest(consulta=consulta[:40]):
                self.assertNotIn("StorageReliabilityCounter", consulta)
                self.assertNotIn("root\\wmi", consulta)

    def test_el_que_falla_no_arrastra_a_los_demas(self):
        # El caso real: `Get-Partition` en un equipo con un volumen que el
        # servicio de disco virtual no sabe describir.
        inventario = self._inventario(respuesta(
            partitions={"ok": False, "error": "El servicio no responde"},
            bios={"ok": True, "filas": [{"ReleaseDate": "/Date(0)/"}]}))
        self.assertFalse(inventario["partitions"].ok)
        self.assertEqual(inventario["partitions"].error, "El servicio no responde")
        self.assertTrue(inventario["bios"].ok)
        self.assertEqual(len(inventario["bios"]), 1)

    def test_si_falla_el_proceso_entero_lo_dicen_todas(self):
        # Es lo que habría pasado lanzándolas una a una: ninguna se ejecutó.
        inventario = self._inventario(None, "powershell no respondió")
        for clave, res in inventario.items():
            with self.subTest(consulta=clave):
                self.assertFalse(res.ok)
                self.assertIn("powershell", res.error)

    def test_una_respuesta_que_no_es_un_diccionario(self):
        inventario = self._inventario("vete a saber")
        self.assertTrue(all(not r.ok for r in inventario.values()))

    def test_una_clave_que_falta_en_la_respuesta(self):
        # No es lo mismo que «se ejecutó y no devolvió nada».
        parcial = respuesta()
        del parcial["chassis"]
        inventario = self._inventario(parcial)
        self.assertFalse(inventario["chassis"].ok)
        self.assertTrue(inventario["os"].ok)


class ElScriptGenerado(unittest.TestCase):
    def test_cada_consulta_va_en_su_propio_try(self):
        # La propiedad que hace posible lo de arriba, comprobada en el script y
        # no solo en la respuesta simulada.
        capturado = {}
        original = sysinfo._ps_raw

        def espia(comando, timeout=30):
            capturado["comando"] = comando
            return None, "no importa"

        sysinfo._ps_raw = espia
        try:
            _inventario_windows()
        finally:
            sysinfo._ps_raw = original

        comando = capturado["comando"]
        self.assertIn("try {", comando)
        self.assertIn("catch {", comando)
        for clave in _CONSULTAS_INVENTARIO:
            with self.subTest(consulta=clave):
                self.assertIn(f"$r['{clave}'] = Leer {{", comando)

    def test_no_queda_ninguna_consulta_suelta(self):
        # Si alguien añade una consulta a sysinfo con `ps_json`, vuelve a haber
        # un powershell.exe de más y este test lo dice.
        import inspect
        fuente = inspect.getsource(sysinfo)
        codigo = "\n".join(l for l in fuente.splitlines()
                           if not l.lstrip().startswith("#"))
        self.assertNotIn("ps_json(", codigo,
                         "hay una consulta fuera del inventario fusionado")


if __name__ == "__main__":
    unittest.main()
