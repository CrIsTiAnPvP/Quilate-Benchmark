"""Grabación en segundo plano de Xbox Game Bar.

Lo que cuesta FPS es el búfer permanente de «Grabar lo que ha pasado»
(HistoricalCaptureEnabled), no que la Game Bar pueda grabar si se lo pides.
GameDVR_Enabled y AppCaptureEnabled valen 1 en una instalación recién hecha, así
que mirarlos a ellos marcaba el hallazgo en prácticamente cualquier equipo.
"""

import pytest

from quilate import audit
from tests.support import FakeRegistry, patched

CONFIG = r"HKCU\System\GameConfigStore"
GAMEDVR = r"HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR"
POLITICA = r"HKLM\SOFTWARE\Policies\Microsoft\Windows\GameDVR"

ENCENDIDO = {"GameDVR_Enabled": 1}


@pytest.fixture
def auditar_gamedvr(auditor):
    """Audita Game DVR con el registro que se le describa. Devuelve `(auditor, resumen)`."""
    def auditar(config=None, gamedvr=None, politica=None):
        arbol = {CONFIG: config or {}, GAMEDVR: gamedvr or {}, POLITICA: politica or {}}
        with patched(audit, FakeRegistry(arbol)):
            a = auditor()
            return a, a.check_game_dvr()
    return auditar


def hallazgos(a):
    return [f for f in a.findings if f.id == "game_dvr"]


def test_la_instalacion_limpia_capturada_no_genera_hallazgo(captura, auditor):
    """Captura real: capturas nunca tocadas, así que no hay búfer en marcha."""
    fx = captura("gamedvr_instalacion_limpia")
    # El equipo capturado tiene GameDVR_Enabled=1 y ningún otro valor.
    assert fx["registry"][CONFIG]["GameDVR_Enabled"] == 1
    assert fx["registry"][GAMEDVR] == {}

    with patched(audit, FakeRegistry(fx["registry"])):
        a = auditor()
        resumen = a.check_game_dvr()
    assert resumen == fx["esperado"]["resumen"]
    assert a.findings == []


# El caso `appcapture-a-cero` era el único de los cinco que no miraba los
# hallazgos, solo el resumen: un hueco sin motivo, porque «desactivada» significa
# exactamente que no hay nada que reportar. Ahora los cinco declaran las dos
# cosas, que es lo que hace útil la tabla: el resumen y si eso genera hallazgo
# son la misma decisión vista por sus dos caras.
@pytest.mark.parametrize("registro, resumen_esperado, hay_hallazgo", [
    (dict(config=ENCENDIDO, gamedvr={"HistoricalCaptureEnabled": 1}),
     "activa", True),
    (dict(config=ENCENDIDO, gamedvr={"HistoricalCaptureEnabled": 0}),
     "sin grabación en segundo plano", False),
    (dict(config={"GameDVR_Enabled": 0}, gamedvr={"HistoricalCaptureEnabled": 1}),
     "desactivada", False),
    (dict(config=ENCENDIDO, gamedvr={"AppCaptureEnabled": 0,
                                     "HistoricalCaptureEnabled": 1}),
     "desactivada", False),
    (dict(config=ENCENDIDO, gamedvr={"HistoricalCaptureEnabled": 1},
          politica={"AllowGameDVR": 0}),
     "desactivada por directiva", False),
], ids=["buffer-activo", "interruptor-apagado", "capturas-desactivadas",
        "appcapture-a-cero", "directiva-de-equipo"])
def test_que_interruptor_decide_el_estado_de_la_grabacion(
        auditar_gamedvr, registro, resumen_esperado, hay_hallazgo):
    a, resumen = auditar_gamedvr(**registro)
    assert resumen == resumen_esperado
    assert bool(hallazgos(a)) is hay_hallazgo


def test_el_paso_cita_el_nombre_actual_del_interruptor(auditar_gamedvr):
    # Windows 11 lo llama «Grabar lo que ha pasado»; el texto decía otra cosa.
    a, _ = auditar_gamedvr(config=ENCENDIDO, gamedvr={"HistoricalCaptureEnabled": 1})
    pasos = next(f for f in a.findings if f.id == "game_dvr").steps
    assert "Grabar lo que ha pasado" in pasos[0]
