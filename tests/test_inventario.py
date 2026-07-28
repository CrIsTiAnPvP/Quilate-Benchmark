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
from quilate.sysinfo import _CONSULTAS_INVENTARIO, _bloque, _inventario_windows


def respuesta(**bloques) -> dict:
    """Lo que devuelve el PowerShell fusionado, en su forma cruda."""
    salida = {}
    for clave in _CONSULTAS_INVENTARIO:
        salida[clave] = bloques.get(clave, {"ok": True, "filas": []})
    return salida


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

    def test_estan_las_once_claves(self):
        inventario = self._inventario(respuesta())
        self.assertEqual(set(inventario), set(_CONSULTAS_INVENTARIO))
        self.assertEqual(len(inventario), 11)

    def test_el_que_falla_no_arrastra_a_los_demas(self):
        # El caso real: `Get-StorageReliabilityCounter` necesita administrador.
        inventario = self._inventario(respuesta(
            reliability={"ok": False, "error": "Acceso denegado"},
            bios={"ok": True, "filas": [{"ReleaseDate": "/Date(0)/"}]}))
        self.assertFalse(inventario["reliability"].ok)
        self.assertEqual(inventario["reliability"].error, "Acceso denegado")
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
