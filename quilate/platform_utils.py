"""Acceso al sistema operativo: comandos, PowerShell, registro y privilegios."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from typing import Any

from .const import CREATE_NO_WINDOW, IS_WINDOWS

if IS_WINDOWS:
    import winreg
else:
    winreg = None


def _console_encoding() -> str:
    """Codificación real de la salida de los programas de consola de Windows.

    Es la página de códigos OEM (850 en un Windows en español), no la ANSI que
    Python usa por defecto (1252). Decodificar con la que no es no rompe nada de
    forma visible: convierte «Máximo» en «M ximo» y «no está sucio» en
    «no est  sucio», y ahí es donde una comprobación que busca texto localizado
    deja de encontrarlo y empieza a informar de problemas que no existen.
    """
    if not IS_WINDOWS:
        return "utf-8"
    try:
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "cp850"


def run_cmd(args: list[str], timeout: int = 25, encoding: str | None = None) -> str | None:
    """Ejecuta un comando y devuelve stdout, o None si falla."""
    try:
        res = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        if res.returncode != 0:
            return None
        return res.stdout.decode(encoding or _console_encoding(), errors="replace").strip()
    except Exception:
        return None


class PSResult(list):
    """Filas de una consulta, con memoria de si llegó a ejecutarse.

    Devolver una lista vacía cuando PowerShell falla y otra igual de vacía
    cuando la consulta se ejecutó y no encontró nada hace que «este equipo no
    tiene archivo de paginación» y «no he podido preguntarlo» se lean igual.
    Varias comprobaciones daban por buena la segunda: `ok` las separa sin
    romper a quien solo itera la lista.
    """

    __slots__ = ("ok", "error")

    def __init__(self, filas=(), ok: bool = True, error: str | None = None):
        super().__init__(filas)
        self.ok = ok
        self.error = error


# PowerShell escribe los errores no terminantes por stderr y sale con código 0,
# así que sin este envoltorio una consulta rechazada por permisos es
# indistinguible de una que no devolvió nada.
_PS_ERROR = "__QUILATE_PS_ERROR__"


def _ps_raw(command: str, timeout: int = 30) -> tuple[Any, str | None]:
    """Devuelve (datos, error). El error es None cuando la consulta se ejecutó,
    aunque no devolviera nada."""
    if not IS_WINDOWS:
        return None, "no es Windows"
    # PowerShell escribe por la consola, así que su salida sale también en la
    # página OEM y un nombre de volumen con acentos llegaría roto. Se le pide
    # UTF-8 expresamente en vez de adivinar.
    wrapped = ("$ProgressPreference='SilentlyContinue'; "
               "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
               "try { $ErrorActionPreference='Stop'; "
               f"{command}"
               f" }} catch {{ Write-Output ('{_PS_ERROR}' + $_.Exception.Message) }}")
    out = run_cmd(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", wrapped],
        timeout=timeout, encoding="utf-8",
    )
    if out is None:
        return None, "powershell no respondió"
    if out.startswith(_PS_ERROR):
        return None, out[len(_PS_ERROR):].strip()[:120] or "error sin descripción"
    if not out:
        return None, None          # se ejecutó y no devolvió nada
    try:
        return json.loads(out), None
    except json.JSONDecodeError:
        return out, None


def run_cmd_bytes(args: list[str], timeout: int = 25) -> bytes | None:
    """Igual que `run_cmd` pero sin decodificar.

    No todos los programas de consola de Windows usan la misma codificación:
    `fsutil` responde en la página OEM y `netsh wlan` en UTF-8. Cuando hay que
    probar varias, decodificar aquí obligaría a ejecutar el comando dos veces.
    """
    try:
        res = subprocess.run(args, capture_output=True, timeout=timeout,
                             creationflags=CREATE_NO_WINDOW)
        return res.stdout if res.returncode == 0 else None
    except Exception:
        return None


def ps(command: str, timeout: int = 30) -> Any:
    """Ejecuta PowerShell devolviendo JSON parseado (o None)."""
    return _ps_raw(command, timeout)[0]


def ps_json(select: str, timeout: int = 30) -> PSResult:
    """Atajo: devuelve siempre filas, y si la consulta falló lo dice en `.ok`."""
    data, error = _ps_raw(f"{select} | ConvertTo-Json -Depth 3 -Compress", timeout=timeout)
    if error is not None:
        return PSResult((), ok=False, error=error)
    if isinstance(data, dict):
        return PSResult([data])
    if isinstance(data, list):
        return PSResult(d for d in data if isinstance(d, dict))
    return PSResult()


# Windows cronometra cada arranque y anota qué lo retrasó. Es la única fuente que
# convierte «tienes muchos programas de inicio» en «estos tres se llevan 19 s».
#   100 → duración del arranque
#   101 → una aplicación tardó más de lo normal
#   102 → un driver tardó de más
#   103 → un servicio tardó de más
_BOOT_LOG = "Microsoft-Windows-Diagnostics-Performance/Operational"
_BOOT_EVENTS = (100, 101, 102, 103)


def boot_performance(max_boots: int = 30, max_delays: int = 200, timeout: int = 40) -> dict:
    """Arranques medidos por el propio Windows, en milisegundos.

    Requiere privilegios de administrador: el log tiene una ACL que se los pide
    incluso para leer. Sin ellos devuelve `{"error": ...}` y quien llame debe
    tratarlo como «no medido», no como «arranque rápido».

    Los nombres de los campos se devuelven tal cual vienen del XML del evento,
    sin normalizar, para que un esquema distinto al esperado se pueda diagnosticar
    desde el JSON exportado en vez de perderse.
    """
    if not IS_WINDOWS:
        return {"error": "solo Windows", "boots": [], "delays": []}

    # Los eventos de duración (100) y los de culpables (101-103) se piden por
    # separado: en un equipo con muchos retrasos, un único -MaxEvents sobre los
    # cuatro identificadores puede no llegar a devolver ni un solo arranque.
    retrasos = ",".join(str(i) for i in _BOOT_EVENTS if i != 100)
    command = (
        "$leer = {"
        "  param($ids, $max)"
        f"  Get-WinEvent -FilterHashtable @{{LogName='{_BOOT_LOG}'; Id=$ids}}"
        "    -MaxEvents $max -ErrorAction Stop | ForEach-Object {"
        "      $d = @{};"
        "      foreach ($n in ([xml]$_.ToXml()).Event.EventData.Data) { $d[$n.Name] = $n.'#text' };"
        "      [PSCustomObject]@{ Id = $_.Id; Time = $_.TimeCreated.ToString('s'); Data = $d } } };"
        "try {"
        f"  $arranques = @(& $leer @(100) {max_boots});"
        # Que no haya retrasos anotados es un resultado válido, no un fallo.
        f"  $retrasos = @(); try {{ $retrasos = @(& $leer @({retrasos}) {max_delays}) }} catch {{ }};"
        "  @{ ok = $true; events = @($arranques + $retrasos) } | ConvertTo-Json -Depth 6 -Compress"
        "} catch {"
        "  @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress }"
    )
    data = ps(command, timeout=timeout)
    if not isinstance(data, dict):
        return {"error": "no se pudo leer el registro de arranque", "boots": [], "delays": []}
    if not data.get("ok"):
        return {"error": str(data.get("error") or "acceso denegado"), "boots": [], "delays": []}

    boots, delays = [], []
    for event in data.get("events") or []:
        fields = event.get("Data") or {}
        if not isinstance(fields, dict):
            continue
        entry = {"time": event.get("Time"), "fields": fields}
        if event.get("Id") == 100:
            boots.append(entry)
        else:
            entry["kind"] = {101: "aplicación", 102: "driver", 103: "servicio"}.get(event.get("Id"))
            delays.append(entry)
    return {"error": None, "boots": boots, "delays": delays}


def reg_read(hive: int, path: str, name: str) -> Any:
    if not IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(hive, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except (FileNotFoundError, OSError):
        return None


def reg_key_readable(hive: int, path: str) -> bool:
    """Si la clave se puede abrir.

    `reg_list_values` devuelve {} tanto cuando la clave está vacía como cuando
    no se ha podido abrir, y hay sitios —el recuento de programas de inicio—
    donde eso es la diferencia entre «no arranca nada contigo» y «no he podido
    mirarlo».
    """
    if not IS_WINDOWS or winreg is None:
        return False
    try:
        with winreg.OpenKey(hive, path):
            return True
    except (FileNotFoundError, OSError):
        return False


def reg_list_values(hive: int, path: str) -> dict[str, Any]:
    if not IS_WINDOWS:
        return {}
    out: dict[str, Any] = {}
    try:
        with winreg.OpenKey(hive, path) as key:
            count = winreg.QueryInfoKey(key)[1]
            for i in range(count):
                name, value, _ = winreg.EnumValue(key, i)
                out[name] = value
    except (FileNotFoundError, OSError):
        pass
    return out


def pending_driver_updates(timeout: int = 90) -> list[str]:
    """Titulos de los controladores que Windows Update tiene pendientes.

    Es la unica via fiable y sin dependencias para saber si existe un driver mas
    nuevo: consultar la web de cada fabricante requeriria identificadores de
    producto que cambian y una API no documentada. Contrapartida: Windows Update
    suele ir por detras de la web del fabricante, asi que sirve para confirmar
    que hay algo mas nuevo, no para descartarlo.

    Tarda entre 10 y 30 segundos y necesita conexion, por eso solo se llama
    cuando se pide expresamente.
    """
    if not IS_WINDOWS:
        return []
    out = run_cmd(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command",
         "$ErrorActionPreference='Stop';"
         "try {"
         "  $s = New-Object -ComObject Microsoft.Update.Session;"
         "  $r = $s.CreateUpdateSearcher().Search(\"IsInstalled=0 and Type='Driver'\");"
         "  $r.Updates | ForEach-Object { $_.Title }"
         "} catch { }"],
        timeout=timeout,
    )
    return [line.strip() for line in (out or "").splitlines() if line.strip()]


def is_admin() -> bool:
    try:
        if IS_WINDOWS:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


def owns_console() -> bool:
    """True si somos el unico proceso de esta consola: es decir, doble clic.

    Windows no dice "me han abierto con doble clic", pero al hacerlo se crea una
    consola nueva cuya lista de procesos solo nos contiene a nosotros; lanzado
    desde cmd o PowerShell, el interprete tambien esta en la lista. Sirve para
    distinguir cuando podemos relanzarnos en una ventana nueva sin robarle la
    sesion a nadie.
    """
    if not IS_WINDOWS:
        return False
    try:
        buf = (ctypes.c_uint * 8)()
        n = ctypes.windll.kernel32.GetConsoleProcessList(buf, 8)
        return n == 1
    except Exception:
        return False


if IS_WINDOWS:
    from ctypes import wintypes

    class _ShellExecuteInfo(ctypes.Structure):
        """SHELLEXECUTEINFOW. Se usa la variante "Ex" de ShellExecute porque es
        la unica que devuelve el handle del proceso creado, y sin handle no se
        puede esperar al hijo ni recoger su codigo de salida."""

        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]


SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
ERROR_CANCELLED = 1223


def relaunch_as_admin(extra_args: list[str] | None = None,
                      wait: bool = False) -> int | None:
    """Vuelve a lanzarse pidiendo elevacion por UAC.

    Devuelve None si el proceso elevado no llego a arrancar —UAC rechazado o
    politica del equipo—; en otro caso quien llama debe terminar, porque el
    trabajo continua en la ventana nueva. Con `wait` espera a que el hijo acabe y
    devuelve su codigo de salida, para que en una terminal el codigo de salida
    siga significando algo; sin el, devuelve 0 en cuanto arranca.

    No se puede elevar un proceso ya en marcha: hay que crear otro, y el unico
    camino soportado es el verbo "runas". Se le pasa el directorio actual para
    que los informes se generen donde el usuario espera y no en system32, que es
    a donde va a parar un proceso elevado por defecto.
    """
    if not IS_WINDOWS:
        return None
    try:
        info = _ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = SEE_MASK_NOASYNC | (SEE_MASK_NOCLOSEPROCESS if wait else 0)
        info.lpVerb = "runas"
        info.lpFile = sys.executable
        info.lpParameters = subprocess.list2cmdline(
            list(sys.argv[1:]) + list(extra_args or []))
        info.lpDirectory = os.getcwd()
        info.nShow = 1   # SW_SHOWNORMAL
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
            return None  # ERROR_CANCELLED (1223) si el usuario dijo que no
        if not wait or not info.hProcess:
            return 0
        kernel32 = ctypes.windll.kernel32
        kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        kernel32.CloseHandle(info.hProcess)
        return int(code.value)
    except Exception:
        return None
