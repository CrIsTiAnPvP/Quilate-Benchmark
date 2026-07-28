"""Andamiaje de los tests: registro y WMI falsos servidos desde los fixtures.

El objetivo es que la lógica se pueda probar en cualquier sistema, no solo en
Windows. Las funciones que leen el registro lo hacen a través de `reg_read` y
`reg_list_values`, importadas en el espacio de nombres de cada módulo, así que
basta con sustituirlas ahí.
"""

from __future__ import annotations

import json
import pathlib
import unittest
from contextlib import contextmanager

from quilate.platform_utils import PSResult

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# Las mismas constantes que winreg, para no depender de tenerlo.
HKCU = 0x80000001
HKLM = 0x80000002
_PREFIX = {HKCU: "HKCU", HKLM: "HKLM"}


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeRegistry:
    """Sirve valores desde un dict {"HKCU\\ruta": {nombre: valor}}.

    Los binarios van en el fixture como "hex:0300...", que es como se guardan
    los blobs de StartupApproved, y aquí se devuelven ya como bytes.
    """

    def __init__(self, tree: dict):
        self.tree = {k.lower(): v for k, v in tree.items()}

    def _key(self, hive: int, path: str) -> dict | None:
        return self.tree.get(f"{_PREFIX[hive]}\\{path}".lower())

    def read(self, hive: int, path: str, name: str):
        values = self._key(hive, path)
        if values is None or name not in values:
            return None
        value = values[name]
        if isinstance(value, str) and value.startswith("hex:"):
            return bytes.fromhex(value[4:])
        return value

    def list_values(self, hive: int, path: str) -> dict:
        return dict(self._key(hive, path) or {})

    def key_readable(self, hive: int, path: str) -> bool:
        """Una clave que el fixture no declara es una clave que no se abre."""
        return self._key(hive, path) is not None


class _FakeWinreg:
    HKEY_CURRENT_USER = HKCU
    HKEY_LOCAL_MACHINE = HKLM


def lote_elevado(bloques: dict | None) -> dict:
    """El lote con permisos ya recogido, para los tests que lo necesitan.

    Las claves que no se den salen como «no se pidieron permisos», que es lo que
    contesta de verdad un proceso sin elevar. Así un test que solo habla del TPM
    no tiene que saber nada del resto del lote, y ninguna comprobación se
    encuentra un `KeyError` por una clave que nadie mencionó.
    """
    from quilate.elevacion import NO_PEDIDOS, _CONSULTAS_ELEVADAS
    lote = {clave: PSResult((), ok=False, error=NO_PEDIDOS)
            for clave in _CONSULTAS_ELEVADAS}
    for clave, valor in (bloques or {}).items():
        lote[clave] = valor if isinstance(valor, PSResult) else PSResult(valor)
    return lote


@contextmanager
def patched(module, registry: FakeRegistry | None = None, wmi=None, elevado=None,
            **overrides):
    """Sustituye el acceso al sistema dentro de un módulo y lo restaura al salir.

    `wmi` reemplaza a `ps_json`; puede ser una lista (misma respuesta siempre) o
    un invocable que reciba la consulta. `elevado` es lo que habría devuelto el
    proceso con permisos, por clave del lote. `overrides` sustituye cualquier
    otro nombre del módulo: `patched(audit, boot_performance=lambda: {...})`.
    """
    from quilate import elevacion
    nombres = ("reg_read", "reg_list_values", "reg_key_readable", "ps_json",
               "winreg", "IS_WINDOWS")
    original = {name: getattr(module, name, None)
                for name in nombres + tuple(overrides)}
    try:
        if registry is not None:
            module.reg_read = registry.read
            module.reg_list_values = registry.list_values
            module.reg_key_readable = registry.key_readable
        if wmi is not None:
            module.ps_json = wmi if callable(wmi) else (lambda *a, **k: wmi)
        for name, value in overrides.items():
            setattr(module, name, value if callable(value) else (lambda *a, **k: value))
        # Siempre, aunque no se pida: sin esto, una comprobación que ahora lee
        # del lote elevado se pondría a hablar con Windows de verdad en mitad de
        # la suite, y en un equipo elevado hasta a lanzar PowerShell.
        elevacion._recogido = lote_elevado(elevado)
        module.winreg = _FakeWinreg
        module.IS_WINDOWS = True
        yield
    finally:
        elevacion.olvidar()
        for name, value in original.items():
            if value is None and not hasattr(module, name):
                continue
            setattr(module, name, value)


class FixtureCase(unittest.TestCase):
    """Base con los ayudantes que usan casi todos los tests."""

    def auditor(self, si=None):
        from quilate.audit import Auditor
        from quilate.sysinfo import SystemInfo
        return Auditor(si or SystemInfo(), None)
