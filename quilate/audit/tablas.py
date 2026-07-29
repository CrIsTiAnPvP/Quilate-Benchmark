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


# --- SMB1 ---------------------------------------------------------------------
_SMB1_ACTIVO = {1, "enabled"}
_SMB1_INACTIVO = {2, "disabled", "disabledwithpayloadremoved"}


# --- Boletines de seguridad ---------------------------------------------------
_MSRC_GRAVES = ("critical",)
_MSRC_SERIAS = ("important",)
