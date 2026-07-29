"""Presentacion en consola: color ANSI, cajas, barras, notas y formato."""

from __future__ import annotations

import ctypes
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
    # Dorado de la marca: el mismo #e8b33e del logo y del informe HTML. Entre los
    # 16 colores basicos no hay ningun dorado, asi que se usa color verdadero,
    # que admiten Windows Terminal y la consola de Windows 10 desde 2017. Si la
    # terminal no lo entiende, ignora la secuencia en vez de imprimirla.
    GOLD = "\033[38;2;232;179;62m"
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

    Se borra con la secuencia VT100 y no llamando a `cls`/`clear`: `configure_output`
    ya ha activado VT100, asi que hace lo mismo sin levantar un proceso hijo por
    ejecucion y sin depender de que %COMSPEC% en Windows —o el `clear` que haya
    en el PATH en Linux— sean los que uno espera.
    """
    if not sys.stdout.isatty():
        return
    print("\033[2J\033[H", end="")   # borrar la pantalla y volver al origen


def read_key() -> str:
    """Lee una tecla y la devuelve normalizada: "" si es Enter, la letra en
    minuscula en otro caso, y "\\x1b" para Escape, Ctrl+C o fin de entrada.

    En Windows `msvcrt` lee del handle de consola sin esperar a Enter, que es lo
    que se espera de un menu de una sola tecla. Fuera de Windows no hay
    equivalente en la biblioteca estandar sin manosear termios, asi que se cae a
    `input()`: alli habra que pulsar Enter ademas de la letra.
    """
    raw = ""
    if IS_WINDOWS:
        try:
            import msvcrt

            raw = msvcrt.getwch()
            if raw in ("\x00", "\xe0"):
                msvcrt.getwch()   # teclas extendidas (flechas, F1...): descartar
                return "?"
        except (ImportError, OSError):
            raw = ""
    if not raw:
        try:
            raw = input().strip()[:1]
        except (EOFError, OSError):
            return "\x1b"
    if raw in ("\r", "\n"):
        return ""
    if raw == "\x03":            # Ctrl+C: msvcrt lo entrega como caracter
        return "\x1b"
    return raw.lower()


BOX_W = 78

# Nombre legible de cada componente puntuado. Vive aqui, y no en cada informe,
# porque habia cinco copias de este diccionario y la de `report` se habia
# quedado sin la GPU: en un equipo donde la grafica era el componente mas flojo,
# buscar el cuello de botella reventaba con KeyError. Una tabla de etiquetas
# duplicada no avisa cuando aparece una clave nueva.
COMPONENT_LABELS = {"cpu_single": "CPU monohilo", "cpu_multi": "CPU multihilo",
                    "memory": "Memoria", "disk": "Almacenamiento", "gpu": "GPU"}


def component_label(key: str) -> str:
    return COMPONENT_LABELS.get(key, key)


def banner() -> None:
    line = "═" * BOX_W
    print(f"\n{C.CYAN}╔{line}╗{C.RESET}")
    title = f"{APP_NAME}  v{APP_VERSION}"
    print(f"{C.CYAN}║{C.RESET}{C.BOLD}{title.center(BOX_W)}{C.RESET}{C.CYAN}║{C.RESET}")
    sub = "Benchmark · Auditoría · Estimación de mejora"
    print(f"{C.CYAN}║{C.RESET}{C.DIM}{sub.center(BOX_W)}{C.RESET}{C.CYAN}║{C.RESET}")
    print(f"{C.CYAN}╠{line}╣{C.RESET}")
    brand = f"{AUTHOR}   ·   {WEBSITE}"
    print(f"{C.CYAN}║{C.RESET}{C.GOLD}{brand.center(BOX_W)}{C.RESET}{C.CYAN}║{C.RESET}")
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


def spinner_done(detail: str = "ok", ok: bool = True, neutral: bool = False) -> None:
    """`neutral` es para lo que no procede comprobar: ni aprobado ni pendiente."""
    if neutral:
        mark = f"{C.GREY}·{C.RESET}"
    else:
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
        (140, "S", C.GOLD),
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


# Lo que le pasa al programa, dicho para quien no programa. El resto del informe
# se ha escrito con ese criterio —los `detail` de cada hallazgo explican el
# porqué, no solo el qué— y no hay motivo para que la única palabra en inglés y
# en CamelCase de toda la ejecución sea la que aparece cuando algo falla.
# Los textos son neutrales respecto a lo que se estaba haciendo, porque el mismo
# traductor se usa al leer (rastreo, red) y al escribir (exportaciones): «sin
# permisos para leerlo» quedaría mal en un fallo de escritura.
_MOTIVOS = {
    PermissionError: "permisos insuficientes",
    FileNotFoundError: "la ruta no existe",
    NotADirectoryError: "la ruta no es una carpeta",
    IsADirectoryError: "la ruta es una carpeta, no un fichero",
    TimeoutError: "ha tardado demasiado",
    MemoryError: "no hay memoria suficiente",
    InterruptedError: "algo lo ha interrumpido",
    OSError: "el sistema lo ha impedido",
}


def _motivo(exc: BaseException) -> str:
    """Traduce una excepción a algo que se pueda leer sin saber Python.

    Se recorre `_MOTIVOS` en orden y no se usa `type(exc)` directamente para
    que las subclases —`PermissionError` lo es de `OSError`— encuentren su
    mensaje concreto antes de caer en el genérico.
    """
    for clase, texto in _MOTIVOS.items():
        if isinstance(exc, clase):
            return texto
    return "ha fallado de una forma no prevista"
