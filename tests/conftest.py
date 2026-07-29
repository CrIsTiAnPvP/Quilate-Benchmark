"""Fixtures compartidas por los tests migrados a estilo pytest.

Esto no sustituye a `support.py`, que sigue siendo donde vive el andamiaje de
verdad (el registro y el WMI falsos, `patched`, `lote_elevado`). Aquí solo se
envuelve ese andamiaje en fixtures, para los ficheros que ya no heredan de
`unittest.TestCase` y por tanto no pueden usar `FixtureCase`.

Las capturas de `fixtures/` **no se tocan**: `captura` las lee tal cual las
dejó quien las anonimizó.
"""

from __future__ import annotations

import pytest

from tests import support


@pytest.fixture
def auditor():
    """Un `Auditor` sobre el `SystemInfo` que se le pase, o uno vacío.

    Es exactamente lo que hacía `FixtureCase.auditor`, en forma de fixture: se
    devuelve la función, no un auditor ya construido, porque casi todos los
    tests necesitan primero rellenar el `SystemInfo` y solo después auditarlo.
    """
    def construir(si=None):
        from quilate.audit import Auditor
        from quilate.sysinfo import SystemInfo
        return Auditor(si or SystemInfo(), None)
    return construir


@pytest.fixture
def captura():
    """Carga una captura anonimizada de `fixtures/` por nombre, sin extensión.

    Envuelve `support.load` sin modificar el fichero de captura ni su
    contenido: la fixture existe para que un test en estilo pytest puro pueda
    pedir los datos sin importar `support` a mano, no para reinterpretarlos.
    """
    return support.load
