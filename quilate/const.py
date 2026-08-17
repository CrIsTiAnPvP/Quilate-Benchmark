"""Constantes globales: branding del proyecto y deteccion de plataforma.

Este modulo no importa nada del paquete: es la base de la que cuelga el resto.
"""

from __future__ import annotations

import platform


# ------------------------------------------------------------------------------
# BRANDING
# ------------------------------------------------------------------------------
APP_NAME = "Quilate Suite"
APP_VERSION = "2.8.0"
AUTHOR = "Cristian Alonso"
WEBSITE = "cristianac.es"
WEBSITE_URL = "https://cristianac.es"

# ------------------------------------------------------------------------------
# TELEMETRIA
# ------------------------------------------------------------------------------
# A donde se manda el resumen de la ejecucion. Ver `telemetria.py` y PRIVACY.md.
#
# *** MARCADOR DE POSICION: el endpoint todavia no existe. ***
# Lo despliega `quilate-infra` (Supabase, region UE). Hasta entonces esta
# constante apunta a un subdominio que aun no resuelve, asi que todos los envios
# fallan en el DNS, se tragan y no sale ningun dato del equipo.
#
# Es un subdominio de un dominio que el proyecto YA controla, y eso es
# deliberado y no cosmetico: si aqui hubiera un dominio sin registrar, cualquiera
# podria registrarlo y empezar a recibir la telemetria real de los usuarios que
# actualicen. Un marcador de posicion en `example.com` o en un dominio inventado
# seria un fallo de seguridad, no un TODO. Quien despliegue `quilate-infra` cambia
# esta linea por la URL real; el resto del programa no necesita tocarse.
#
# Un unico destino, documentado, para que se pueda bloquear en el cortafuegos:
# es una de las tres vias de oposicion que promete PRIVACY.md.
TELEMETRIA_URL = "https://telemetria.cristianac.es/v1/run"

# Version del propio formato del payload. Sube si cambia la forma de lo que se
# manda, para que el servidor pueda distinguir esquemas viejos sin adivinar.
TELEMETRIA_ESQUEMA = 1

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MAC = platform.system() == "Darwin"

CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
