"""Andamiaje de los tests: registro y WMI falsos servidos desde los fixtures.

El objetivo es que la lógica se pueda probar en cualquier sistema, no solo en
Windows. Las funciones que leen el registro lo hacen a través de `reg_read` y
`reg_list_values`, importadas en el espacio de nombres de cada módulo, así que
basta con sustituirlas ahí.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import sys
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


_AUSENTE = object()   # el módulo no declaraba ese nombre antes de parchearlo


def alcanzados(module) -> list:
    """El módulo y, si es un paquete, sus submódulos ya importados.

    Una función resuelve `ps_json` o `IS_WINDOWS` en los globals del fichero
    donde está definida, no en los del paquete que la reexporta. En cuanto las
    comprobaciones viven repartidas en varios ficheros, sustituir solo el
    `__init__` deja a la mitad sin parchear —y no fallando, que sería lo
    cómodo, sino hablando con Windows de verdad en mitad de la suite.
    """
    destinos = [module]
    prefijo = module.__name__ + "."
    destinos += [m for nombre, m in sorted(sys.modules.items())
                 if nombre.startswith(prefijo) and m is not None]
    return destinos


def fuente_completa(module) -> str:
    """Todo el código del módulo, o del paquete entero si lo es.

    `inspect.getsource` sobre un paquete devuelve solo su `__init__.py`. Los
    tests que barren el código declarado —los identificadores de hallazgo, las
    severidades— no fallarían con un paquete delante: pasarían en vacío, que es
    la forma de romperse que no se nota.
    """
    if not hasattr(module, "__path__"):
        return inspect.getsource(module)
    carpeta = pathlib.Path(module.__file__).parent
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted(carpeta.rglob("*.py")))


@contextmanager
def patched(module, registry: FakeRegistry | None = None, wmi=None, elevado=None,
            **overrides):
    """Sustituye el acceso al sistema dentro de un módulo y lo restaura al salir.

    `wmi` reemplaza a `ps_json`; puede ser una lista (misma respuesta siempre) o
    un invocable que reciba la consulta. `elevado` es lo que habría devuelto el
    proceso con permisos, por clave del lote. `overrides` sustituye cualquier
    otro nombre del módulo: `patched(audit, boot_performance=lambda: {...})`.

    Si `module` es un paquete, la sustitución alcanza a sus submódulos: ver
    `alcanzados`. En un submódulo solo se toca lo que ya declaraba, para no
    inventarle nombres que no usa.
    """
    from quilate import elevacion
    destinos = alcanzados(module)
    original: dict = {}          # (módulo, nombre) -> valor de antes

    def poner(name, value):
        for destino in destinos:
            if destino is not module and not hasattr(destino, name):
                continue
            original.setdefault((destino, name), getattr(destino, name, _AUSENTE))
            setattr(destino, name, value)

    def constante(value):
        # `__v=value` fija el valor en la definición: sin eso las lambdas de
        # varios overrides comparten la variable del bucle y todas acaban
        # devolviendo la última.
        return value if callable(value) else (lambda *a, __v=value, **k: __v)

    try:
        if registry is not None:
            poner("reg_read", registry.read)
            poner("reg_list_values", registry.list_values)
            poner("reg_key_readable", registry.key_readable)
        if wmi is not None:
            poner("ps_json", constante(wmi))
        for name, value in overrides.items():
            poner(name, constante(value))
        # Siempre, aunque no se pida: sin esto, una comprobación que ahora lee
        # del lote elevado se pondría a hablar con Windows de verdad en mitad de
        # la suite, y en un equipo elevado hasta a lanzar PowerShell.
        elevacion._recogido = lote_elevado(elevado)
        poner("winreg", _FakeWinreg)
        poner("IS_WINDOWS", True)
        yield
    finally:
        elevacion.olvidar()
        for (destino, name), value in original.items():
            if value is _AUSENTE:
                delattr(destino, name)
            else:
                setattr(destino, name, value)


class FixtureCase(unittest.TestCase):
    """Base con los ayudantes que usan casi todos los tests."""

    def auditor(self, si=None):
        from quilate.audit import Auditor
        from quilate.sysinfo import SystemInfo
        return Auditor(si or SystemInfo(), None)
