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
