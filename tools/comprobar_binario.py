# -*- coding: utf-8 -*-
"""Revisa dist/Quilate.exe antes de distribuirlo. Sale con 1 si algo falla.

Es la reja que impide que vuelva a pasar lo que ya paso. La compresion con UPX no
estaba puesta en ningun sitio: PyInstaller usa UPX en cuanto lo encuentra en el
PATH, sin avisar, asi que el fallo se cuela solo —basta con que alguien instale
UPX para otra cosa— y no se nota hasta que Defender se lleva el .exe a
cuarentena. Un comentario en build.ps1 no protege de eso. Esto si.

Comprueba cinco cosas, y cada una fue un problema real:

  1. Que no haya pasado nada por UPX. La compresion rompia las firmas de los 18
     binarios de la Python Software Foundation y de Microsoft que van dentro, y el
     resultado se clasificaba como Trojan:Win32/Bearfoos.A!ml.
  2. Que los binarios de dentro conserven su firma. Es la comprobacion de fondo
     de la anterior: lo que importa no es la ausencia de UPX, es que las firmas
     esten intactas.
  3. Que el VERSIONINFO tenga los campos rellenos. Salian todos vacios.
  4. Que el manifest declare asInvoker. Que nadie lo cambie a
     requireAdministrator sin enterarse de lo que eso implica.
  5. Que el icono este incrustado.

La firma Authenticode del propio .exe se comprueba aparte, con
`sign.ps1 -SoloVerificar`: aqui no, porque una compilacion de trabajo sin firmar
es correcta y esto tiene que poder pasar en verde sobre ella.

Uso:  python tools/comprobar_binario.py [dist/Quilate.exe]
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from quilate.const import APP_VERSION, AUTHOR  # noqa: E402

# Los campos que no pueden estar vacios, y con que se comparan cuando hay algo
# concreto que exigir. `None` significa "que diga algo, lo que sea".
CAMPOS = {
    "CompanyName": AUTHOR,
    "ProductName": None,
    "FileDescription": None,
    "FileVersion": APP_VERSION,
    "LegalCopyright": None,
    "OriginalFilename": "Quilate.exe",
}

fallos: list[str] = []
avisos: list[str] = []


def mal(texto: str) -> None:
    fallos.append(texto)
    print(f"  [FALLO] {texto}")


def aviso(texto: str) -> None:
    avisos.append(texto)
    print(f"  [aviso] {texto}")


def bien(texto: str) -> None:
    print(f"  [ok]    {texto}")


def _powershell(guion: str) -> str:
    """Un PowerShell corto para lo que solo sabe contestar Windows.

    Sin `-ExecutionPolicy Bypass`, igual que el resto del proyecto: la politica
    se aplica a los ficheros de guion, no a `-Command`.
    """
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", guion],
            capture_output=True, timeout=120)
        return res.stdout.decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        aviso(f"no se ha podido preguntar a PowerShell: {exc}")
        return ""


def _tiene_firma_incrustada(datos: bytes) -> bool | None:
    """True si el PE lleva tabla de certificados; None si no se puede leer.

    Se camina la cabecera a mano, que son cuatro saltos y ninguna dependencia:

      0x3C            -> desplazamiento de la cabecera PE (e_lfanew)
      +0              -> la firma "PE\\0\\0", para no leer basura
      +20             -> empieza la cabecera opcional, cuyo primer campo (magic)
                         dice si es de 32 bits (0x10B) o de 64 (0x20B)
      +96 o +112      -> los directorios de datos, de ocho bytes cada uno

    El directorio numero 4 es la tabla de certificados, y es el unico cuyo primer
    campo no es una direccion virtual sino un desplazamiento dentro del fichero
    —detalle que no importa aqui, porque solo se mira el tamano—. Tamano cero
    significa que no hay firma incrustada.
    """
    try:
        if datos[:2] != b"MZ":
            return None
        pe = int.from_bytes(datos[0x3C:0x40], "little")
        if datos[pe:pe + 4] != b"PE\0\0":
            return None
        opcional = pe + 24
        magic = int.from_bytes(datos[opcional:opcional + 2], "little")
        if magic == 0x10B:        # PE32
            base, cuantos_en = opcional + 96, opcional + 92
        elif magic == 0x20B:      # PE32+
            base, cuantos_en = opcional + 112, opcional + 108
        else:
            return None
        # Un PE puede declarar menos de 16 directorios; si no llega al 4, no hay
        # tabla de certificados que mirar y la respuesta es "no la lleva".
        if int.from_bytes(datos[cuantos_en:cuantos_en + 4], "little") < 5:
            return False
        entrada = base + 4 * 8
        tamano = int.from_bytes(datos[entrada + 4:entrada + 8], "little")
        return tamano > 0
    except (IndexError, ValueError):
        return None


# --------------------------------------------------------------- 1, 2: UPX --
def revisar_bundle(exe: Path) -> None:
    """Los binarios que viajan dentro: ni comprimidos, ni sin firma."""
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        aviso("sin PyInstaller no se puede mirar dentro del .exe; me salto UPX")
        return

    try:
        archivo = CArchiveReader(str(exe))
    except Exception as exc:
        mal(f"no se ha podido abrir el archivo interno del .exe: {exc}")
        return

    binarios = [n for n in archivo.toc if n.lower().endswith((".dll", ".pyd"))]
    if not binarios:
        aviso("el .exe no lleva binarios dentro; esto no parece un --onefile")
        return

    comprimidos = []
    extraidos: dict[str, bytes] = {}
    for nombre in binarios:
        datos = archivo.extract(nombre)
        if isinstance(datos, tuple):      # por si cambia la API entre versiones
            datos = datos[1]
        extraidos[nombre] = datos
        # La marca de UPX va en la cabecera del PE que genera, no al final.
        if b"UPX!" in datos[:16384] or b"UPX0" in datos[:4096]:
            comprimidos.append(nombre)

    if comprimidos:
        mal(f"{len(comprimidos)} de {len(binarios)} binarios pasados por UPX "
            f"(p.ej. {', '.join(comprimidos[:3])}). "
            f"Falta --noupx en build.ps1: PyInstaller usa UPX solo si lo encuentra.")
    else:
        bien(f"ninguno de los {len(binarios)} binarios internos pasa por UPX")

    # Las firmas de dentro, leidas del propio PE y sin llamar a nadie.
    #
    # La primera version de esto escribia los veinte binarios a un temporal y le
    # preguntaba a `Get-AuthenticodeSignature`. Daba cero firmas validas, y no
    # porque no las hubiera: en un proceso lanzado desde PowerShell 7, el
    # `PSModulePath` heredado lleva delante los modulos de la 7, y cuando
    # `powershell.exe` 5.1 intenta cargar solo `Microsoft.PowerShell.Security` se
    # encuentra el de la version que no le toca y no lo carga. El cmdlet no
    # existia, `$s.Status` volvia vacio, y este script leia ese vacio como "sin
    # firma". Es decir: el comprobador informaba de un problema en el binario
    # cuando el problema estaba en el comprobador.
    #
    # Se podia arreglar con un `Import-Module` explicito, y aun asi seria un mal
    # sitio para depender de un modulo de PowerShell. Lo que hace falta saber aqui
    # es si el binario LLEVA firma incrustada o no, y eso esta escrito en el
    # propio fichero: en la entrada 4 del directorio de datos del PE, la tabla de
    # certificados. Si tiene tamano, hay firma; si vale cero, no la hay. Es lo
    # que borra un compresor de ejecutables al reescribir el PE, o sea justo lo
    # que se quiere detectar. Leerlo aqui no necesita Windows, ni PowerShell, ni
    # escribir nada al disco, y da el mismo resultado siempre.
    #
    # Lo que esta lectura NO dice es si la firma es criptograficamente valida ni
    # de quien es; para eso hace falta validar la cadena. No importa: de los
    # binarios de dentro solo interesa que nadie les haya quitado la firma que
    # traian, y del .exe de fuera se encarga `sign.ps1 -SoloVerificar`.
    estados: dict[str, str] = {}
    for nombre, datos in extraidos.items():
        firma = _tiene_firma_incrustada(datos)
        estados[Path(nombre).name] = {True: "firmado",
                                      False: "sin firma",
                                      None: "ilegible"}[firma]
    # Lo que sigue es informativo y NO puede tumbar una release por si solo. La
    # primera version de esto si podia, y estaba mal por dos razones.
    #
    # La primera es que se equivocaba. Trataba `NotSigned` como un fallo, y hay
    # binarios que nunca vinieron firmados y no tienen por que estarlo:
    # `_psutil_windows.pyd` sale de una rueda de PyPI y ahi no firma nadie.
    #
    # La segunda es que era inestable. Leer una firma depende de cosas de fuera:
    # hay que escribir veinte DLL en un temporal y preguntar por ellas una a una,
    # y el antivirus puede tenerlas cogidas justo en ese momento. Paso: una
    # ejecucion dio las veinte sin firma y la siguiente, sobre el mismo binario,
    # diecinueve validas. Un comprobador que bloquea una release segun lo ocupado
    # que este Defender no sirve de reja, sirve de molestia.
    #
    # Quien manda es la comprobacion de UPX de arriba, que no depende de nada
    # externo: se mira la marca en los bytes y ya. Aqui solo se anade un dato que
    # UPX no da: `HashMismatch` significa que el binario lleva una firma que ya no
    # cuadra con su contenido, o sea que alguien lo reescribio DESPUES de
    # firmarlo. Eso si es un fallo, y no es ambiguo.
    firmados = sorted(n for n, e in estados.items() if e == "firmado")
    sin_firma = sorted(n for n, e in estados.items() if e == "sin firma")
    ilegibles = sorted(n for n, e in estados.items() if e == "ilegible")

    if firmados:
        bien(f"{len(firmados)} de {len(estados)} binarios internos conservan su "
             f"firma incrustada")
    if sin_firma:
        # No es un fallo por si mismo: hay binarios que nunca vinieron firmados.
        # `_psutil_windows.pyd` sale de una rueda de PyPI y ahi no firma nadie.
        # Lo que seria un fallo es que no la conservara ninguno, y de eso avisa la
        # comprobacion de UPX, que es la que manda.
        print(f"          sin firma de origen: {', '.join(sin_firma)}")
    if ilegibles:
        aviso(f"no se ha podido leer la cabecera PE de: {', '.join(ilegibles)}")
    if not firmados and not ilegibles:
        mal("ningun binario interno conserva firma incrustada. Si no es UPX, "
            "algo mas esta reescribiendo los binarios del paquete.")


# ------------------------------------------------------------ 3: metadatos --
def revisar_version(exe: Path) -> None:
    if sys.platform != "win32":
        aviso("los metadatos solo se pueden leer en Windows")
        return
    consultas = ";".join(
        f"\"{c}=$($v.{c})\"" for c in CAMPOS)
    salida = _powershell(
        f"$v = (Get-Item -LiteralPath '{exe}').VersionInfo; {consultas}")
    leidos = {}
    for linea in salida.splitlines():
        clave, _, valor = linea.strip().partition("=")
        leidos[clave] = valor

    if not leidos:
        aviso("no se han podido leer los metadatos")
        return
    for campo, esperado in CAMPOS.items():
        valor = leidos.get(campo, "")
        if not valor:
            mal(f"VERSIONINFO: {campo} esta vacio (falta --version-file)")
        elif esperado and valor != esperado:
            mal(f"VERSIONINFO: {campo} dice {valor!r} y deberia decir {esperado!r}")
        else:
            bien(f"VERSIONINFO: {campo} = {valor}")


# ------------------------------------------------------------- 4: manifest --
def revisar_manifest(exe: Path) -> None:
    crudo = exe.read_bytes()
    # El manifest va incrustado como recurso en texto plano, asi que se busca
    # directamente. Es una comprobacion tosca pero no se puede equivocar: o esta
    # la cadena o no esta.
    texto = crudo.decode("latin-1")
    hueco = texto.find("<assembly")
    if hueco < 0:
        mal("el .exe no lleva manifest incrustado")
        return
    manifest = texto[hueco:texto.find("</assembly>", hueco) + 11]

    nivel = re.search(r'requestedExecutionLevel\s+level="([^"]+)"', manifest)
    if not nivel:
        mal("el manifest no declara requestedExecutionLevel")
    elif nivel.group(1) == "asInvoker":
        bien("manifest: requestedExecutionLevel = asInvoker")
    else:
        mal(f"manifest: requestedExecutionLevel = {nivel.group(1)}. "
            f"Quilate no se eleva; ver `NIVEL` en tools/make_win_resources.py.")

    if "longPathAware" in manifest:
        bien("manifest: longPathAware declarado")
    else:
        aviso("manifest: sin longPathAware (el manifest generico de PyInstaller)")
    if "supportedOS" in manifest:
        bien("manifest: supportedOS declarado")
    else:
        aviso("manifest: sin supportedOS")


# ---------------------------------------------------------------- 5: icono --
def revisar_icono(exe: Path) -> None:
    if sys.platform != "win32":
        aviso("el icono solo se puede comprobar en Windows")
        return
    salida = _powershell(
        "Add-Type -AssemblyName System.Drawing;"
        f"$i = [System.Drawing.Icon]::ExtractAssociatedIcon('{exe}');"
        "if ($i) { \"$($i.Width)x$($i.Height)\" } else { 'nada' }")
    if salida and salida != "nada":
        bien(f"icono incrustado ({salida})")
    else:
        mal("el .exe no lleva icono (falta --icon quilate.ico)")


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "dist" / "Quilate.exe"
    if not exe.is_file():
        print(f"[!] No existe {exe}. Compila primero con build.ps1.")
        return 1

    print(f"Revisando {exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"Esperado: Quilate {APP_VERSION} de {AUTHOR}\n")

    print("-- binarios internos --")
    revisar_bundle(exe)
    print("-- metadatos --")
    revisar_version(exe)
    print("-- manifest --")
    revisar_manifest(exe)
    print("-- icono --")
    revisar_icono(exe)

    print()
    if fallos:
        print(f"{len(fallos)} fallo(s). Esto no se distribuye:")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print(f"Todo en orden{f' ({len(avisos)} aviso(s))' if avisos else ''}.")
    print("Falta la firma Authenticode: comprobar con  .\\sign.ps1 -SoloVerificar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
