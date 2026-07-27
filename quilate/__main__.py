"""Permite `python -m quilate` además de `python quilate.py`."""

from __future__ import annotations

import sys

try:
    import psutil  # noqa: F401
except ImportError:
    print("\n[!] Falta la dependencia 'psutil'.\n    Instálala con:  pip install psutil\n")
    sys.exit(1)

from .cli import run

sys.exit(run())
