"""Lo que Windows contesta, traducido a lo que significa.

Doce tablas y un decodificador. Están juntas y aparte de las comprobaciones por
un motivo que se repite en todas: son la frontera entre un valor que decide
Microsoft —un entero sin documentar, una cadena localizada, un código de
error— y el juicio que Quilate emite a partir de él.

Esa frontera se cruza por tabla y no por comparación a propósito. Un valor
nuevo que Windows empiece a devolver algún día cae por su propio peso en «no lo
reconozco», que es lo que se quiere: entre callarse y acusar en falso, aquí se
prefiere callarse siempre. Ver `_estado_antivirus`, `check_filesystem_health` y
`_estado_cifrado`, que aplican los tres el mismo criterio.

Solo stdlib. Nada de aquí sabe que existe un `Auditor`.
"""

from __future__ import annotations

import re
from datetime import date


def _clave(valor):
    """Un valor de Windows listo para buscarlo en una de estas tablas.

    El texto se normaliza —PowerShell devuelve «Enabled», «enabled» y a veces
    con espacios—; lo que no es texto se deja tal cual, porque los enteros de
    las enumeraciones se buscan por su valor.
    """
    return valor.strip().lower() if isinstance(valor, str) else valor


# --- Antivirus ----------------------------------------------------------------
# `productState` es un entero de 32 bits que el Centro de seguridad de Windows
# no documenta en ninguna parte oficial, pero cuyo reparto es estable desde
# Vista: 0xAABBCC, donde BB dice si el motor está vigilando (0x10 u 0x11) y CC
# si las firmas están al día (0x00) o caducadas (0x10). AA es el tipo de
# producto y aquí no interesa.
#
# Precisamente por no estar documentado, un valor que no encaje en ninguno de
# los dos juegos conocidos no se interpreta: se calla.
_AV_MOTOR = {0x00: False, 0x01: False, 0x10: True, 0x11: True}   # {byte: vigilando}
_AV_FIRMAS = {0x00: True, 0x10: False}                           # {byte: al día}


def _estado_antivirus(state) -> tuple[bool, bool] | None:
    """(vigilando, firmas al día), o None si el entero no se reconoce.

    Los dos bytes se traducen por tabla y no por comparación: así, un valor
    nuevo que Microsoft empiece a devolver algún día cae por su propio peso en
    «no lo reconozco» en vez de colarse como uno de los conocidos.
    """
    if isinstance(state, bool) or not isinstance(state, int):
        return None
    motor, firmas = (state >> 8) & 0xFF, state & 0xFF
    if motor not in _AV_MOTOR or firmas not in _AV_FIRMAS:
        return None
    return _AV_MOTOR[motor], _AV_FIRMAS[firmas]


# --- Dispositivos -------------------------------------------------------------
# El «código de problema» del Administrador de dispositivos: el número que hay
# detrás del signo de exclamación amarillo, que Windows enseña en una ventana que
# nadie abre. `ConfigManagerErrorCode` lo expone tal cual, y estos son los que
# significan que el dispositivo no está haciendo su trabajo.
_PNP_PROBLEMA = {
    1: "Windows no tiene su configuración",
    3: "su driver está dañado, o falta memoria",
    10: "no puede arrancar",
    12: "no hay recursos libres suficientes para él",
    14: "necesita que reinicies para terminar de configurarse",
    18: "hay que reinstalar sus drivers",
    19: "su configuración en el registro está dañada",
    21: "Windows lo está quitando",
    24: "no está presente, o no funciona bien",
    28: "no tiene drivers instalados",
    31: "Windows no puede cargar los drivers que necesita",
    35: "la BIOS no le ha reservado recursos",
    37: "su driver ha fallado al inicializarlo",
    39: "su driver falta o está dañado",
    43: "Windows lo ha parado porque el propio dispositivo avisó de un fallo",
    48: "su software está bloqueado por incompatible",
    52: "no se puede verificar la firma de su driver",
}

# Estos códigos también son distintos de cero y ninguno es una avería: los tres
# primeros los provoca quien usa el equipo y el último es un estado de paso. Van
# aparte para no acusar de estropeado a lo que alguien apagó a propósito.
_PNP_DELIBERADO = frozenset({22, 32, 45, 47})

# Cinco años. Por debajo hay demasiado driver que sencillamente está terminado y
# no necesita más versiones: un lector de tarjetas de 2022 funciona igual hoy.
_DRIVER_VIEJO_DIAS = round(5 * 365.25)


# --- Estado del sistema de ficheros -------------------------------------------
# `fsutil dirty query` contesta en el idioma de Windows, y la frase afirmativa y
# la negativa se diferencian en una sola palabra. Buscar «dirty» a secas daría
# por sucio un volumen limpio en cuanto la respuesta sea «is NOT dirty».
_NEGACIONES = {"not", "no", "nicht", "non", "pas", "niet", "nao", "não"}
_SUCIO = ("dirty", "sucio", "sujo", "verschmutzt", "sporco", "vuil")


# --- BitLocker ----------------------------------------------------------------
# `Get-BitLockerVolume` devuelve el estado unas veces como entero y otras como
# nombre, según la versión de Windows y de PowerShell. Se aceptan las dos formas,
# y lo que no encaje en ninguna se trata como desconocido y no como «sin cifrar»:
# decirle a alguien que su disco está desprotegido cuando sí lo está es la clase
# de aviso que hace que se deje de leer el informe entero.
_CIFRADO_SI = {1, "1", "on", "fullyencrypted"}
_CIFRADO_NO = {0, "0", "off", "fullydecrypted"}


# --- Cortafuegos --------------------------------------------------------------
# `Get-NetFirewallProfile` devuelve `Enabled` como la enumeración GpoBoolean,
# que `ConvertTo-Json` serializa unas veces como entero y otras por nombre.
# Mismo criterio que en BitLocker: lo que no encaje no se interpreta.
_FIREWALL_ACTIVO = {1: True, "1": True, "true": True, "enabled": True,
                    0: False, "0": False, "false": False, "notconfigured": False}

# Los perfiles donde no hay nada más entre el equipo e internet. Dominio queda
# fuera: aplica en redes de empresa, donde suele haber politica central y un
# cortafuegos perimetral delante.
_PERFILES_EXPUESTOS = frozenset({"public", "private", "público", "publico", "privado"})


# --- Escritorio remoto --------------------------------------------------------
# Dos valores del registro y ningún privilegio. `fDenyTSConnections` vale 0
# cuando RDP acepta conexiones —la doble negación es de Microsoft, no nuestra— y
# `UserAuthentication` vale 1 cuando se exige NLA.
_RDP_CLAVE = r"SYSTEM\CurrentControlSet\Control\Terminal Server"
_RDP_TCP_CLAVE = _RDP_CLAVE + r"\WinStations\RDP-Tcp"


# --- SMB1 ---------------------------------------------------------------------
_SMB1_ACTIVO = {1, "enabled"}
_SMB1_INACTIVO = {2, "disabled", "disabledwithpayloadremoved"}


# --- Soporte de Windows -------------------------------------------------------
# Hasta cuándo publica Microsoft parches para cada versión. Esta tabla se
# escribe a mano y caduca sola: por eso lleva su propia fecha de revisión y por
# eso `check_windows_soportado` se calla cuando lleva demasiado sin tocarse.
# Decirle a alguien «tu Windows ya no recibe parches» con una tabla vieja es
# peor que no decirle nada, porque es un aviso que se actúa.
_SOPORTE_REVISADO = "2026-05"
_SOPORTE_CADUCA_MESES = 18

# build -> (fin de soporte en Home/Pro, fin en Enterprise/Education)
# Las ediciones de empresa reciben unos dos años más por la misma build, así
# que usar una sola fecha marcaría como caducado un equipo que sí tiene parches.
_SOPORTE_WINDOWS = {
    19044: ("2023-06-13", "2024-06-11"),   # Windows 10 21H2
    19045: ("2025-10-14", "2025-10-14"),   # Windows 10 22H2 · fin de Windows 10
    22000: ("2023-10-10", "2024-10-08"),   # Windows 11 21H2
    22621: ("2024-10-08", "2025-10-14"),   # Windows 11 22H2
    22631: ("2025-11-11", "2026-11-10"),   # Windows 11 23H2
    26100: ("2026-10-13", "2027-10-12"),   # Windows 11 24H2
}

# Por debajo de la build más antigua de la tabla no hace falta tabla: Windows 8.1
# dejó de recibir parches en enero de 2023 y Windows 7 en enero de 2020. Esto no
# caduca, solo puede volverse más cierto con el tiempo.
_SOPORTE_SUELO = min(_SOPORTE_WINDOWS)

# Las que reciben el ciclo largo. Se busca en el nombre que publica Windows
# (`Caption`), que trae la edición: «Microsoft Windows 11 Enterprise».
_EDICIONES_LARGAS = ("enterprise", "education", "iot")


def _build_de(os_build: str) -> int | None:
    """El número de build dentro de lo que publica el inventario.

    Llega como «10.0.26200 (build 26200)» en Windows y como cualquier otra cosa
    fuera de él, así que se busca el número que va detrás de «build» y, si no
    está, el tercer componente de la versión.
    """
    texto = str(os_build or "")
    marcado = re.search(r"build\s+(\d+)", texto, re.I)
    if marcado:
        return int(marcado.group(1))
    partes = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", texto)
    return int(partes.group(3)) if partes else None


def _tabla_de_soporte_caducada(hoy: date | None = None) -> bool:
    """Si la tabla de fin de soporte lleva demasiado sin revisarse.

    Misma idea que `reference_is_stale` para la escala del benchmark, pero
    contra la fecha de ESTA tabla: son dos cosas que se revisan por separado y
    atarlas a la misma fecha haría que una caducase por culpa de la otra.
    """
    hoy = hoy or date.today()
    año, mes = (int(x) for x in _SOPORTE_REVISADO.split("-"))
    return (hoy.year - año) * 12 + (hoy.month - mes) >= _SOPORTE_CADUCA_MESES


# --- Boletines de seguridad ---------------------------------------------------
_MSRC_GRAVES = ("critical",)
_MSRC_SERIAS = ("important",)
