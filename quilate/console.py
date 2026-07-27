"""Presentacion en consola: color ANSI, cajas, barras, notas y formato."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from typing import Any

from .const import APP_NAME, APP_VERSION, AUTHOR, IS_WINDOWS, WEBSITE


class C:
    """Códigos ANSI. Se desactivan solos si la terminal no los soporta."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GREY = "\033[90m"

    @classmethod
    def disable(cls) -> None:
        for attr in dir(cls):
            if attr.isupper():
                setattr(cls, attr, "")


def enable_ansi() -> None:
    """Activa las secuencias VT100 en consolas de Windows."""
    if not sys.stdout.isatty():
        C.disable()
        return
    if IS_WINDOWS:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            C.disable()


def configure_output() -> None:
    """Prepara la salida para el juego de caracteres del informe.

    El informe usa caracteres de dibujo de caja, flechas y acentos. Sin esto, en
    cuanto la consola hereda una pagina de codigos heredada (cp1252 en Windows en
    español, que es justo lo que recibe el .exe empaquetado) el primer print del
    banner revienta con UnicodeEncodeError. `errors="replace"` deja ademas una
    red de seguridad: antes un caracter raro que una traza.
    """
    if IS_WINDOWS:
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def clear_screen() -> None:
    """Limpia la consola antes de empezar el informe.

    Solo cuando la salida va a un terminal: si esta redirigida a un fichero o a
    otro proceso, el borrado no tendria efecto visible y ademas ensuciaria el
    destino con el codigo de control.
    """
    if not sys.stdout.isatty():
        return
    try:
        subprocess.run("cls" if IS_WINDOWS else "clear", shell=True, check=False)
    except OSError:
        print("\033[2J\033[H", end="")   # respaldo: borrar y volver al origen


BOX_W = 78


def banner() -> None:
    line = "═" * BOX_W
    print(f"\n{C.CYAN}╔{line}╗{C.RESET}")
    title = f"{APP_NAME}  v{APP_VERSION}"
    print(f"{C.CYAN}║{C.RESET}{C.BOLD}{title.center(BOX_W)}{C.RESET}{C.CYAN}║{C.RESET}")
    sub = "Benchmark · Auditoría · Estimación de mejora"
    print(f"{C.CYAN}║{C.RESET}{C.DIM}{sub.center(BOX_W)}{C.RESET}{C.CYAN}║{C.RESET}")
    print(f"{C.CYAN}╠{line}╣{C.RESET}")
    brand = f"{AUTHOR}   ·   {WEBSITE}"
    print(f"{C.CYAN}║{C.RESET}{C.MAGENTA}{brand.center(BOX_W)}{C.RESET}{C.CYAN}║{C.RESET}")
    print(f"{C.CYAN}╚{line}╝{C.RESET}")


def section(title: str) -> None:
    print(f"\n{C.BOLD}{C.BLUE}▌ {title.upper()}{C.RESET}")
    print(f"{C.GREY}{'─' * BOX_W}{C.RESET}")


def kv(key: str, value: Any, width: int = 26) -> None:
    print(f"  {C.GREY}{str(key).ljust(width, '.')}{C.RESET} {value}")


def bar(pct: float, width: int = 28, good_high: bool = True) -> str:
    pct = max(0.0, min(150.0, float(pct)))
    filled = int(round(width * min(pct, 100) / 100))
    if good_high:
        color = C.GREEN if pct >= 85 else C.YELLOW if pct >= 55 else C.RED
    else:
        color = C.RED if pct >= 85 else C.YELLOW if pct >= 55 else C.GREEN
    return f"{color}{'█' * filled}{C.GREY}{'░' * (width - filled)}{C.RESET}"


def spinner_step(msg: str) -> None:
    print(f"  {C.CYAN}▸{C.RESET} {msg} ", end="", flush=True)


def spinner_done(detail: str = "ok", ok: bool = True) -> None:
    mark = f"{C.GREEN}✓{C.RESET}" if ok else f"{C.YELLOW}~{C.RESET}"
    print(f"{mark} {C.DIM}{detail}{C.RESET}")


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} EB"


def grade(score: float) -> tuple[str, str]:
    """Devuelve (letra, color) para un score normalizado (100 = referencia)."""
    table = [
        (140, "S", C.MAGENTA),
        (115, "A+", C.GREEN),
        (95, "A", C.GREEN),
        (80, "B", C.CYAN),
        (60, "C", C.YELLOW),
        (40, "D", C.YELLOW),
        (0, "F", C.RED),
    ]
    for threshold, letter, color in table:
        if score >= threshold:
            return letter, color
    return "F", C.RED


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]
