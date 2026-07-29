"""Limpiar la pantalla no justifica lanzar un proceso.

`cls` y `clear` costaban un hijo por ejecución y ponían la limpieza de pantalla
en manos de `%COMSPEC%` en Windows y del `clear` que hubiera en el `PATH` en
Linux. La secuencia VT100 hace lo mismo, ya está activada, y no depende de nadie.
"""

from __future__ import annotations

import io
import subprocess
import unittest
from contextlib import redirect_stdout

from quilate import console

BORRAR = "\033[2J\033[H"


class _SalidaFalsa(io.StringIO):
    def __init__(self, terminal: bool):
        super().__init__()
        self._terminal = terminal

    def isatty(self) -> bool:
        return self._terminal


class LimpiarPantalla(unittest.TestCase):
    def setUp(self):
        # Cualquier intento de crear un proceso hijo, venga de donde venga, tiene
        # que hacer fallar el test: es justo lo que se ha quitado.
        self.popen_original = subprocess.Popen
        subprocess.Popen = self._prohibido

    def tearDown(self):
        subprocess.Popen = self.popen_original

    @staticmethod
    def _prohibido(*args, **kwargs):
        raise AssertionError(f"se ha lanzado un proceso para limpiar la pantalla: {args}")

    def _limpiar(self, terminal: bool) -> str:
        salida = _SalidaFalsa(terminal)
        with redirect_stdout(salida):
            console.clear_screen()
        return salida.getvalue()

    def test_en_un_terminal_se_borra_con_vt100(self):
        self.assertEqual(self._limpiar(terminal=True), BORRAR)

    def test_con_la_salida_redirigida_no_se_escribe_nada(self):
        # El código de control ensuciaría el fichero o el proceso de destino.
        self.assertEqual(self._limpiar(terminal=False), "")

    def test_el_modulo_ya_no_necesita_subprocess(self):
        self.assertFalse(hasattr(console, "subprocess"),
                         "queda un import de subprocess sin usar")


if __name__ == "__main__":
    unittest.main()
