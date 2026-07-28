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
import secrets
import time
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


def recoger() -> dict[str, PSResult]:
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
        _recogido = consulta_elevada(_CONSULTAS_ELEVADAS)
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

_SEE_MASK_NOASYNC = 0x00000100
_SW_HIDE = 0
_BUFFER = 1 << 16


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


def _escuchar(handle, evento, plazo: float) -> bytes | None:
    """Espera al hijo y devuelve lo que escriba, o None si no llega a tiempo.

    Todo va con E/S solapada porque si no la espera no tiene fin: con una
    tubería bloqueante, un UAC rechazado o un PowerShell que muere antes de
    conectarse dejarían a Quilate esperando para siempre a alguien que ya no
    va a venir.
    """
    ov = _Overlapped()
    ov.hEvent = evento
    buf = ctypes.create_string_buffer(_BUFFER)
    leidos = wintypes.DWORD()

    def esperar() -> bool:
        restante = int(max(0.0, plazo - time.monotonic()) * 1000)
        return _k32.WaitForSingleObject(evento, restante) == _WAIT_OBJECT_0

    try:
        _k32.ResetEvent(evento)
        if not _k32.ConnectNamedPipe(handle, ctypes.byref(ov)):
            error = ctypes.get_last_error()
            if error == _ERROR_IO_PENDING:
                if not esperar():
                    return None
            elif error != _ERROR_PIPE_CONNECTED:
                return None

        trozos = []
        while True:
            _k32.ResetEvent(evento)
            if not _k32.ReadFile(handle, buf, _BUFFER, ctypes.byref(leidos),
                                 ctypes.byref(ov)):
                error = ctypes.get_last_error()
                if error == _ERROR_IO_PENDING:
                    if not esperar():
                        return None
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
        _k32.CancelIo(handle)
        _k32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(leidos), True)


def consulta_elevada(consultas: dict[str, str], timeout: int = 60,
                     ) -> dict[str, PSResult]:
    """Ejecuta el lote en un PowerShell elevado y trocea la respuesta.

    Devuelve un `PSResult` por consulta, igual que el inventario. Que el usuario
    diga que no al aviso de UAC es un camino normal y no un fallo: las claves
    salen con `ok=False` y un motivo que se puede enseñar tal cual, para que el
    informe diga «no se comprobó porque no se dieron permisos» en vez de dar por
    bueno lo que nadie ha llegado a mirar.
    """
    if not IS_WINDOWS:
        return trocear(None, consultas, "solo Windows")
    if not consultas:
        return {}

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
        guion = _guion(consultas, nombre)
        codificado = base64.b64encode(guion.encode("utf-16-le")).decode("ascii")
        arrancado = _lanzar_elevado(
            _sys_exe("powershell.exe"),
            "-NoProfile -NonInteractive -ExecutionPolicy Bypass "
            f"-WindowStyle Hidden -EncodedCommand {codificado}")
        if not arrancado:
            return trocear(None, consultas, SIN_PERMISOS)
        crudo = _escuchar(handle, evento, time.monotonic() + timeout)
    finally:
        _k32.CloseHandle(handle)
        if evento:
            _k32.CloseHandle(evento)

    if not crudo:
        return trocear(None, consultas,
                       "el proceso con permisos no ha contestado a tiempo")
    return trocear(_json(crudo), consultas,
                   "el proceso con permisos no ha devuelto nada legible")


def _json(crudo: bytes) -> Any:
    try:
        return json.loads(crudo.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
