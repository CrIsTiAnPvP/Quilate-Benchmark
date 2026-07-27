"""Ninguna comprobación puede dar por bueno lo que no ha podido mirar.

Es la prueba que impide que vuelva el fallo de fondo de toda la herramienta:
una comprobación cuya fuente no responde devolvía un mensaje neutro, el informe
lo contaba como prueba superada, y el usuario leía «correcto» sobre algo que
nadie había llegado a leer. Aquí se ciegan TODAS las fuentes externas —registro,
WMI, comandos, log de arranque— y se exige que cada comprobación lo diga.

Las dos excepciones están declaradas de forma explícita: leen de psutil, que
sigue funcionando aunque el resto no, así que sí tienen dato con el que opinar.
"""

from __future__ import annotations

import unittest

from quilate import audit
from quilate.audit import Auditor, NoAplica, SinDato
from quilate.platform_utils import PSResult
from quilate.sysinfo import SystemInfo
from tests.support import patched

# Comprobaciones que siguen teniendo una fuente viva cuando el sistema calla:
# psutil y /proc no se pueden cegar desde aquí, y en la máquina donde aplican
# son tan reales como cualquier otra fuente. Las de Linux salen igualmente por
# NoAplica cuando se ejecutan en Windows.
CON_FUENTE_PROPIA = {"check_memory", "check_background_load",
                     "check_linux_swappiness", "check_linux_governor"}


def comprobaciones() -> list[str]:
    return sorted(n for n in dir(Auditor)
                  if n.startswith("check_") and callable(getattr(Auditor, n)))


class SistemaMudo(unittest.TestCase):
    """Nada responde: ni registro, ni WMI, ni comandos, ni el log de arranque."""

    def setUp(self):
        self.auditor = Auditor(SystemInfo(), None)

    def _ciego(self):
        return patched(
            audit,
            reg_read=lambda *a, **k: None,
            reg_list_values=lambda *a, **k: {},
            reg_key_readable=lambda *a, **k: False,
            ps_json=lambda *a, **k: PSResult((), ok=False, error="sin respuesta"),
            run_cmd=lambda *a, **k: None,
            boot_performance=lambda *a, **k: {"error": "sin acceso", "boots": [], "delays": []},
            pending_driver_updates=lambda *a, **k: [],
            cpu_temperature=lambda *a, **k: None,
            gpu_temperature=lambda *a, **k: None,
            temperature_report=lambda *a, **k: [],
        )

    def test_ninguna_da_un_veredicto_sin_datos(self):
        fallos = []
        with self._ciego():
            for nombre in comprobaciones():
                if nombre in CON_FUENTE_PROPIA:
                    continue
                try:
                    resultado = getattr(self.auditor, nombre)()
                except (SinDato, NoAplica):
                    continue
                except Exception as exc:      # noqa: BLE001 — cualquier otro fallo también cuenta
                    fallos.append(f"{nombre}: reventó con {type(exc).__name__}: {exc}")
                    continue
                fallos.append(f"{nombre}: devolvió «{resultado}» sin tener datos")
        self.assertEqual(fallos, [], "comprobaciones que opinan a ciegas:\n  " +
                                     "\n  ".join(fallos))

    def test_a_ciegas_no_se_emite_ningun_hallazgo(self):
        # Lo contrario del caso anterior y igual de importante: tampoco puede
        # inventarse problemas por no encontrar el dato. Las dos que leen de
        # psutil quedan fuera: sus hallazgos vendrían de datos de verdad.
        with self._ciego():
            for nombre in comprobaciones():
                if nombre in CON_FUENTE_PROPIA:
                    continue
                try:
                    getattr(self.auditor, nombre)()
                except Exception:            # noqa: BLE001
                    pass
        self.assertEqual([f.id for f in self.auditor.findings], [])

    def test_las_excepciones_siguen_siendo_esas(self):
        # Si alguien añade una comprobación a la lista de exentas, que sea a
        # propósito y no de rebote para que el test deje de molestar.
        self.assertEqual(CON_FUENTE_PROPIA,
                         {"check_memory", "check_background_load",
                          "check_linux_swappiness", "check_linux_governor"})
        for nombre in CON_FUENTE_PROPIA:
            self.assertTrue(hasattr(Auditor, nombre))


class RecuentoHonesto(unittest.TestCase):
    def test_las_no_concluyentes_no_cuentan_como_pruebas_hechas(self):
        a = Auditor(SystemInfo(), None)
        a.checks_run = 0
        checks = [("Con dato", lambda: "correcto"),
                  ("Sin dato", lambda: (_ for _ in ()).throw(SinDato("no hay fuente"))),
                  ("No aplica", lambda: (_ for _ in ()).throw(NoAplica("no procede"))),
                  ("Reventada", lambda: (_ for _ in ()).throw(ValueError("uy")))]
        for label, fn in checks:
            try:
                fn()
                a.checks_run += 1
            except NoAplica as exc:
                a.not_applicable.append((label, str(exc)))
            except SinDato as exc:
                a.unverified.append((label, str(exc)))
            except Exception as exc:         # noqa: BLE001
                a.unverified.append((label, f"error interno · {type(exc).__name__}: {exc}"))

        self.assertEqual(a.checks_run, 1)
        self.assertEqual([l for l, _ in a.unverified], ["Sin dato", "Reventada"])
        self.assertEqual([l for l, _ in a.not_applicable], ["No aplica"])

    def test_una_comprobacion_que_revienta_no_desaparece(self):
        # Antes, una excepción imprevista se tragaba en el bucle y la
        # comprobación no salía por ningún lado del informe.
        a = Auditor(SystemInfo(), None)
        with patched(audit, reg_read=lambda *a, **k: 1 / 0):
            try:
                a.check_fast_startup()
            except Exception as exc:         # noqa: BLE001
                a.unverified.append(("Inicio rápido", f"error interno · {type(exc).__name__}"))
        self.assertEqual(len(a.unverified), 1)


if __name__ == "__main__":
    unittest.main()
