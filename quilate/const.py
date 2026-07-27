"""Constantes globales: branding del proyecto y deteccion de plataforma.

Este modulo no importa nada del paquete: es la base de la que cuelga el resto.
"""

from __future__ import annotations

import platform


# ------------------------------------------------------------------------------
# BRANDING
# ------------------------------------------------------------------------------
APP_NAME = "Quilate Suite"
APP_VERSION = "2.4.0"
AUTHOR = "Cristian Alonso"
WEBSITE = "cristianac.es"
WEBSITE_URL = "https://cristianac.es"

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MAC = platform.system() == "Darwin"

CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
