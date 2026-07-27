"""Quilate Suite · benchmark, auditoría de optimización y estimación de mejora.

El paquete está dividido por responsabilidades y el orden de importación va
siempre de lo general a lo concreto, sin ciclos:

    const ─ console ─ platform_utils        (base, sin dependencias del paquete)
      └─ workloads ─ sysinfo ─ benchmark ─ audit ─ projection ─ components
           └─ report ─ export/{json,html,plan} ─ cli

`quilate.py`, en la raíz del proyecto, es solo el lanzador.
"""

from __future__ import annotations

from .const import APP_NAME, APP_VERSION, AUTHOR, WEBSITE, WEBSITE_URL

__all__ = ["APP_NAME", "APP_VERSION", "AUTHOR", "WEBSITE", "WEBSITE_URL"]
