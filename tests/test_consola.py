"""Limpiar la pantalla no justifica lanzar un proceso.

`cls` y `clear` costaban un hijo por ejecución y ponían la limpieza de pantalla
en manos de `%COMSPEC%` en Windows y del `clear` que hubiera en el `PATH` en
Linux. La secuencia VT100 hace lo mismo, ya está activada, y no depende de nadie.
"""

from __future__ import annotations

import io
import subprocess
from contextlib import redirect_stdout

import pytest

from quilate import console

BORRAR = "\033[2J\033[H"


class _SalidaFalsa(io.StringIO):
    def __init__(self, terminal: bool):
        super().__init__()
        self._terminal = terminal

    def isatty(self) -> bool:
        return self._terminal


@pytest.fixture(autouse=True)
def sin_procesos_hijo(monkeypatch):
    """Cualquier intento de crear un proceso hijo hace fallar el test.

    Es justo lo que se ha quitado del módulo. Va con `monkeypatch` y no con
    `setUp`/`tearDown` porque `subprocess.Popen` es estado global del intérprete:
    si el test reventara entre el parcheo y la restauración, el `tearDown` de
    antes tampoco se saltaría, pero `monkeypatch` deshace el cambio sin que haya
    que acordarse de escribirlo.
    """
    def prohibido(*args, **kwargs):
        raise AssertionError(
            f"se ha lanzado un proceso para limpiar la pantalla: {args}")

    monkeypatch.setattr(subprocess, "Popen", prohibido)


def limpiar(terminal: bool) -> str:
    salida = _SalidaFalsa(terminal)
    with redirect_stdout(salida):
        console.clear_screen()
    return salida.getvalue()


def test_en_un_terminal_se_borra_con_vt100():
    assert limpiar(terminal=True) == BORRAR


def test_con_la_salida_redirigida_no_se_escribe_nada():
    # El código de control ensuciaría el fichero o el proceso de destino.
    assert limpiar(terminal=False) == ""


def test_el_modulo_ya_no_necesita_subprocess():
    assert not hasattr(console, "subprocess"), "queda un import de subprocess sin usar"


# ===================================================== el giro de la espera ==
#
# Existe por el lote con permisos: ese paso se va a otro proceso y tarda entre
# cinco y treinta segundos, durante los cuales antes no se imprimía nada. Una
# línea a medias que no avanza, justo después de conceder permisos de
# administrador, se lee como un cuelgue —y de hecho se leyó así—.


@pytest.fixture(autouse=True)
def giro_limpio():
    """El giro guarda estado entre llamadas, y es global del módulo.

    Sin esto, un test que dejara el giro a medias le pasaría al siguiente unos
    retrocesos que no le corresponden.
    """
    console.spinner_stop()
    yield
    console.spinner_stop()


def _pintar(terminal: bool, vueltas: int = 4) -> str:
    salida = _SalidaFalsa(terminal)
    with redirect_stdout(salida):
        console.spinner_step("Consultando".ljust(38))
        for segundo in range(vueltas):
            console.spinner_tick(f"{segundo} s")
        console.spinner_done("9 de 9 en 3 s")
    return salida.getvalue()


def _visible(crudo: str) -> str:
    """Lo que quedaría en pantalla, aplicando los retrocesos como el terminal."""
    pantalla: list[str] = []
    for caracter in crudo:
        if caracter == "\b":
            if pantalla:
                pantalla.pop()
        else:
            pantalla.append(caracter)
    return "".join(pantalla)


def test_el_giro_anima_mientras_se_espera():
    crudo = _pintar(terminal=True)
    assert sum(crudo.count(c) for c in console._GIRO) == 4
    assert crudo.count("\b") > 0


def test_el_giro_no_deja_restos_delante_del_resultado():
    # Lo que se veía mal antes de `spinner_stop`: el último fotograma se quedaba
    # fosilizado entre el texto del paso y el ✓.
    visible = _visible(_pintar(terminal=True))
    assert "9 de 9 en 3 s" in visible
    for fotograma in console._GIRO:
        assert fotograma not in visible, f"ha quedado un {fotograma!r} en la línea"


def test_con_la_salida_redirigida_el_giro_no_escribe_nada():
    # Los retrocesos y los fotogramas ensuciarían el fichero de destino. Se
    # compara contra la misma secuencia sin animar: tienen que ser idénticas.
    con_giro = _pintar(terminal=False, vueltas=6)
    salida = _SalidaFalsa(False)
    with redirect_stdout(salida):
        console.spinner_step("Consultando".ljust(38))
        console.spinner_done("9 de 9 en 3 s")
    assert con_giro == salida.getvalue()
    assert "\b" not in con_giro


def test_el_giro_sobrevive_a_una_salida_rota():
    """Un adorno no puede tumbar un análisis que ya está hecho."""
    class Rota(io.StringIO):
        def isatty(self):
            return True

        def write(self, _texto):
            raise OSError("la consola se ha ido")

    with redirect_stdout(Rota()):
        console.spinner_tick("1 s")
        console.spinner_stop()
