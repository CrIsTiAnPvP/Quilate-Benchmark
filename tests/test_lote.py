"""Ocho consultas en un proceso, sin que ninguna deje de valerse por sí misma.

Cada comprobación arrancaba su propio `powershell.exe`. Arrancar PowerShell
cuesta bastante más que la consulta que se le pide, así que ocho procesos para
ocho preguntas era casi todo overhead.

Lo que se comprueba aquí no es la velocidad —eso no se mide en un test— sino
las tres propiedades de las que depende que la fusión sea segura: que el lote
cubre exactamente lo que dice cubrir, que una comprobación llamada suelta sigue
funcionando sin él, y que un lote que se va al traste deja a todas «sin dato»
en vez de dejarlas creyendo que preguntaron y no había nada.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import unittest
from unittest import mock

from quilate import audit
from quilate.audit import Auditor
from quilate.audit import lote
from quilate.platform_utils import PSResult
from quilate.sysinfo import SystemInfo
from tests.support import patched

PAQUETE = pathlib.Path(audit.__file__).parent


def claves_pedidas() -> set[str]:
    """Las claves que el código pide de verdad con `self._consulta(...)`."""
    pedidas = set()
    for fichero in sorted(PAQUETE.rglob("*.py")):
        for nodo in ast.walk(ast.parse(fichero.read_text(encoding="utf-8"))):
            if (isinstance(nodo, ast.Call)
                    and getattr(nodo.func, "attr", "") == "_consulta"
                    and nodo.args and isinstance(nodo.args[0], ast.Constant)):
                pedidas.add(nodo.args[0].value)
    return pedidas


class ElLoteCubreLoQueDice(unittest.TestCase):
    def test_cada_consulta_la_pide_alguna_comprobacion(self):
        # Una consulta que nadie pide es tiempo de PowerShell regalado en cada
        # ejecución, y nadie se enteraría.
        self.assertEqual(set(lote.CONSULTAS) - claves_pedidas(), set())

    def test_ninguna_comprobacion_pide_una_clave_que_no_existe(self):
        # Al revés: un `KeyError` aquí saldría como «no evaluable» en el informe
        # y solo en el equipo del usuario.
        self.assertEqual(claves_pedidas() - set(lote.CONSULTAS), set())

    def test_los_drivers_se_quedan_fuera(self):
        # La condición que pone la auditoría: `Win32_PnPSignedDriver` tarda
        # entre 2 y 11 segundos y haría esperar a las otras ocho a la más lenta.
        # Además va detrás de una bandera y no todo el mundo la pide.
        for clave, consulta in lote.CONSULTAS.items():
            with self.subTest(consulta=clave):
                self.assertNotIn("Win32_PnPSignedDriver", consulta)
        self.assertIn("Win32_PnPSignedDriver",
                      inspect.getsource(Auditor.check_old_drivers))

    def test_ninguna_consulta_del_lote_cambia_nada(self):
        # Este lote no pide permisos, pero el criterio es el mismo que el del
        # elevado: aquí solo se lee.
        prohibidos = ("Set-", "Remove-", "New-Item", "Stop-", "Start-",
                      "Disable-", "Enable-", "Clear-", "Restart-", "Add-")
        for clave, consulta in lote.CONSULTAS.items():
            for prohibido in prohibidos:
                with self.subTest(consulta=clave, prohibido=prohibido):
                    self.assertNotIn(prohibido, consulta)


class CuandoElLoteFalla(unittest.TestCase):
    def test_fuera_de_windows_no_se_lanza_nada(self):
        with mock.patch.object(lote, "IS_WINDOWS", False):
            with mock.patch.object(lote, "_ps_raw") as lanzado:
                resultado = lote.recoger()
        lanzado.assert_not_called()
        self.assertEqual(set(resultado), set(lote.CONSULTAS))
        for clave, valor in resultado.items():
            with self.subTest(consulta=clave):
                self.assertFalse(valor.ok)

    def test_si_se_va_al_traste_ninguna_queda_como_preguntada(self):
        # La diferencia que importa: «no se pudo preguntar» y «se preguntó y no
        # había nada» son cosas distintas, y confundirlas hace que el informe dé
        # por bueno lo que nadie ha llegado a mirar.
        with mock.patch.object(lote, "IS_WINDOWS", True), \
             mock.patch.object(lote, "_ps_raw", return_value=(None, "PowerShell no responde")):
            resultado = lote.recoger()
        for clave, valor in resultado.items():
            with self.subTest(consulta=clave):
                self.assertFalse(valor.ok)
                self.assertIn("PowerShell no responde", valor.error)


class UnaComprobacionSuelta(unittest.TestCase):
    """Sin lote preparado, cada una sigue preguntando por su cuenta.

    No es solo comodidad para los tests: el `Auditor` se puede usar sin llamar a
    `run()`, y una comprobación que solo funcionara dentro del lote sería una
    trampa esperando a quien lo hiciera.
    """

    def test_con_el_lote_preparado_no_se_pregunta_nada_mas(self):
        a = Auditor(SystemInfo(), None)
        a._lote = {"cortafuegos": PSResult([{"Name": "Public", "Enabled": 1}])}
        llamadas = []
        with patched(audit, ps_json=lambda *args, **k: llamadas.append(args) or PSResult()):
            a.check_firewall()
        self.assertEqual(llamadas, [], "ha lanzado una consulta que ya estaba en el lote")

    def test_sin_lote_se_pregunta_esa_sola(self):
        a = Auditor(SystemInfo(), None)
        pedidas = []

        def falso(consulta, *a_, **k):
            pedidas.append(consulta)
            return PSResult([{"Name": "Public", "Enabled": 1}])

        with patched(audit, ps_json=falso):
            a.check_firewall()
        self.assertEqual(pedidas, [lote.CONSULTAS["cortafuegos"]])

    def test_el_texto_es_el_mismo_que_se_lanzaba_antes(self):
        # La fusión reordena cuándo se pregunta, no qué. Si el texto cambiara,
        # cambiarían las columnas que llegan y ningún test de las comprobaciones
        # lo notaría: todos parten de filas ya construidas.
        self.assertIn("Get-NetFirewallProfile", lote.CONSULTAS["cortafuegos"])
        self.assertIn("Win32_PnPEntity", lote.CONSULTAS["dispositivos"])
        self.assertIn("Get-LocalUser", lote.CONSULTAS["cuentas"])
        self.assertIn("SecurityCenter2", lote.CONSULTAS["antivirus"])


if __name__ == "__main__":
    unittest.main()
