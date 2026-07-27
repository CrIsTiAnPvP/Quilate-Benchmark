"""Duración del arranque leída del registro de rendimiento de Windows.

Convierte el hallazgo de programas de inicio, que era una estimación, en una
medida: Windows cronometra cada encendido y anota qué lo retrasó.

Los eventos de estos tests son SINTÉTICOS, construidos según el esquema del log
`Microsoft-Windows-Diagnostics-Performance/Operational`. Prueban el parseo y los
umbrales, no que el esquema real coincida — para eso hace falta un volcado con
privilegios de administrador.
"""

import unittest

from quilate import audit
from quilate.sysinfo import SystemInfo
from tests.support import FixtureCase, patched


def arranque(ms, actualizacion=False, total=None):
    return {"time": "2026-07-27T09:00:00", "fields": {
        "MainPathBootTime": str(ms),
        "BootTime": str(total if total is not None else ms + 8000),
        "BootPostBootTime": "8000",
        "BootIsRebootAfterInstall": "1" if actualizacion else "0",
        "BootNumStartupApps": "15"}}


def retraso(nombre, ms, kind="aplicación"):
    return {"time": "2026-07-27T09:00:00", "kind": kind,
            "fields": {"Name": nombre, "TotalTime": str(ms), "DegradationTime": str(ms // 2)}}


def informe(boots, delays=()):
    return {"error": None, "boots": list(boots), "delays": list(delays)}


class SinPrivilegios(FixtureCase):
    def test_no_se_confunde_con_arranque_limpio(self):
        # PowerShell devuelve «no se encontraron eventos» cuando en realidad es
        # que la ACL del log no deja leerlo. Decir «0 s» sería mentir.
        si = SystemInfo()
        si.is_admin = False
        fallo = {"error": "No se encontraron eventos que coincidan...",
                 "boots": [], "delays": []}
        with patched(audit, boot_performance=lambda *a, **k: fallo):
            a = self.auditor(si)
            with self.assertRaises(audit.SinDato) as ctx:
                a.check_boot_time()
        self.assertIn("administrador", str(ctx.exception))
        self.assertEqual(a.findings, [])
        self.assertEqual(a.boot_seconds, None)

    def test_con_privilegios_se_reporta_el_error_real(self):
        si = SystemInfo()
        si.is_admin = True
        with patched(audit, boot_performance=lambda *a, **k: {
                "error": "el log está deshabilitado", "boots": [], "delays": []}):
            a = self.auditor(si)
            with self.assertRaises(audit.SinDato) as ctx:
                a.check_boot_time()
        self.assertIn("el log está deshabilitado", str(ctx.exception))
        self.assertEqual(a.findings, [])


class Umbrales(FixtureCase):
    def _auditar(self, *ms, delays=()):
        si = SystemInfo()
        si.is_admin = True
        with patched(audit, boot_performance=lambda *a, **k: informe(
                [arranque(m) for m in ms], delays)):
            a = self.auditor(si)
            return a, a.check_boot_time()

    def test_arranque_rapido_no_genera_hallazgo(self):
        a, resumen = self._auditar(18_000, 21_000, 19_000)
        self.assertEqual(a.findings, [])
        self.assertIn("19 s", resumen)
        self.assertAlmostEqual(a.boot_seconds, 19.0)

    def test_severidad_creciente(self):
        for ms, severidad in ((45_000, "low"), (80_000, "medium"), (130_000, "high")):
            with self.subTest(ms=ms):
                a, _ = self._auditar(ms)
                hallazgo = next(f for f in a.findings if f.id == "boot_slow")
                self.assertEqual(hallazgo.severity, severidad)

    def test_usa_la_mediana_no_el_ultimo(self):
        # Un arranque anómalo no debe disparar el hallazgo él solo.
        a, _ = self._auditar(18_000, 20_000, 19_000, 240_000)
        self.assertEqual(a.findings, [])

    def test_la_ganancia_se_declara_medida(self):
        a, _ = self._auditar(130_000)
        hallazgo = next(f for f in a.findings if f.id == "boot_slow")
        self.assertIn("medido", hallazgo.gain_note)


class ReiniciosDeActualizacion(FixtureCase):
    def test_no_entran_en_la_mediana(self):
        # Un reinicio tras instalar actualizaciones siempre es lento y no
        # representa el arranque de cada día.
        si = SystemInfo()
        si.is_admin = True
        boots = [arranque(18_000), arranque(200_000, actualizacion=True),
                 arranque(20_000), arranque(210_000, actualizacion=True)]
        with patched(audit, boot_performance=lambda *a, **k: informe(boots)):
            a = self.auditor(si)
            resumen = a.check_boot_time()
        self.assertEqual(a.findings, [])
        self.assertIn("2 arranques", resumen)

    def test_si_todos_son_de_actualizacion_no_se_inventa_nada(self):
        si = SystemInfo()
        si.is_admin = True
        with patched(audit, boot_performance=lambda *a, **k: informe(
                [arranque(200_000, actualizacion=True)])):
            a = self.auditor(si)
            with self.assertRaises(audit.SinDato):
                a.check_boot_time()
        self.assertEqual(a.findings, [])


class Culpables(FixtureCase):
    def _auditar(self, delays):
        si = SystemInfo()
        si.is_admin = True
        with patched(audit, boot_performance=lambda *a, **k: informe(
                [arranque(95_000)], delays)):
            a = self.auditor(si)
            a.check_boot_time()
            return next(f for f in a.findings if f.id == "boot_slow")

    def test_se_ordenan_por_tiempo_y_el_peor_va_primero(self):
        hallazgo = self._auditar([
            retraso("ActualizadorLento", 19_000),
            retraso("ServicioRegular", 4_000, kind="servicio"),
            retraso("DriverPesado", 11_000, kind="driver")])
        self.assertIn("ActualizadorLento (19.0 s, aplicación)", hallazgo.detail)
        self.assertIn("DriverPesado (11.0 s, driver)", hallazgo.detail)
        self.assertIn("ActualizadorLento", hallazgo.steps[0])
        self.assertLess(hallazgo.detail.index("ActualizadorLento"),
                        hallazgo.detail.index("DriverPesado"))

    def test_se_agrega_por_nombre_quedandose_con_el_peor(self):
        # El mismo programa aparece en cada arranque; interesa su peor caso, no
        # una entrada por evento.
        hallazgo = self._auditar([retraso("Repetido", 5_000),
                                  retraso("Repetido", 12_000),
                                  retraso("Repetido", 7_000)])
        self.assertIn("Repetido (12.0 s", hallazgo.detail)
        self.assertEqual(hallazgo.detail.count("Repetido"), 1)

    def test_sin_culpables_se_dice_expresamente(self):
        hallazgo = self._auditar([])
        self.assertIn("no ha señalado ningún culpable", hallazgo.detail)


class EsquemaInesperado(FixtureCase):
    """El log lo escribe Windows; sus campos pueden no ser los previstos."""

    def _auditar(self, boots, delays=()):
        si = SystemInfo()
        si.is_admin = True
        with patched(audit, boot_performance=lambda *a, **k: informe(boots, delays)):
            a = self.auditor(si)
            return a, a.check_boot_time()

    def test_sin_mainpath_usa_boottime(self):
        boot = {"time": "t", "fields": {"BootTime": "42000"}}
        a, resumen = self._auditar([boot])
        self.assertAlmostEqual(a.boot_seconds, 42.0)

    def test_valores_no_numericos_se_ignoran(self):
        malo = {"time": "t", "fields": {"MainPathBootTime": "n/d", "BootTime": "20000"}}
        a, _ = self._auditar([malo])
        self.assertAlmostEqual(a.boot_seconds, 20.0)

    def test_evento_sin_campos_utiles(self):
        si = SystemInfo()
        si.is_admin = True
        with patched(audit, boot_performance=lambda *a, **k: informe(
                [{"time": "t", "fields": {"Otra": "cosa"}}])):
            with self.assertRaises(audit.SinDato):
                self.auditor(si).check_boot_time()

    def test_retraso_sin_nombre_no_cuenta(self):
        a, _ = self._auditar([arranque(95_000)],
                             [{"time": "t", "kind": "aplicación",
                               "fields": {"TotalTime": "9000"}}])
        hallazgo = next(f for f in a.findings if f.id == "boot_slow")
        self.assertIn("no ha señalado ningún culpable", hallazgo.detail)

    def test_nombre_alternativo(self):
        a, _ = self._auditar([arranque(95_000)],
                             [{"time": "t", "kind": "aplicación",
                               "fields": {"FriendlyName": "PorNombreAmable",
                                          "DegradationTime": "6000"}}])
        hallazgo = next(f for f in a.findings if f.id == "boot_slow")
        self.assertIn("PorNombreAmable", hallazgo.detail)


if __name__ == "__main__":
    unittest.main()
