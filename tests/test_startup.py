"""Entradas de inicio: solo cuentan las que Windows va a ejecutar de verdad.

El Administrador de tareas no borra el valor de Run al desactivar una app: lo
deja donde está y anota el estado en StartupApproved. Sin mirar ahí, todo lo que
el usuario ha ido desactivando seguía contando.
"""

import unittest

from quilate import audit
from tests.support import FixtureCase, FakeRegistry, load, patched


class BlobDeDecision(FixtureCase):
    """El primer byte del blob lleva el bit 0 a 1 cuando está desactivada."""

    def _estado(self, blob):
        tree = {"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer"
                "\\StartupApproved\\Run": {"App": blob} if blob is not None else {}}
        with patched(audit, FakeRegistry(tree)):
            from tests.support import HKCU
            return self.auditor()._startup_enabled([(HKCU, "Run")], "App")

    def test_activas(self):
        for blob in ("hex:020000000000000000000000",       # activa, sin marca de tiempo
                     "hex:06000000000000000000000"  "0"):  # activa, la variante del sistema
            with self.subTest(blob=blob):
                self.assertTrue(self._estado(blob))

    def test_desactivadas(self):
        for blob in ("hex:030000009a2a61623d2dda01",       # desactivada con marca de tiempo
                     "hex:070000000000000000000000"):
            with self.subTest(blob=blob):
                self.assertFalse(self._estado(blob))

    def test_sin_blob_se_ejecuta(self):
        # Es el caso de una app recién instalada: el valor de Run ya existe pero
        # Windows todavía no ha escrito su estado. La ejecuta.
        self.assertTrue(self._estado(None))

    def test_blob_vacio_no_revienta(self):
        self.assertTrue(self._estado("hex:"))


class CapturaDeWindows11(FixtureCase):
    """Equipo real con 31 entradas, 14 de ellas desactivadas a mano."""

    def setUp(self):
        self.fx = load("startup_windows11")
        self.registro = FakeRegistry(self.fx["registry"])
        self.wmi = self.fx["startup_commands"]

    def _items(self):
        with patched(audit, self.registro, wmi=self.wmi):
            a = self.auditor()
            a.check_startup()
            return a.startup_items

    def test_cuenta_solo_las_activas(self):
        esperado = self.fx["esperado"]
        items = self._items()
        activos = [i for i in items if i["enabled"]]
        self.assertEqual(len(items), esperado["total"])
        self.assertEqual(len(activos), esperado["activos"])
        self.assertEqual(len(items) - len(activos), esperado["desactivados"])

    def test_estado_entrada_por_entrada(self):
        estado = {i["name"]: i["enabled"] for i in self._items()}
        for nombre, activo in self.fx["muestra"].items():
            with self.subTest(app=nombre):
                self.assertEqual(estado[nombre], activo)

    def test_no_duplica_por_perfil_de_usuario(self):
        # Win32_StartupCommand devuelve las claves Run una vez por cada perfil del
        # equipo, incluido .DEFAULT. Sumarlas a las que ya se leen del registro
        # inflaba la cuenta.
        items = self._items()
        claves = [(i["name"], i["location"]) for i in items]
        self.assertEqual(len(claves), len(set(claves)))
        repetida = self.fx["duplicado_entre_perfiles"]
        self.assertGreater(sum(1 for c in self.wmi if c["Name"] == repetida), 1)
        self.assertEqual([i["name"] for i in items].count(repetida), 1)

    def test_incluye_las_carpetas_de_inicio(self):
        carpetas = [i for i in self._items() if i["location"] == "Carpeta Inicio"]
        self.assertEqual(sorted(i["name"] for i in carpetas), self.fx["carpetas_inicio"])

    def test_el_historico_de_startupapproved_no_cuenta(self):
        # StartupApproved conserva el estado de aplicaciones cuyo valor de Run ya
        # no existe (desinstaladas). Si se recorriera esa clave como origen en vez
        # de usarla solo como consulta, aparecerían programas que no arrancan
        # porque ni siquiera están instalados.
        nombres = {i["name"] for i in self._items()}
        for fantasma in self.fx["solo_en_startupapproved"]:
            with self.subTest(app=fantasma):
                self.assertNotIn(fantasma, nombres)

    def test_el_hallazgo_usa_la_cuenta_de_activas(self):
        with patched(audit, self.registro, wmi=self.wmi):
            a = self.auditor()
            resumen = a.check_startup()
        activos = str(self.fx["esperado"]["activos"])
        apagados = str(self.fx["esperado"]["desactivados"])
        # Lo que se juzga es lo que arranca. Contando las 31 el hallazgo salía
        # como si hubiera el doble de programas de los que se ejecutan.
        self.assertIn(activos, resumen)
        self.assertIn(apagados, resumen)
        hallazgo = next(f for f in a.findings if f.id == "startup_bloat")
        self.assertIn(activos, hallazgo.title)
        self.assertIn(f"{apagados} entradas ya desactivadas", hallazgo.detail)
        # Y ninguna desactivada se cuela en la lista de ejemplos del detalle.
        for item in a.startup_items:
            if not item["enabled"]:
                self.assertNotIn(item["name"], hallazgo.detail.split("Activos:")[1])


class SinDatos(FixtureCase):
    def test_sin_entradas(self):
        with patched(audit, FakeRegistry({}), wmi=[]):
            a = self.auditor()
            self.assertIn("0", a.check_startup())
            self.assertEqual(a.findings, [])


if __name__ == "__main__":
    unittest.main()
