# -*- coding: utf-8 -*-
"""Comprueba con que codificacion responden los programas de consola de Windows.

`run_cmd` decodifica la salida de `fsutil` y `powercfg` con `GetOEMCP()`, que es
la pagina de codigos OEM del sistema (850 en un Windows en español). Pero
`configure_output()` llama antes a `SetConsoleOutputCP(65001)` para poder
imprimir el informe en UTF-8, y esa llamada NO cambia lo que devuelve
`GetOEMCP()`. Si algun binario mirara `GetConsoleOutputCP()` en vez de la OEM
para decidir como escribe, `run_cmd` lo decodificaria con la que no es.

Eso no revienta: `errors="replace"` convierte «Maximo» en «M ximo» y «no esta
sucio» en «no est  sucio» sin lanzar ninguna excepcion. Y ahi es donde una
comprobacion que busca texto localizado deja de encontrarlo. `check_power_plan`
y `check_filesystem_health` se defienden de ello, pero conviene saber si la
discrepancia existe de verdad antes de tocar nada.

Esto NO se puede comprobar sin ejecutarlo en un Windows cuyo idioma use acentos,
asi que va como script aparte y no como test.

Uso:  python tools/comprobar_codificacion.py

Lo que interesa no es solo que codec gana, sino si gana el MISMO antes y despues
de SetConsoleOutputCP.

RESULTADO EN UN WINDOWS 11 PRO EN ESPAÑOL (cp850 / cp1252), julio de 2026:

    GetConsoleOutputCP    cp850  ->  cp65001   (SI cambia)
    bytes de powercfg     identicos en las dos vueltas
    bytes de fsutil       identicos en las dos vueltas
    codec ganador         cp850 en las dos vueltas

  Conclusion: NO hay discrepancia. Con `capture_output=True` la salida va a una
  tuberia, y ni `powercfg` ni `fsutil` miran `GetConsoleOutputCP` para decidir
  como escriben: siguen usando la pagina OEM. `run_cmd` hace lo correcto
  decodificando con `GetOEMCP()`, y no hay nada que cambiar.

  Hallazgo secundario, que importa si algun dia SI hubiera que cambiarlo: cp1252
  decodifica estos bytes con CERO caracteres de sustitucion y aun asi da el texto
  equivocado («energ¡a» en vez de «energia»). Es decir, el criterio de
  `network._decodificar` —quedarse con el codec que menos sustituciones
  produzca— NO sabe separar cp850 de cp1252. Alli funciona por el orden de la
  tupla, no porque el criterio discrimine. Si alguna vez hay que elegir codec
  aqui, hara falta un criterio mejor que contar sustituciones.

Este script se conserva para poder repetir la comprobacion en otras
localizaciones (aleman, polaco, ruso), donde la respuesta podria ser otra.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quilate.const import CREATE_NO_WINDOW, IS_WINDOWS       # noqa: E402
from quilate.platform_utils import _sys_exe                  # noqa: E402

# Los mismos que prueba `network._decodificar`, mas la UTF-8 que dejaria
# SetConsoleOutputCP si algun binario le hiciera caso.
CODECS = ("cp850", "cp437", "cp1252", "utf-8")

# Palabras con acento que tienen que aparecer enteras en la respuesta. Si el
# codec es el correcto se leen bien; si no, salen partidas.
ACENTOS = ("á", "é", "í", "ó", "ú", "ñ", "Á", "É", "Í", "Ó", "Ú", "Ñ")

COMANDOS = [
    ("powercfg /getactivescheme", [_sys_exe("powercfg.exe"), "/getactivescheme"]),
    ("fsutil dirty query C:", [_sys_exe("fsutil.exe"), "dirty", "query",
                               "C:"]),
    ("fsutil behavior query DisableDeleteNotify",
     [_sys_exe("fsutil.exe"), "behavior", "query", "DisableDeleteNotify"]),
]


def paginas_de_codigos() -> dict[str, int]:
    k32 = ctypes.windll.kernel32
    return {
        "GetOEMCP (la que usa run_cmd)": k32.GetOEMCP(),
        "GetACP (la ANSI del sistema)": k32.GetACP(),
        "GetConsoleOutputCP (la de la consola)": k32.GetConsoleOutputCP(),
    }


def probar(etiqueta: str, args: list[str]) -> None:
    print(f"\n{'=' * 78}\n  {etiqueta}\n{'=' * 78}")
    try:
        res = subprocess.run(args, capture_output=True, timeout=20,
                             creationflags=CREATE_NO_WINDOW)
    except OSError as exc:
        print(f"  no se ha podido ejecutar: {exc}")
        return
    if res.returncode != 0:
        print(f"  codigo de salida {res.returncode} "
              f"(fsutil dirty query necesita administrador)")
    crudo = res.stdout or res.stderr
    if not crudo:
        print("  sin salida")
        return
    print(f"  bytes crudos: {crudo[:120]!r}")
    print()
    resultados = []
    for codec in CODECS:
        try:
            texto = crudo.decode(codec, errors="replace")
        except LookupError:
            continue
        fallos = texto.count("�")
        acentos = sum(1 for a in ACENTOS if a in texto)
        resultados.append((codec, fallos, acentos, texto))
        print(f"  {codec:8} | {fallos} sustituciones | {acentos} acentos reconocidos"
              f"\n           {texto.strip()[:100]!r}")
    if not resultados:
        return
    # Se ordena por acentos reconocidos ANTES que por sustituciones, y no al
    # reves: cp1252 decodifica estos bytes sin producir ni un solo caracter de
    # sustitucion, y aun asi da el texto equivocado. Contar sustituciones —que
    # es lo que hace `network._decodificar`— no separa cp850 de cp1252 aqui.
    por_fallos = min(resultados, key=lambda r: r[1])[0]
    por_acentos = max(resultados, key=lambda r: (r[2], -r[1]))[0]
    print(f"\n  --> el que menos sustituciones produce: {por_fallos}")
    print(f"  --> el que reconoce mas acentos:        {por_acentos}")
    if por_fallos != por_acentos:
        print("      OJO: no coinciden. Contar sustituciones NO basta para elegir.")


def main() -> int:
    if not IS_WINDOWS:
        print("Esto solo tiene sentido en Windows.")
        return 1
    # Este script imprime a proposito texto mal decodificado, asi que su propia
    # salida lleva caracteres que la consola heredada no sabe escribir. Sin
    # esto, el script muere con UnicodeEncodeError antes de contar nada.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    print(f"Python {sys.version.split()[0]}  ·  {sys.platform}")
    print("\nPAGINAS DE CODIGOS ANTES DE TOCAR NADA")
    for nombre, valor in paginas_de_codigos().items():
        print(f"  {nombre:40} cp{valor}")

    for etiqueta, args in COMANDOS:
        probar(etiqueta, args)

    # Segunda vuelta: lo mismo pero despues de que `configure_output()` haya
    # puesto la consola en UTF-8, que es como corre el programa de verdad.
    print(f"\n\n{'#' * 78}\n#  AHORA CON LA CONSOLA YA EN UTF-8 (como la deja configure_output)\n{'#' * 78}")
    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    print("\nPAGINAS DE CODIGOS DESPUES")
    for nombre, valor in paginas_de_codigos().items():
        print(f"  {nombre:40} cp{valor}")
    for etiqueta, args in COMANDOS:
        probar(etiqueta, args)

    print("\n\nQUE MIRAR:")
    print("  1. Si gana el MISMO codec en las dos vueltas, no hay discrepancia y")
    print("     `run_cmd` puede seguir decodificando con GetOEMCP() sin mas.")
    print("  2. Si en la segunda vuelta gana utf-8 y en la primera cp850, entonces")
    print("     los binarios SI miran GetConsoleOutputCP y hay que aplicar en")
    print("     `run_cmd` el patron de `network._decodificar`: probar varios y")
    print("     quedarse con el que menos sustituciones produzca.")
    print("  3. Si algun comando sale con 0 acentos reconocidos en TODOS los codecs,")
    print("     es que ese comando no devuelve texto acentuado en este equipo y no")
    print("     sirve para decidir: hace falta uno que si.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
