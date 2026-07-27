"""Grabación en segundo plano de Xbox Game Bar.

Lo que cuesta FPS es el búfer permanente de «Grabar lo que ha pasado»
(HistoricalCaptureEnabled), no que la Game Bar pueda grabar si se lo pides.
GameDVR_Enabled y AppCaptureEnabled valen 1 en una instalación recién hecha, así
que mirarlos a ellos marcaba el hallazgo en prácticamente cualquier equipo.
"""

import unittest

from quilate import audit
from tests.support import FakeRegistry, FixtureCase, load, patched

CONFIG = r"HKCU\System\GameConfigStore"
GAMEDVR = r"HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR"
POLITICA = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR"


class InstalacionLimpia(FixtureCase):
    """Captura real: capturas nunca tocadas, así que no hay búfer en marcha."""

    def test_no_hay_hallazgo(self):
        fx = load("gamedvr_instalacion_limpia")
        # El equipo capturado tiene GameDVR_Enabled=1 y ningún otro valor.
        self.assertEqual(fx["registry"][CONFIG]["GameDVR_Enabled"], 1)
        self.assertEqual(fx["registry"][GAMEDVR], {})

        with patched(audit, FakeRegistry(fx["registry"])):
            a = self.auditor()
            resumen = a.check_game_dvr()
        self.assertEqual(resumen, fx["esperado"]["resumen"])
        self.assertEqual(a.findings, [])


class Estados(FixtureCase):
    def _auditar(self, config=None, gamedvr=None, politica=None):
        arbol = {CONFIG: config or {}, GAMEDVR: gamedvr or {}, POLITICA: politica or {}}
        with patched(audit, FakeRegistry(arbol)):
            a = self.auditor()
            return a, a.check_game_dvr()

    def test_grabacion_en_segundo_plano_activa(self):
        a, resumen = self._auditar(config={"GameDVR_Enabled": 1},
                                   gamedvr={"HistoricalCaptureEnabled": 1})
        self.assertEqual(resumen, "activa")
        self.assertTrue([f for f in a.findings if f.id == "game_dvr"])

    def test_interruptor_apagado_explicitamente(self):
        a, resumen = self._auditar(config={"GameDVR_Enabled": 1},
                                   gamedvr={"HistoricalCaptureEnabled": 0})
        self.assertEqual(resumen, "sin grabación en segundo plano")
        self.assertEqual(a.findings, [])

    def test_capturas_desactivadas_del_todo(self):
        a, resumen = self._auditar(config={"GameDVR_Enabled": 0},
                                   gamedvr={"HistoricalCaptureEnabled": 1})
        self.assertEqual(resumen, "desactivada")
        self.assertEqual(a.findings, [])

    def test_appcapture_a_cero_corta_igual(self):
        a, resumen = self._auditar(config={"GameDVR_Enabled": 1},
                                   gamedvr={"AppCaptureEnabled": 0,
                                            "HistoricalCaptureEnabled": 1})
        self.assertEqual(resumen, "desactivada")

    def test_directiva_de_equipo_manda(self):
        a, resumen = self._auditar(config={"GameDVR_Enabled": 1},
                                   gamedvr={"HistoricalCaptureEnabled": 1},
                                   politica={"AllowGameDVR": 0})
        self.assertEqual(resumen, "desactivada por directiva")
        self.assertEqual(a.findings, [])

    def test_el_paso_cita_el_nombre_actual_del_interruptor(self):
        # Windows 11 lo llama «Grabar lo que ha pasado»; el texto decía otra cosa.
        a, _ = self._auditar(config={"GameDVR_Enabled": 1},
                             gamedvr={"HistoricalCaptureEnabled": 1})
        pasos = next(f for f in a.findings if f.id == "game_dvr").steps
        self.assertIn("Grabar lo que ha pasado", pasos[0])


if __name__ == "__main__":
    unittest.main()
