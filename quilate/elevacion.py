"""Lo poco que necesita permisos, en un proceso elevado que dura dos segundos.

Quilate pedía UAC al arrancar y a partir de ahí ejecutaba TODO como
administrador: el banco de pruebas, el rastreo de archivos, la escritura del
informe y la del histórico. De las comprobaciones que hace, las que de verdad
necesitan permisos son seis, más dos consultas del inventario. Elevar el resto
no aportaba nada y traía cosas malas: los informes salían con propietario
Administrador, y el histórico dependía de un `LOCALAPPDATA` que el proceso sin
elevar podía haber dejado apuntando a cualquier sitio.

Aquí el proceso no se eleva nunca. Se lanza un PowerShell elevado que ejecuta
un lote fijo de consultas de lectura, devuelve un JSON y muere.

El resultado no puede volver por la salida estándar. `ShellExecuteEx` con el
verbo «runas» —el único camino que Windows soporta para elevar— devuelve el
handle del proceso pero no admite `STARTUPINFO`, así que no hay tubería que
heredar. La alternativa habitual es que el hijo escriba en un fichero temporal,
y no se ha hecho así: sería una escritura elevada a una ruta que el usuario
controla, que es la forma clásica de una escalada de privilegios por enlace
simbólico. En su lugar el padre, sin privilegios, monta una tubería con nombre y
el hijo elevado se conecta a ella: un proceso de integridad alta puede abrir un
objeto creado por uno de integridad media, y así no se toca el disco.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from .const import IS_WINDOWS
from .platform_utils import (PSResult, _ps_raw, _sys_exe, guion_de_bloques, is_admin,
                             trocear)

SIN_PERMISOS = "no se han concedido permisos de administrador"
NO_PEDIDOS = "no se pidieron permisos de administrador"

# Todo lo que Quilate ejecuta con privilegios está aquí y en ningún otro sitio.
# Tenerlo en una sola lista es la única forma de que alguien pueda leer de una
# sentada qué hace este programa cuando le das permisos de administrador, que en
# una herramienta que precisamente audita seguridad no es un detalle.
#
# Las once son de lectura: ninguna cambia nada del sistema.
_CONSULTAS_ELEVADAS: dict[str, str] = {
    # --- para el inventario (sysinfo._map_storage) ---
    "reliability": "Get-PhysicalDisk | ForEach-Object { $d = $_; "
                   "$c = $d | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; "
                   "if ($c) { [PSCustomObject]@{ DeviceId = $d.DeviceId; Wear = $c.Wear; "
                   "Temperature = $c.Temperature; PowerOnHours = $c.PowerOnHours; "
                   "ReadErrorsUncorrected = $c.ReadErrorsUncorrected; "
                   "WriteErrorsUncorrected = $c.WriteErrorsUncorrected } } }",
    # Solo lo publican los ATA/SATA: el driver NVMe no expone esta clase y los
    # puentes USB no dejan pasar el comando. Que falte no es un fallo.
    "smart": "Get-CimInstance -Namespace root\\wmi "
             "-ClassName MSStorageDriver_FailurePredictData | "
             "Select-Object InstanceName,VendorSpecific",

    # --- para la auditoría (audit.Auditor) ---
    # La disponibilidad se resuelve con `Get-Command` y no leyendo el texto del
    # error: en Windows Home el cmdlet no existe y el mensaje viene traducido.
    "bitlocker": "$( if (-not (Get-Command Get-BitLockerVolume -ErrorAction SilentlyContinue)) {"
                 "     [PSCustomObject]@{ disponible = $false } } else {"
                 "     Get-BitLockerVolume | Select-Object @{n='disponible';e={$true}},"
                 "       MountPoint,VolumeStatus,ProtectionStatus } )",
    "secureboot": "$( [PSCustomObject]@{ firmware = $env:firmware_type;"
                  "   activo = $( try { Confirm-SecureBootUEFI } catch { $null } ) } )",
    "tpm": "$( if (-not (Get-Command Get-Tpm -ErrorAction SilentlyContinue)) {"
           "     [PSCustomObject]@{ disponible = $false } } else {"
           "     Get-Tpm | Select-Object @{n='disponible';e={$true}},"
           "       TpmPresent,TpmReady,TpmEnabled } )",
    "smb1": "$( if (-not (Get-Command Get-WindowsOptionalFeature -ErrorAction SilentlyContinue)) {"
            "     [PSCustomObject]@{ disponible = $false } } else {"
            "     Get-WindowsOptionalFeature -Online -FeatureName SMB1Protocol |"
            "       Select-Object @{n='disponible';e={$true}},State } )",
    # Clave hermana de la anterior: mismo cmdlet, otra caracteristica. Va en el
    # lote y no aparte precisamente por eso — el lote entero se pregunta en un
    # solo proceso, asi que esto no anade ni un aviso de UAC ni un arranque de
    # PowerShell mas. Preguntar por ella suelta habria costado las dos cosas.
    "powershell2": "$( if (-not (Get-Command Get-WindowsOptionalFeature -ErrorAction SilentlyContinue)) {"
                   "     [PSCustomObject]@{ disponible = $false } } else {"
                   "     Get-WindowsOptionalFeature -Online "
                   "       -FeatureName MicrosoftWindowsPowerShellV2Root |"
                   "       Select-Object @{n='disponible';e={$true}},State } )",
    # El log de arranque tiene una ACL propia que pide permisos hasta para leer.
    # Los eventos de duración (100) y los de culpables (101-103) se piden por
    # separado: con muchos retrasos, un único -MaxEvents sobre los cuatro
    # identificadores puede no llegar a devolver ni un solo arranque.
    "arranque": "$leer = { param($ids, $max) "
                "  Get-WinEvent -FilterHashtable @{"
                "      LogName='Microsoft-Windows-Diagnostics-Performance/Operational';"
                "      Id=$ids} -MaxEvents $max -ErrorAction Stop | ForEach-Object {"
                "    $d = @{};"
                "    foreach ($n in ([xml]$_.ToXml()).Event.EventData.Data) { $d[$n.Name] = $n.'#text' };"
                "    [PSCustomObject]@{ Id = $_.Id; Time = $_.TimeCreated.ToString('s'); Data = $d } } };"
                "$arranques = @(& $leer @(100) 30);"
                # Que no haya retrasos anotados es un resultado válido, no un fallo.
                "$retrasos = @(); try { $retrasos = @(& $leer @(101,102,103) 200) } catch { };"
                "@($arranques + $retrasos)",
    # La única que no es PowerShell. La ruta sale de `[Environment]::SystemDirectory`
    # y no de `$env:SystemRoot` por lo mismo que `_sys_exe` usa GetSystemDirectoryW:
    # aquí se está eligiendo qué binario corre elevado. `dirty query` solo lee;
    # quien lo cambie por `dirty set` estará marcando el volumen a mano.
    # Aquí no se toca `[Console]::OutputEncoding`, y no es un olvido. En 1.9 hizo
    # falta elegir la página OEM a mano porque `run_cmd` lee los bytes crudos del
    # proceso y tiene que decidir con qué códec descifrarlos. Aquí decodifica
    # PowerShell, que ya sabe en qué página escribe su consola. Comprobado
    # ejecutándolo: la ayuda de `fsutil` vuelve con sus 25 líneas acentuadas
    # intactas con y sin la línea. Y ponerla sería peor, no mejor: en una consola
    # puesta en UTF-8 con `chcp 65001` forzaría cp850 y rompería justo lo que
    # pretendía arreglar.
    "fsdirty": "$sys = [Environment]::SystemDirectory;"
               "$u = $sys.Substring(0, 2);"
               "$t = & \"$sys\\fsutil.exe\" dirty query $u;"
               "[PSCustomObject]@{ unidad = $u;"
               "  salida = [string]::Join([char]10, @($t)); codigo = $LASTEXITCODE }",
}

_recogido: dict[str, PSResult] | None = None
_pedir = False


def permitir_uac(activo: bool = True) -> None:
    """Autoriza a enseñar el aviso de UAC.

    Está apagado por defecto a propósito: importar Quilate como biblioteca, o
    correr sus tests, no puede sacarle a nadie un diálogo de Windows por
    sorpresa. Solo la interfaz de órdenes lo enciende, y solo cuando hay alguien
    delante que pueda contestar.
    """
    global _pedir
    _pedir = activo


def olvidar() -> None:
    """Tira lo recogido. Existe para los tests, que necesitan empezar limpios."""
    global _recogido
    _recogido = None


def recoger(latido=None) -> dict[str, PSResult]:
    """El lote entero, preguntado una sola vez por ejecución.

    Una sola vez porque cada llamada sería otro aviso de UAC, y encadenar tres
    diálogos para una auditoría es la forma más rápida de enseñarle a alguien a
    darle a «Sí» sin leer.
    """
    global _recogido
    if _recogido is not None:
        return _recogido
    if not _CONSULTAS_ELEVADAS or not IS_WINDOWS:
        _recogido = trocear(None, _CONSULTAS_ELEVADAS, "solo Windows")
    elif is_admin():
        # Ya estamos elevados: montar un proceso aparte y un aviso para leer lo
        # que este mismo puede leer no tendría ningún sentido.
        datos, error = _ps_raw(
            guion_de_bloques(_CONSULTAS_ELEVADAS)
            + "$r | ConvertTo-Json -Depth 8 -Compress", timeout=60)
        _recogido = trocear(datos, _CONSULTAS_ELEVADAS,
                            error or "el lote con permisos no ha devuelto nada legible")
    elif _pedir:
        # `lote_propio` y no `consulta_elevada`: el que sale en el aviso de UAC es
        # el programa que se eleva, y aqui el que se eleva es Quilate. Ver la
        # explicacion en esa funcion.
        _recogido = lote_propio(latido=latido)
    else:
        _recogido = trocear(None, _CONSULTAS_ELEVADAS, NO_PEDIDOS)
    return _recogido

if IS_WINDOWS:
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p),
                    ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD),
                    ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    from .platform_utils import _ShellExecuteInfo

    # Con `use_last_error` el código de error queda guardado por ctypes antes de
    # que nada más pueda pisarlo, que es la única forma de distinguir «la lectura
    # está en marcha» de «el hijo ha cerrado la tubería».
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PTR = ctypes.POINTER(wintypes.DWORD)
    for _funcion, _args, _res in (
            ("CreateNamedPipeW", [wintypes.LPCWSTR] + [wintypes.DWORD] * 6
             + [ctypes.c_void_p], wintypes.HANDLE),
            # Las dos siguientes las usa el lado que CONTESTA: el proceso elevado
            # que abre la tuberia como cliente y escribe el JSON. No se usa el
            # `open()` de Python porque en modo "wb" pide O_CREAT, y crear un
            # fichero no es lo que se quiere hacer sobre una tuberia que ya
            # existe: hay que abrirla con OPEN_EXISTING y nada mas.
            ("CreateFileW", [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                             wintypes.HANDLE], wintypes.HANDLE),
            ("WriteFile", [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                           _PTR, ctypes.c_void_p], wintypes.BOOL),
            ("CreateEventW", [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                              wintypes.LPCWSTR], wintypes.HANDLE),
            ("ConnectNamedPipe", [wintypes.HANDLE, ctypes.c_void_p], wintypes.BOOL),
            ("ReadFile", [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, _PTR,
                          ctypes.c_void_p], wintypes.BOOL),
            ("GetOverlappedResult", [wintypes.HANDLE, ctypes.c_void_p, _PTR,
                                     wintypes.BOOL], wintypes.BOOL),
            ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ("ResetEvent", [wintypes.HANDLE], wintypes.BOOL),
            ("CancelIo", [wintypes.HANDLE], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL)):
        getattr(_k32, _funcion).argtypes = _args
        getattr(_k32, _funcion).restype = _res

_PIPE_ACCESS_INBOUND = 0x00000001
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_FILE_FLAG_OVERLAPPED = 0x40000000
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_INVALID_HANDLE = ctypes.c_void_p(-1).value

_ERROR_IO_PENDING = 997
_ERROR_PIPE_CONNECTED = 535
_ERROR_BROKEN_PIPE = 109
_ERROR_HANDLE_EOF = 38
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102

# Cada cuánto se sale de la espera para dar señales de vida. Cinco veces por
# segundo: suficiente para que un giro se vea fluido y lo bastante poco como para
# que el coste sea invisible al lado de lo que tarda el proceso con permisos.
_LATIDO = 0.2

_SEE_MASK_NOASYNC = 0x00000100
_SW_HIDE = 0
_BUFFER = 1 << 16

# Para abrir la tuberia desde el lado que contesta. `OPEN_EXISTING` y no
# `CREATE_ALWAYS`: la tuberia la crea el padre y aqui solo se abre.
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3


def _nombre_de_tuberia() -> str:
    """Un nombre que nadie pueda adivinar para adelantarse a crearlo.

    Con `FILE_FLAG_FIRST_PIPE_INSTANCE` un nombre ya ocupado hace fallar la
    creación en vez de compartir la tubería con quien llegó antes, así que
    adivinar el nombre es la única vía y son 128 bits.
    """
    return f"quilate-{os.getpid()}-{secrets.token_hex(16)}"


def _guion(consultas: dict[str, str], tuberia: str) -> str:
    """El lote de consultas, más el envío del resultado por la tubería.

    Las consultas son constantes del código fuente y nunca traen nada que venga
    de la línea de órdenes: lo que se ejecuta con privilegios no puede depender
    de lo que a nadie le apetezca escribir.
    """
    return (
        guion_de_bloques(consultas)
        + "$t = New-Object System.IO.Pipes.NamedPipeClientStream("
        f"  '.', '{tuberia}', [System.IO.Pipes.PipeDirection]::Out);"
        "$t.Connect(20000);"
        "$w = New-Object System.IO.StreamWriter($t, (New-Object Text.UTF8Encoding $false));"
        "$w.Write(($r | ConvertTo-Json -Depth 8 -Compress));"
        "$w.Flush(); $w.Dispose(); $t.Dispose()")


def _lanzar_elevado(exe: str, parametros: str) -> bool:
    """Pide UAC y lanza el proceso. False si no se han concedido los permisos.

    No se espera al hijo: quien manda es la tubería, que se cierra sola cuando
    el hijo termina de escribir o cuando muere sin escribir.
    """
    try:
        info = _ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = _SEE_MASK_NOASYNC
        info.lpVerb = "runas"
        info.lpFile = exe
        info.lpParameters = parametros
        info.lpDirectory = None
        info.nShow = _SW_HIDE
        return bool(ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)))
    except Exception:
        return False


def _escuchar(handle, evento, plazo: float, latido=None) -> bytes | None:
    """Espera al hijo y devuelve lo que escriba, o None si no llega a tiempo.

    Todo va con E/S solapada porque si no la espera no tiene fin: con una
    tubería bloqueante, un UAC rechazado o un PowerShell que muere antes de
    conectarse dejarían a Quilate esperando para siempre a alguien que ya no
    va a venir.

    `latido`, si se pasa, se llama cada pocas décimas mientras se espera. Sirve
    para que quien llama pueda mover un indicador: esta espera dura lo que tarde
    el proceso con permisos, y sin señales de vida no se distingue de un cuelgue.
    """
    ov = _Overlapped()
    ov.hEvent = evento
    buf = ctypes.create_string_buffer(_BUFFER)
    leidos = wintypes.DWORD()

    # Si hay o no una operación de verdad en marcha. Lo consulta el `finally`, y
    # llevar la cuenta no es una precaución de más: ver el comentario de allí.
    pendiente = False

    def esperar() -> bool:
        """Espera a que el hijo dé señales, troceando para poder latir.

        La espera se parte en trozos de `_LATIDO` en vez de pedir el plazo entero
        de una vez. No cambia cuánto se espera —el corte sigue siendo `plazo`—
        pero deja un hueco cada pocas décimas para avisar de que esto sigue vivo.
        """
        while True:
            restante = plazo - time.monotonic()
            if restante <= 0:
                return False
            trozo = int(min(restante, _LATIDO) * 1000)
            resultado = _k32.WaitForSingleObject(evento, trozo)
            if resultado == _WAIT_OBJECT_0:
                return True
            if resultado != _WAIT_TIMEOUT:
                # Ni señalado ni agotado: el handle no vale. Insistir hasta
                # agotar el plazo sería quemar un minuto girando en vano.
                return False
            if latido is not None:
                try:
                    latido()
                except Exception:
                    # Un indicador es un adorno. Si el sitio donde se pinta ya no
                    # existe, se sigue esperando igual: lo que importa es el lote.
                    pass

    try:
        _k32.ResetEvent(evento)
        if not _k32.ConnectNamedPipe(handle, ctypes.byref(ov)):
            error = ctypes.get_last_error()
            if error == _ERROR_IO_PENDING:
                pendiente = True
                if not esperar():
                    return None
                pendiente = False
            elif error != _ERROR_PIPE_CONNECTED:
                return None

        trozos = []
        while True:
            _k32.ResetEvent(evento)
            if not _k32.ReadFile(handle, buf, _BUFFER, ctypes.byref(leidos),
                                 ctypes.byref(ov)):
                error = ctypes.get_last_error()
                if error == _ERROR_IO_PENDING:
                    pendiente = True
                    if not esperar():
                        return None
                    pendiente = False
                    if not _k32.GetOverlappedResult(handle, ctypes.byref(ov),
                                                    ctypes.byref(leidos), False):
                        break      # el hijo ha cerrado: se acabó de leer
                elif error in (_ERROR_BROKEN_PIPE, _ERROR_HANDLE_EOF):
                    break
                else:
                    return None
            if not leidos.value:
                break
            trozos.append(buf.raw[:leidos.value])
        return b"".join(trozos)
    finally:
        # Salir de aquí con una lectura todavía en marcha deja al kernel
        # escribiendo en la estructura y en el buffer, que son locales y dejan
        # de existir. No se nota al momento: revienta más tarde, cuando otra
        # cosa cualquiera ocupa esa memoria. Hay que cancelar y esperar a que el
        # kernel confirme que ha soltado las dos.
        #
        # Pero SOLO si queda algo en marcha, y el `if` es el arreglo de un cuelgue
        # de verdad. `GetOverlappedResult` con `bWait=True` sobre un OVERLAPPED
        # que ya no tiene nada pendiente se queda esperando en `hEvent`, y ese
        # evento está sin señalar porque el bucle hace `ResetEvent` al principio
        # de cada vuelta. Es decir: en el camino normal —el hijo escribe, cierra,
        # y la siguiente lectura sale por ERROR_BROKEN_PIPE sin llegar a quedar
        # pendiente— se entraba aquí a esperar para siempre a algo que ya había
        # pasado. Con el cliente de PowerShell no se notaba porque salía por la
        # otra rama; con el ayudante sí, y Quilate se quedaba colgado justo
        # después de que el usuario concediera los permisos.
        if pendiente:
            _k32.CancelIo(handle)
            _k32.GetOverlappedResult(handle, ctypes.byref(ov),
                                     ctypes.byref(leidos), True)


def _con_tuberia(arrancar, consultas: dict[str, str], timeout: int,
                 latido=None) -> dict[str, PSResult]:
    """Monta el canal, llama a `arrancar(nombre)` y trocea lo que llegue.

    Todo lo de aquí es igual para los dos caminos —el que eleva un PowerShell y el
    que eleva a Quilate— y lo único que cambia entre ellos es qué proceso se
    lanza. Por eso `arrancar` es un parámetro: la tubería, la espera, el JSON y el
    troceado se escriben una vez.

    Devuelve un `PSResult` por consulta, igual que el inventario. Que el usuario
    diga que no al aviso de UAC es un camino normal y no un fallo: las claves
    salen con `ok=False` y un motivo que se puede enseñar tal cual, para que el
    informe diga «no se comprobó porque no se dieron permisos» en vez de dar por
    bueno lo que nadie ha llegado a mirar.
    """
    nombre = _nombre_de_tuberia()
    handle = _k32.CreateNamedPipeW(
        f"\\\\.\\pipe\\{nombre}",
        _PIPE_ACCESS_INBOUND | _FILE_FLAG_OVERLAPPED | _FILE_FLAG_FIRST_PIPE_INSTANCE,
        _PIPE_TYPE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
        1, 0, _BUFFER, 0, None)
    if not handle or handle == _INVALID_HANDLE:
        return trocear(None, consultas, "no se ha podido abrir el canal de respuesta")
    evento = _k32.CreateEventW(None, True, False, None)
    try:
        if not arrancar(nombre):
            return trocear(None, consultas, SIN_PERMISOS)
        crudo = _escuchar(handle, evento, time.monotonic() + timeout, latido)
    finally:
        _k32.CloseHandle(handle)
        if evento:
            _k32.CloseHandle(evento)

    if not crudo:
        return trocear(None, consultas,
                       "el proceso con permisos no ha contestado a tiempo")
    return trocear(_json(crudo), consultas,
                   "el proceso con permisos no ha devuelto nada legible")


def consulta_elevada(consultas: dict[str, str], timeout: int = 60,
                     latido=None) -> dict[str, PSResult]:
    """Ejecuta un lote CUALQUIERA en un PowerShell elevado.

    Es la vía general, y ya no es la que usa `recoger`: el aviso de UAC de este
    camino lo firma «Windows PowerShell», porque el programa que se eleva es
    PowerShell. Para lo que ve el usuario está `lote_propio`.

    Se conserva porque sigue siendo la única forma de ejecutar un lote arbitrario
    con permisos, y es lo que permite probar la tubería, el JSON y el troceado con
    consultas de mentira, sin depender del lote real ni de que haya un .exe
    compilado.
    """
    if not IS_WINDOWS:
        return trocear(None, consultas, "solo Windows")
    if not consultas:
        return {}

    def arrancar(nombre: str) -> bool:
        guion = _guion(consultas, nombre)
        codificado = base64.b64encode(guion.encode("utf-16-le")).decode("ascii")
        # Aqui no van `-ExecutionPolicy Bypass` ni `-WindowStyle Hidden`, y las
        # dos ausencias son deliberadas.
        #
        # `-ExecutionPolicy Bypass` sobraba. La politica de ejecucion gobierna
        # los *ficheros* de guion: un .ps1 que se carga del disco. Lo que se pasa
        # aqui es `-EncodedCommand`, que no es un fichero y al que la politica no
        # se aplica en ningun caso. Es decir, la opcion no estaba habilitando
        # nada —el lote se ejecuta igual sin ella, con la politica que sea— y a
        # cambio anadia a la linea de ordenes del proceso elevado una de las tres
        # palabras que buscan todas las reglas de deteccion de PowerShell
        # abusivo. Pagar ese precio por nada no tiene sentido.
        #
        # `-WindowStyle Hidden` era redundante: la ventana ya se crea oculta con
        # `nShow = SW_HIDE` en ShellExecuteEx, que es el mecanismo que de verdad
        # decide, y se aplica al crear el proceso. La opcion de PowerShell actua
        # despues de arrancar, asi que ni siquiera evitaba el parpadeo que
        # pretendia evitar. Quedaba solo el aspecto: "powershell oculto" escrito
        # en la linea de ordenes.
        #
        # `-EncodedCommand` si se queda. No es ofuscacion: es UTF-16LE en base64,
        # que es el mecanismo documentado para pasar un guion sin que el
        # entrecomillado lo destroce por el camino. La alternativa —meter el lote
        # entero, con sus comillas simples y dobles, en `-Command` a traves de
        # `lpParameters`— se romperia en silencio a la primera consulta que
        # alguien edite, y romperse en silencio aqui significa un informe que
        # dice "no se comprobo" cuando en realidad se rompio el entrecomillado.
        # La otra alternativa, escribir el guion a un fichero para pasarlo con
        # `-File`, es la elevacion de privilegios por ruta que el docstring de
        # este modulo explica que no se va a hacer.
        return _lanzar_elevado(
            _sys_exe("powershell.exe"),
            f"-NoProfile -NonInteractive -EncodedCommand {codificado}")

    return _con_tuberia(arrancar, consultas, timeout, latido)


def lote_propio(timeout: int = 60, latido=None) -> dict[str, PSResult]:
    """El lote fijo, ejecutado por Quilate elevándose a sí mismo.

    --- Por qué existe ---

    El aviso de UAC no dice quién *pide* la elevación: dice quién la *recibe*.
    Windows le pone al diálogo el icono, la descripción y el editor del ejecutable
    que va a arrancar con permisos, leídos de su VERSIONINFO y de su firma. Al
    elevar `powershell.exe`, lo que el usuario leía era «Windows PowerShell,
    editor comprobado: Microsoft Windows», sin una sola mención a Quilate. En una
    herramienta que pide administrador eso es justo lo contrario de lo que hace
    falta: quien concede los permisos tiene que reconocer a quién se los da.

    Aquí el que se eleva es `Quilate.exe`, así que el aviso lleva su nombre, su
    logo y —en cuanto haya certificado— su editor.

    --- Lo que se gana además ---

    Desaparece el `-EncodedCommand` de la línea de órdenes del proceso elevado.
    Antes, lo que pasaba por UAC era un PowerShell con un base64 de miles de
    caracteres detrás, que es exactamente la forma de un ataque por PowerShell;
    ahora es `Quilate.exe --lote-elevado quilate-1234-<32 hex>`, que se lee.

    Y se estrecha lo que el hijo puede hacer. Antes el guion viajaba desde el
    padre, así que el proceso elevado ejecutaba lo que le mandaran; ahora las
    consultas son las once constantes de este módulo, compiladas dentro del
    binario, y por la línea de órdenes solo entra el nombre de la tubería, que se
    valida antes de tocarlo. Lo que Quilate hace con permisos ya no depende de
    nada que venga de fuera.

    --- Lo que cuesta ---

    Un proceso más: el Quilate elevado lanza a su vez el PowerShell que hace las
    consultas. Ese ya no pasa por UAC ni lo ve nadie, y es hijo de un proceso que
    ya está elevado, así que no hereda ningún aviso.
    """
    if not IS_WINDOWS:
        return trocear(None, _CONSULTAS_ELEVADAS, "solo Windows")

    def arrancar(nombre: str) -> bool:
        exe, parametros = _ayudante(nombre)
        return _lanzar_elevado(exe, parametros)

    return _con_tuberia(arrancar, _CONSULTAS_ELEVADAS, timeout, latido)


# La marca que distingue «ejecútate normal» de «eres el ayudante elevado». Está
# aquí y no en `cli` porque quien la escribe y quien la lee son las dos funciones
# de este módulo; `cli` solo la reenvía.
MARCA_AYUDANTE = "--lote-elevado"

# El nombre de la tubería es lo ÚNICO que entra al proceso elevado desde fuera, y
# por tanto lo único que hay que validar. El formato lo pone `_nombre_de_tuberia`:
# la palabra, el pid y 32 dígitos hexadecimales.
_TUBERIA_VALIDA = re.compile(r"quilate-\d{1,10}-[0-9a-f]{32}\Z")


def _ayudante(tuberia: str) -> tuple[str, str]:
    """Qué ejecutar para que el ayudante elevado sea Quilate.

    Empaquetado es directo: el propio `.exe`. Sin empaquetar hay que pasar por el
    intérprete y darle el lanzador, y entonces el aviso dice «Python», que es la
    verdad —el programa que se eleva es Python—. No se arregla y no hace falta: el
    `.exe` es lo que usa la gente, y que el camino sea el mismo en los dos casos es
    lo que permite probarlo sin compilar.

    El lanzador va entre comillas porque su ruta puede llevar espacios y esto acaba
    en una sola cadena de parámetros para `ShellExecuteEx`.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, f"{MARCA_AYUDANTE} {tuberia}"
    lanzador = Path(__file__).resolve().parent.parent / "quilate.py"
    return sys.executable, f'"{lanzador}" {MARCA_AYUDANTE} {tuberia}'


def _enviar(tuberia: str, carga: bytes, plazo: float = 20.0) -> bool:
    """Abre la tubería como cliente y escribe: el lado elevado de la conversación.

    Se reintenta porque el padre puede no haber llegado aún a `ConnectNamedPipe`:
    entre aceptar el UAC y arrancar el hijo hay un servicio de Windows por medio y
    el orden no está garantizado. Veinte segundos es el mismo plazo que usaba el
    cliente de PowerShell al que esto sustituye.
    """
    ruta = f"\\\\.\\pipe\\{tuberia}"
    limite = time.monotonic() + plazo
    while True:
        handle = _k32.CreateFileW(ruta, _GENERIC_WRITE, 0, None,
                                  _OPEN_EXISTING, 0, None)
        if handle and handle != _INVALID_HANDLE:
            try:
                escritos = wintypes.DWORD()
                buf = ctypes.create_string_buffer(carga, len(carga))
                return bool(_k32.WriteFile(handle, buf, len(carga),
                                           ctypes.byref(escritos), None))
            finally:
                _k32.CloseHandle(handle)
        if time.monotonic() >= limite:
            return False
        time.sleep(0.1)


def servir_lote(tuberia: str) -> int:
    """El ayudante elevado: ejecuta el lote fijo y lo manda por la tubería.

    Es lo que corre `Quilate.exe --lote-elevado <tuberia>` después de que alguien
    acepte el UAC. No imprime nada, no escribe en el disco y no mira ningún otro
    argumento: hace las once consultas de lectura, contesta y muere.

    El código de salida sirve para depurar esto a mano; nadie lo lee, porque quien
    manda es la tubería.
    """
    if not IS_WINDOWS:
        return 1
    if not _TUBERIA_VALIDA.match(tuberia or ""):
        # Un nombre que no tiene la forma que genera `_nombre_de_tuberia` no puede
        # venir de Quilate, así que no se toca.
        return 2

    # Este proceso ya está elevado, así que las consultas se hacen aquí mismo: es
    # el mismo camino que sigue `recoger` cuando a Quilate lo arrancan ya elevado.
    datos, _error = _ps_raw(
        guion_de_bloques(_CONSULTAS_ELEVADAS)
        + "$r | ConvertTo-Json -Depth 8 -Compress", timeout=50)

    # Si no ha vuelto nada legible se manda `null` en vez de no mandar nada. Las
    # dos cosas acaban en un lote sin datos, pero contestar deja al padre seguir en
    # el momento en vez de esperar a que se le acabe el plazo.
    carga = json.dumps(datos, ensure_ascii=False) if datos is not None else "null"
    return 0 if _enviar(tuberia, carga.encode("utf-8")) else 3


def _json(crudo: bytes) -> Any:
    try:
        return json.loads(crudo.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
