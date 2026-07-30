# -*- coding: utf-8 -*-
"""Genera los dos recursos de Windows que se incrustan en Quilate.exe.

    build/version_info.txt   el VERSIONINFO (quien publica, que version)
    build/quilate.manifest   el manifest de la aplicacion

Los dos salen de `quilate/const.py` y no se escriben a mano. Es la unica forma
de que no haya un dia en que el .exe declare una version y el programa imprima
otra: la version vive en un sitio, y aqui se lee.

--- Por que hace falta el VERSIONINFO ---

Sin el, el .exe sale con CompanyName, ProductName, FileDescription y FileVersion
vacios, que es como estaba: las propiedades del fichero no dicen ni quien lo
publica ni que version es. Eso importa por dos motivos distintos.

El primero es la firma. Authenticode y el VERSIONINFO son cosas separadas —el
"editor" que enseña SmartScreen sale del certificado, no de aqui— pero se leen
juntas: una vez firmado, el dialogo de propiedades muestra el nombre del
certificado en la pestana de firmas y estos campos en la de detalles. Que la
firma diga "Cristian Alonso" y el fichero no diga nada es justo el tipo de
discrepancia que no conviene ensenar.

El segundo es la deteccion heuristica. Un binario de 6 MB sin un solo campo de
metadatos es una senal por si misma para los clasificadores de reputacion: los
binarios legitimos los llevan y los generados en serie no. No es la causa de una
deteccion, pero es gratis quitarla de en medio.

--- Por que el manifest declara asInvoker y no requireAdministrator ---

Esto es deliberado y va contra la solucion que parece obvia. El razonamiento
esta en el comentario de `NIVEL`, mas abajo.

Uso:  python tools/make_win_resources.py [directorio_destino]
      (por defecto, build/)
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from quilate.const import APP_NAME, APP_VERSION, AUTHOR, WEBSITE_URL  # noqa: E402

# El ano del copyright sale del LICENSE, que es el que manda: tenerlo escrito
# dos veces es tenerlo mal en una de las dos.
ANO = "2026"

# 0x0C0A es español de España; 0x04B0 (1200) es Unicode. El programa habla
# español, así que el bloque se declara en español. Windows resuelve la
# traducción leyendo el VarFileInfo, así que un sistema en inglés también
# encuentra estos campos: no hace falta un bloque 0x0409 aparte.
IDIOMA = 0x0C0A
PAGINA = 0x04B0

# El nombre del ensamblado no es un identificador cualquiera: por convencion va
# en notacion de dominio invertido, y el dominio es el del proyecto.
IDENTIDAD = "es.cristianac.quilate"

# ------------------------------------------------------------------ el nivel --
# `asInvoker` significa "arranca con los permisos de quien me lance, sin pedir
# nada". Es lo que Quilate necesita, y ponerle `requireAdministrator` seria un
# retroceso, no un arreglo. Merece la pena dejarlo escrito porque la conclusion
# es contraintuitiva:
#
# Quilate NO se auto-eleva. El proceso principal corre sin privilegios de
# principio a fin. Lo unico que se eleva es un PowerShell aparte que ejecuta un
# lote fijo de once consultas de lectura, contesta por una tuberia con nombre y
# muere en dos segundos (ver el docstring de `quilate/elevacion.py`, que explica
# el diseno entero). Eso NO es auto-elevacion oculta: es un proceso auxiliar
# elevado, que es un patron distinto y legitimo —lo usan los instaladores y los
# paneles de control— y ademas es la version segura de lo que habia antes.
#
# Poner `requireAdministrator` volveria a elevar el programa completo, que es
# exactamente de lo que el proyecto se alejo a proposito. Sus consecuencias
# estan documentadas en `elevacion.py`: los informes salian con propietario
# Administrador, y el historico dependia de un `LOCALAPPDATA` heredado de un
# proceso sin elevar. Ademas obligaria a pasar por UAC para `--history` o
# `--compare`, que no tocan nada del sistema, y pondria el banco de pruebas y el
# rastreo de ficheros —lo que mas trabajo hace— en integridad alta sin ninguna
# necesidad.
#
# Y no arreglaria la deteccion, que es lo que se venia a arreglar: un proceso
# elevado hace que Defender vigile MAS, no menos.
NIVEL = "asInvoker"


def cuarteto(version: str) -> tuple[int, int, int, int]:
    """`filevers` y `prodvers` son cuatro enteros, y APP_VERSION trae tres.

    El cuarto es el numero de compilacion, que este proyecto no usa: se rellena
    con cero en lugar de inventarselo. Un componente no numerico (un `2.7.0rc1`)
    aborta aqui en vez de colarse como basura dentro del recurso.
    """
    partes = version.split(".")
    if not all(p.isdigit() for p in partes) or not 1 <= len(partes) <= 4:
        raise SystemExit(f"[!] APP_VERSION no es numerica y no cabe en VERSIONINFO: {version!r}")
    numeros = [int(p) for p in partes] + [0] * (4 - len(partes))
    return tuple(numeros)  # type: ignore[return-value]


def version_info() -> str:
    v = cuarteto(APP_VERSION)
    campos = [
        # OriginalFilename e InternalName tienen que coincidir con el nombre real
        # del binario: si no, Windows los ensena y no cuadran con el fichero.
        ("CompanyName", AUTHOR),
        ("FileDescription", f"{APP_NAME} - Benchmark y Auditoria"),
        ("FileVersion", APP_VERSION),
        ("InternalName", "quilate"),
        ("LegalCopyright", f"(c) {ANO} {AUTHOR}. Licencia MIT."),
        ("OriginalFilename", "Quilate.exe"),
        ("ProductName", APP_NAME),
        ("ProductVersion", APP_VERSION),
        ("Comments", f"Herramienta de solo lectura. {WEBSITE_URL}"),
    ]
    entradas = ",\n".join(
        f"        StringStruct('{clave}', '{valor}')" for clave, valor in campos)
    return f"""# Recurso VERSIONINFO de Quilate.exe.
#
# GENERADO por tools/make_win_resources.py — no editar a mano: se sobrescribe en
# cada compilacion. Los datos salen de quilate/const.py.

VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v},
    prodvers={v},
    # mask/flags: se declaran los cuatro campos de flags como validos y ninguno
    # activo. En concreto VS_FF_DEBUG y VS_FF_PRERELEASE apagados, que es lo que
    # corresponde a un binario de release.
    mask=0x3f,
    flags=0x0,
    # 0x40004 = VOS_NT_WINDOWS32. 0x1 = VFT_APP (una aplicacion, no una DLL).
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '{IDIOMA:04x}{PAGINA:04x}',
        [
{entradas},
        ])
    ]),
    VarFileInfo([VarStruct('Translation', [{IDIOMA}, {PAGINA}])])
  ]
)
"""


def manifest() -> str:
    v = ".".join(str(n) for n in cuarteto(APP_VERSION))
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!-- Manifest de Quilate.exe.

     GENERADO por tools/make_win_resources.py — no editar a mano: se sobrescribe
     en cada compilacion.

     Sustituye al manifest generico que PyInstaller pone por su cuenta, que no
     declara `supportedOS` ni `longPathAware`. Declararlo todo de forma explicita
     es lo contrario de auto-elevarse por codigo: aqui queda escrito, dentro del
     binario, que este programa no pide permisos al arrancar, y cualquiera puede
     comprobarlo sin leer el fuente.
-->
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">

  <assemblyIdentity type="win32"
                    name="{IDENTIDAD}"
                    version="{v}"
                    processorArchitecture="*"/>

  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <!-- asInvoker: el proceso principal nunca se eleva. Lo que necesita
             permisos se va a un PowerShell aparte que vive dos segundos. El
             razonamiento completo esta en `NIVEL`, en el generador. -->
        <requestedExecutionLevel level="{NIVEL}" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>

  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <!-- Sin esto Windows aplica el modo de compatibilidad de Vista y las APIs
           que consultan la version del sistema devuelven 6.2 en un Windows 11.
           Quilate no pregunta la version por esa via —usa `platform` y WMI— pero
           declararlo es lo correcto y evita que un cambio futuro se encuentre
           una version falseada. Los dos GUID son Windows 8.1 y Windows 10/11:
           Microsoft no ha publicado uno nuevo para el 11, se sigue usando el
           del 10. -->
      <supportedOS Id="{{1f676c76-80e1-4239-95bb-83d0f6d0da78}}"/>
      <supportedOS Id="{{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}}"/>
    </application>
  </compatibility>

  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <!-- El rastreo de almacenamiento recorre arboles de directorios enteros y
           puede encontrarse rutas de mas de 260 caracteres, que sin esto fallan
           con un error que no dice lo que pasa. -->
      <longPathAware
          xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>

</assembly>
"""


def main() -> int:
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "build"
    destino.mkdir(parents=True, exist_ok=True)

    for nombre, contenido in (("version_info.txt", version_info()),
                              ("quilate.manifest", manifest())):
        # UTF-8 sin BOM: el manifest declara `encoding="UTF-8"` en su cabecera y
        # un BOM delante de esa declaracion rompe algunos analizadores de XML.
        (destino / nombre).write_text(contenido, encoding="utf-8")
        print(f"  {nombre}")

    print(f"{APP_NAME} {APP_VERSION} · {AUTHOR} · nivel {NIVEL}  ->  {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
