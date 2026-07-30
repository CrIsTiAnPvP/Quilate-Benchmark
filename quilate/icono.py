"""El logo y el nombre de Quilate en la ventana de la consola.

Son dos cosas distintas y no se arreglan igual, asi que conviene tenerlas
separadas desde el principio:

  El TITULO (`poner_titulo`) se puede poner siempre. `SetConsoleTitleW` funciona
  en la consola clasica y tambien en Windows Terminal, porque el titulo de la
  pestana sigue al del proceso. Sin esto, al abrir el .exe con doble clic la
  pestana se llama `C:\\ruta\\donde\\este\\quilate`, que es la ruta del fichero.

  El ICONO (`aplicar`) solo se puede poner con la consola clasica. Lo de abajo
  explica por que.


El icono esta bien puesto dentro del .exe: PyInstaller incrusta `quilate.ico`
como recurso y ahi estan sus siete resoluciones (un RT_GROUP_ICON y siete
RT_ICON, comprobado sobre el binario compilado). Por eso el Explorador lo ensena
bien. Lo que no se hereda es la ventana, y el motivo es que la ventana no es de
Quilate: una aplicacion de consola no tiene ventana propia. La pinta el
anfitrion, que es otro proceso, y cada anfitrion decide su icono de una forma
distinta.

Hay dos, y solo uno tiene arreglo:

  conhost.exe (la consola clasica)
      La ventana es de conhost, pero Windows deja cambiarle el icono desde el
      proceso que la usa, con `SetConsoleIcon`. Eso es lo que hace este modulo, y
      funciona: el logo sale en la barra de titulo, en la barra de tareas y en
      Alt+Tab.

  Windows Terminal (el predeterminado en Windows 11)
      Aqui no hay nada que hacer desde el proceso, y conviene ser claro en vez de
      dejar que parezca un fallo. Terminal no dibuja una ventana por programa:
      dibuja pestanas, y el icono de una pestana sale del *perfil* con el que se
      abrio. No existe API para cambiarlo —`SetConsoleIcon` no falla, simplemente
      no tiene ninguna ventana sobre la que actuar, porque lo que devuelve
      `GetConsoleWindow` bajo Terminal es una ventana oculta de ConPTY— y
      tampoco hay secuencia de escape: Terminal no implementa la de ConEmu para
      iconos de pestana.

      La unica via seria registrar un perfil de Terminal que apunte al .ico y
      abrir Quilate a traves de el, pero eso solo sirve cuando se abre DESDE el
      perfil: al hacer doble clic sobre el .exe en el Explorador, Windows le
      entrega la consola a Terminal directamente y no se pasa por ningun perfil.
      Como ese es justo el caso que importa, no se registra ningun perfil y se
      asume que bajo Terminal no hay logo.

      El nombre de la pestana si se arregla, y en los dos anfitriones: eso es
      `poner_titulo`, aqui abajo.

Nada de aqui es imprescindible: si algo falla, se devuelve el motivo y el
programa sigue. Un icono no es razon para no arrancar.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from .const import APP_NAME, APP_VERSION, IS_WINDOWS

# Anfitriones que se distinguen. Se devuelven como cadena y no como booleano
# porque "no lo se" es un caso real —una consola de terceros, ConEmu, un
# ejecutable lanzado sin consola— y tratarlo como si fuera conhost seria
# adivinar.
CONHOST = "conhost"
TERMINAL = "terminal"
SIN_CONSOLA = "sin consola"
OTRO = "otro"

_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x00000010
_LR_DEFAULTSIZE = 0x00000040
_WM_SETICON = 0x0080
_ICON_SMALL = 0
_ICON_BIG = 1


def _ventana_de_consola() -> int:
    try:
        return int(ctypes.windll.kernel32.GetConsoleWindow() or 0)
    except Exception:
        return 0


def anfitrion() -> str:
    """Quien dibuja la ventana: conhost, Windows Terminal, otro, o ninguna.

    Se mira primero la ventana y despues el entorno, y ese orden importa.
    `WT_SESSION` es la senal documentada de Windows Terminal, pero es una
    variable de entorno: la heredan los hijos. Un `cmd` abierto dentro de
    Terminal que lance Quilate en una consola nueva la sigue teniendo puesta
    aunque esa consola ya no sea de Terminal. La clase de la ventana no se hereda
    y no se puede falsear por accidente: bajo Terminal lo que devuelve
    `GetConsoleWindow` es una ventana oculta de ConPTY, con su propia clase, y
    bajo la consola clasica es `ConsoleWindowClass`.
    """
    if not IS_WINDOWS:
        return SIN_CONSOLA
    hwnd = _ventana_de_consola()
    if not hwnd:
        return SIN_CONSOLA
    try:
        buf = ctypes.create_unicode_buffer(256)
        if ctypes.windll.user32.GetClassNameW(hwnd, buf, 256):
            clase = buf.value
            if clase == "ConsoleWindowClass":
                return CONHOST
            if "Pseudo" in clase or "Cascadia" in clase:
                return TERMINAL
    except Exception:
        pass
    # La ventana no ha dicho nada util: ahora si vale preguntarle al entorno,
    # con lo dicho arriba sobre lo que vale su respuesta.
    if os.environ.get("WT_SESSION"):
        return TERMINAL
    return OTRO


def poner_titulo(texto: str | None = None) -> str:
    """Le pone nombre a la ventana. Devuelve el motivo si no puede.

    Esto si funciona en los dos anfitriones, y es la mitad del problema que se
    ve al abrir el .exe con doble clic: sin ponerlo, la pestana se llama con la
    ruta del ejecutable —`E:\\algo\\dist\\Quilate`— porque a falta de titulo es lo
    que enseñan tanto conhost como Windows Terminal.

    Bajo Windows Terminal el titulo de la pestana sigue al del proceso, asi que
    esto llega igual aunque el icono no. Comprobado dentro de los dos: conhost
    pasa de `C:\\WINDOWS\\system32\\conhost.exe` al nombre y la version del
    programa, y Terminal de `Default` a lo mismo. Por eso esta funcion no
    pregunta por el anfitrion y `aplicar` si.
    """
    if not IS_WINDOWS:
        return "solo Windows"
    if not _ventana_de_consola():
        # Con la salida redirigida a un fichero no hay ventana a la que ponerle
        # nombre, y `SetConsoleTitleW` no serviria de nada.
        return "no hay ventana de consola"
    try:
        k32 = ctypes.windll.kernel32
        # Los tipos se declaran con los de `ctypes` y no con los de
        # `ctypes.wintypes`, que es lo que se usaria normalmente: ese modulo no se
        # puede importar fuera de Windows, y este fichero se importa en Linux cada
        # vez que corren los tests. `elevacion.py` lo resuelve metiendo el import
        # dentro de un `if IS_WINDOWS`; aqui no hace falta llegar a eso, porque
        # `c_wchar_p` y `c_bool` valen igual.
        k32.SetConsoleTitleW.argtypes = [ctypes.c_wchar_p]
        k32.SetConsoleTitleW.restype = ctypes.c_bool
        nombre = texto or f"{APP_NAME} {APP_VERSION}"
        return "" if k32.SetConsoleTitleW(nombre) else "la consola no ha aceptado el titulo"
    except Exception as exc:
        return f"no se ha podido poner el titulo: {exc}"


def _origen_del_icono() -> Path | None:
    """De donde sacar el icono, segun si esto es el .exe o el codigo fuente.

    Empaquetado no hace falta buscar ningun fichero: el propio ejecutable lleva
    el icono dentro y `ExtractIconEx` lo saca de ahi. Sin empaquetar se usa el
    `quilate.ico` del repositorio, que es lo mismo que acabara incrustado, para
    que trabajar sobre el codigo se vea igual que el resultado.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    ico = Path(__file__).resolve().parent.parent / "quilate.ico"
    return ico if ico.is_file() else None


def _cargar(origen: Path) -> tuple[int, int]:
    """Devuelve (grande, pequeno) como HICON. Cero en los que no salgan.

    Del .exe se extraen con `ExtractIconEx`, que entiende de recursos y devuelve
    las dos resoluciones que Windows quiere para una ventana: la grande para
    Alt+Tab y la pequena para la barra de titulo. De un .ico se carga con
    `LoadImage`, que solo da una.
    """
    if origen.suffix.lower() == ".ico":
        h = ctypes.windll.user32.LoadImageW(
            None, str(origen), _IMAGE_ICON, 0, 0, _LR_LOADFROMFILE | _LR_DEFAULTSIZE)
        return int(h or 0), int(h or 0)

    grande = ctypes.c_void_p()
    pequeno = ctypes.c_void_p()
    ctypes.windll.shell32.ExtractIconExW(
        ctypes.c_wchar_p(str(origen)), 0,
        ctypes.byref(grande), ctypes.byref(pequeno), 1)
    return int(grande.value or 0), int(pequeno.value or 0)


def aplicar() -> str:
    """Pone el logo en la ventana de la consola. Devuelve el motivo si no puede.

    La cadena vacia significa que se ha aplicado. Cualquier otra cosa es una
    explicacion para quien este depurando esto, no un error que haya que
    ensenarle a nadie: se llama sin mirar el resultado.
    """
    if not IS_WINDOWS:
        return "solo Windows"

    donde = anfitrion()
    if donde == SIN_CONSOLA:
        return "no hay ventana de consola"
    if donde == TERMINAL:
        # No se intenta. `SetConsoleIcon` devolveria exito sobre la ventana
        # oculta de ConPTY y no se veria ningun cambio, que es la peor de las
        # respuestas posibles: la de que todo ha ido bien cuando no ha ido nada.
        return "Windows Terminal no permite cambiar el icono de la pestana"

    origen = _origen_del_icono()
    if origen is None:
        return "no se encuentra el icono"

    try:
        grande, pequeno = _cargar(origen)
        if not grande and not pequeno:
            return f"no se ha podido cargar el icono de {origen.name}"

        k32 = ctypes.windll.kernel32
        # `SetConsoleIcon` no esta documentada, pero la exporta kernel32 desde XP
        # y sigue exportada en Windows 11 (comprobado con GetProcAddress). Que no
        # este documentada es justo el motivo de declararle los tipos a mano en
        # vez de dejar que ctypes adivine: recibe un handle, que en 64 bits no
        # cabe en el `int` que ctypes supone por defecto.
        k32.SetConsoleIcon.argtypes = [ctypes.c_void_p]
        k32.SetConsoleIcon.restype = ctypes.c_bool
        if k32.SetConsoleIcon(ctypes.c_void_p(grande or pequeno)):
            return ""

        # Plan B para las consolas que no implementan la llamada de arriba: se le
        # manda el icono a la ventana directamente. Funciona porque un HICON vale
        # en toda la sesion, no solo dentro del proceso que lo creo.
        hwnd = _ventana_de_consola()
        user32 = ctypes.windll.user32
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                        ctypes.c_void_p, ctypes.c_void_p]
        enviado = False
        for cual, handle in ((_ICON_BIG, grande), (_ICON_SMALL, pequeno)):
            if handle:
                user32.SendMessageW(ctypes.c_void_p(hwnd), _WM_SETICON,
                                    ctypes.c_void_p(cual), ctypes.c_void_p(handle))
                enviado = True
        return "" if enviado else "la consola no ha aceptado el icono"
    except Exception as exc:
        return f"no se ha podido poner el icono: {exc}"
