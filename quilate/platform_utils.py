"""Acceso al sistema operativo: comandos, PowerShell, registro y privilegios."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
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
