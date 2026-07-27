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


def run_cmd(args: list[str], timeout: int = 25) -> str | None:
    """Ejecuta un comando y devuelve stdout, o None si falla."""
    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            errors="replace",
        )
        return res.stdout.strip() if res.returncode == 0 else None
    except Exception:
        return None


def ps(command: str, timeout: int = 30) -> Any:
    """Ejecuta PowerShell devolviendo JSON parseado (o None)."""
    if not IS_WINDOWS:
        return None
    wrapped = f"$ProgressPreference='SilentlyContinue'; {command}"
    out = run_cmd(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", wrapped],
        timeout=timeout,
    )
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def ps_json(select: str, timeout: int = 30) -> list[dict]:
    """Atajo: devuelve siempre una lista de dicts."""
    data = ps(f"{select} | ConvertTo-Json -Depth 3 -Compress", timeout=timeout)
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def reg_read(hive: int, path: str, name: str) -> Any:
    if not IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(hive, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except (FileNotFoundError, OSError):
        return None


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
