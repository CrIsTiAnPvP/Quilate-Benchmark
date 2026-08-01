"""Lo que tiene que seguir siendo cierto del .exe que se distribuye.

Estos tests no comprueban lo que hace Quilate: comprueban cómo sale empaquetado,
que es lo que se rompió. El `.exe` compilado se clasificaba como
`Trojan:Win32/Bearfoos.A!ml` y Windows Defender se lo llevaba a cuarentena. La
causa no estaba escrita en ningún sitio —PyInstaller usa UPX en cuanto lo
encuentra en el `PATH`, sin pedir permiso ni avisar— y la compresión dejaba sin
firma los diecinueve binarios de la Python Software Foundation y de Microsoft que
van dentro del paquete.

Un fallo que se activa solo, con instalar una herramienta que no tiene nada que
ver, y que no se nota hasta que el antivirus borra el resultado, necesita una
reja escrita. Es esto.

Casi todo es independiente del sistema y se ejecuta también en Linux, que es
donde corre la mayor parte de la integración continua. Lo que necesita Windows de
verdad va marcado, y lo cubre el trabajo `importar-en-windows`.
"""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from quilate.const import APP_VERSION, AUTHOR, IS_WINDOWS

RAIZ = Path(__file__).resolve().parent.parent


def _cargar(nombre: str):
    """Importa un módulo de `tools/`, que no es un paquete y no se puede importar.

    Se hace con `importlib` y no metiendo `tools/` en `sys.path` porque eso
    dejaría sus nombres colgando para el resto de la sesión de pruebas.
    """
    ruta = RAIZ / "tools" / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(f"_tools_{nombre}", ruta)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


recursos = _cargar("make_win_resources")
comprobador = _cargar("comprobar_binario")


# ============================================================== el build.ps1 ==
#
# Se lee el guion de compilación como texto. Es tosco, y es a propósito: lo que
# hay que garantizar es que esas tres opciones sigan escritas ahí, y cualquier
# comprobación más lista se podría quedar conforme con un build.ps1 que ya no las
# pasa.

def _build_ps1() -> str:
    return (RAIZ / "build.ps1").read_text(encoding="utf-8", errors="replace")


def test_el_build_pasa_noupx():
    # La opción que impide volver al problema. PyInstaller comprime con UPX si lo
    # encuentra en el PATH, así que no hay que pedirlo: hay que prohibirlo.
    assert "--noupx" in _build_ps1()


def test_el_build_incrusta_los_metadatos_y_el_manifest():
    guion = _build_ps1()
    assert "--version-file" in guion
    assert "--manifest" in guion
    # Y los genera antes de usarlos, que es lo que se olvidaría: el directorio
    # `build` se borra al principio, así que generarlos antes de la limpieza los
    # dejaría escritos y borrados.
    assert guion.index("make_win_resources") < guion.index("--version-file")


def test_el_build_sigue_generando_una_aplicacion_de_consola():
    # Quilate es una herramienta de terminal. Si alguien pusiera `--windowed`,
    # el informe no se vería en ningún sitio.
    assert "--console" in _build_ps1()


# ========================================================= los dos recursos ==

def test_cuarteto_rellena_la_compilacion_con_cero():
    assert recursos.cuarteto("2.6.0") == (2, 6, 0, 0)
    assert recursos.cuarteto("1.2.3.4") == (1, 2, 3, 4)
    assert recursos.cuarteto("3") == (3, 0, 0, 0)


def test_cuarteto_no_deja_pasar_una_version_no_numerica():
    # VERSIONINFO son cuatro enteros. Un `2.7.0rc1` no cabe, y tiene que abortar
    # aquí en vez de colarse como basura dentro del recurso del ejecutable.
    for malo in ("2.7.0rc1", "2.6.0-beta", "1.2.3.4.5", ""):
        with pytest.raises(SystemExit):
            recursos.cuarteto(malo)


def test_la_version_del_recurso_sale_de_const():
    # El motivo de generar el recurso en vez de escribirlo a mano: que no pueda
    # haber un día en que el .exe declare una versión y el programa imprima otra.
    texto = recursos.version_info()
    assert f"StringStruct('FileVersion', '{APP_VERSION}')" in texto
    assert f"StringStruct('CompanyName', '{AUTHOR}')" in texto


def test_el_recurso_de_version_no_deja_ningun_campo_vacio():
    # Salían los seis vacíos, que además de quedar mal en las propiedades del
    # fichero es una señal más para los clasificadores de reputación.
    texto = recursos.version_info()
    for campo in ("CompanyName", "ProductName", "FileDescription",
                  "FileVersion", "LegalCopyright", "OriginalFilename"):
        assert f"StringStruct('{campo}', ''" not in texto, f"{campo} vacío"
        assert campo in texto


def test_el_recurso_de_version_lo_entiende_pyinstaller():
    # El fichero es un DSL que PyInstaller evalúa. Que sea texto plausible no
    # basta: si no se puede evaluar, la compilación falla al final, después de
    # los tres minutos de análisis.
    pyinstaller = pytest.importorskip(
        "PyInstaller.utils.win32.versioninfo",
        reason="sin PyInstaller no hay DSL que evaluar")
    entorno = {n: getattr(pyinstaller, n) for n in dir(pyinstaller)
               if not n.startswith("_")}
    evaluado = eval(compile(recursos.version_info(), "version_info", "eval"), entorno)
    assert type(evaluado).__name__ == "VSVersionInfo"


# --------------------------------------------------------- flujo de release --
#
# La versión viaja por una cadena de cuatro eslabones y cada uno tiene que decir
# lo mismo que el anterior:
#
#     etiqueta git ─ quilate/const.py ─ VERSIONINFO ─ el .exe compilado
#
# Los tres últimos ya están cubiertos: `version_info()` sale de `APP_VERSION`
# (arriba), y `comprobar_binario.py` verifica sobre el binario ya compilado que
# el recurso llegó. El primero es el que no puede comprobarse desde dentro del
# programa, porque la etiqueta solo existe en el repositorio: lo comprueba el
# flujo de release, y esto comprueba que el flujo lo sigue comprobando.

FLUJO_RELEASE = RAIZ / ".github" / "workflows" / "release.yml"


def test_el_flujo_de_release_contrasta_la_etiqueta_con_la_version():
    # Sin esta comprobación, `git tag v2.8.0` con APP_VERSION todavía en 2.7.0
    # publica una release titulada 2.8.0 cuyo binario se identifica como 2.7.0
    # en sus propiedades, en `--version` y en cada informe que genera. No falla
    # nada y no lo nota nadie: la versión deja de significar algo.
    texto = FLUJO_RELEASE.read_text(encoding="utf-8")
    assert "GITHUB_REF_NAME" in texto, "el flujo no mira la etiqueta"
    assert "APP_VERSION" in texto, "el flujo no lee la versión del código"
    assert "exit 1" in texto, "el flujo detecta el desajuste pero no falla"


def test_el_flujo_de_release_publica_la_huella():
    # El `.exe` no se firma, así que la huella es lo único que permite comprobar
    # que lo descargado es lo que se publicó. Tiene que ir en dos sitios: el
    # fichero suelto, para `sha256sum -c`, y el cuerpo de la release, porque
    # quien descarga no se baja un segundo fichero para comparar a ojo.
    texto = FLUJO_RELEASE.read_text(encoding="utf-8")
    assert "Quilate.exe.sha256" in texto
    assert "body_path" in texto, "la release no lleva notas propias"


def test_el_flujo_de_release_no_renombra_los_ficheros_publicados():
    # quilate.cristianac.es enlaza a /releases/latest/download/Quilate.exe y a
    # su .sha256. Esa ruta exige el nombre exacto y no admite comodines, así que
    # meter la versión en el nombre del fichero deja el botón de descarga de la
    # web devolviendo un 404 sin que falle nada en este repositorio. La versión
    # va en el título de la release, en su cuerpo y dentro del propio binario.
    texto = FLUJO_RELEASE.read_text(encoding="utf-8")
    publicados = texto.split("Adjuntar a la release", 1)[1]
    assert "dist/Quilate.exe\n" in publicados
    assert "dist/Quilate.exe.sha256" in publicados


def test_el_manifest_es_xml_valido():
    # Un manifest que no analiza no lo rechaza PyInstaller: lo incrusta, y es
    # Windows quien se niega a arrancar el programa.
    ET.fromstring(recursos.manifest())


def test_el_manifest_declara_asinvoker_y_no_pide_administrador():
    # Esto es deliberado y va contra la solución que parece obvia, así que el
    # test existe para que nadie lo cambie por descuido. Quilate no se eleva: el
    # proceso principal corre sin privilegios y lo único que se eleva es un
    # PowerShell aparte que lee once cosas y muere. Ponerle requireAdministrator
    # devolvería el programa entero a integridad alta, que es de lo que el
    # proyecto se alejó a propósito —los informes salían con propietario
    # Administrador— y encima no arreglaría la detección: un proceso elevado hace
    # que Defender vigile más, no menos. El razonamiento largo está en `NIVEL`,
    # en tools/make_win_resources.py.
    assert recursos.NIVEL == "asInvoker"
    texto = recursos.manifest()
    assert 'level="asInvoker"' in texto
    assert "requireAdministrator" not in texto
    assert "highestAvailable" not in texto


def test_el_manifest_declara_el_sistema_y_las_rutas_largas():
    texto = recursos.manifest()
    assert "supportedOS" in texto
    assert "longPathAware" in texto


def test_la_version_del_manifest_cuadra_con_la_del_programa():
    raiz = ET.fromstring(recursos.manifest())
    identidad = raiz.find("{urn:schemas-microsoft-com:asm.v1}assemblyIdentity")
    assert identidad is not None
    esperado = ".".join(str(n) for n in recursos.cuarteto(APP_VERSION))
    assert identidad.get("version") == esperado


# ================================================== la lectura de la firma ==
#
# `_tiene_firma_incrustada` lee la tabla de certificados del PE a mano. Se prueba
# con cabeceras construidas aquí y no con ficheros reales porque tiene que poder
# ejecutarse en Linux, donde no hay ningún binario de Windows firmado a mano.


def _pe(tamano_firma: int, magic: int = 0x20B, directorios: int = 16) -> bytes:
    """Un PE mínimo con la tabla de certificados declarando ese tamaño."""
    inicio_pe = 0x80
    datos = bytearray(0x400)
    datos[0:2] = b"MZ"
    datos[0x3C:0x40] = inicio_pe.to_bytes(4, "little")
    datos[inicio_pe:inicio_pe + 4] = b"PE\0\0"
    opcional = inicio_pe + 24          # 4 de la firma + 20 de la cabecera COFF
    datos[opcional:opcional + 2] = magic.to_bytes(2, "little")
    desplazamiento = 108 if magic == 0x20B else 92
    datos[opcional + desplazamiento:opcional + desplazamiento + 4] = \
        directorios.to_bytes(4, "little")
    base = opcional + (112 if magic == 0x20B else 96)
    entrada = base + 4 * 8             # el directorio 4 es la tabla de certificados
    datos[entrada + 4:entrada + 8] = tamano_firma.to_bytes(4, "little")
    return bytes(datos)


def test_la_tabla_de_certificados_con_tamano_es_una_firma():
    assert comprobador._tiene_firma_incrustada(_pe(0x1800)) is True
    assert comprobador._tiene_firma_incrustada(_pe(0x1800, magic=0x10B)) is True


def test_sin_tabla_de_certificados_no_hay_firma():
    # Es lo que deja un compresor de ejecutables al reescribir el PE, y es
    # exactamente lo que hay que detectar.
    assert comprobador._tiene_firma_incrustada(_pe(0)) is False


def test_un_pe_que_declara_pocos_directorios_no_lleva_firma():
    # Un PE puede declarar menos de dieciséis directorios. Si no llega al cuarto,
    # no hay tabla de certificados que mirar, y la respuesta correcta es «no la
    # lleva», no reventar leyendo bytes de otro sitio.
    assert comprobador._tiene_firma_incrustada(_pe(0x1800, directorios=2)) is False


def test_lo_que_no_es_un_pe_no_se_adivina():
    # `None` es «no he podido leerlo», que no es lo mismo que «no está firmado».
    # Confundirlos fue justo el fallo de la primera versión del comprobador: leía
    # una respuesta vacía y la contaba como binario sin firma.
    for basura in (b"", b"no soy un PE", b"MZ", b"MZ" + b"\0" * 200):
        assert comprobador._tiene_firma_incrustada(basura) is None


def test_la_marca_de_upx_no_se_confunde_con_una_firma():
    # Las dos comprobaciones son independientes: una mira la tabla de
    # certificados y la otra busca la marca del compresor. Un binario puede no
    # tener firma sin llevar UPX (las ruedas de PyPI), y eso no es un fallo.
    limpio = _pe(0)
    assert b"UPX!" not in limpio[:16384]


# ============================================ el icono de la consola ==

from quilate import icono  # noqa: E402  (después de las utilidades de arriba)


def test_los_anfitriones_son_cadenas_distintas():
    valores = {icono.CONHOST, icono.TERMINAL, icono.SIN_CONSOLA, icono.OTRO}
    assert len(valores) == 4


def test_anfitrion_contesta_algo_conocido():
    assert icono.anfitrion() in {icono.CONHOST, icono.TERMINAL,
                                 icono.SIN_CONSOLA, icono.OTRO}


@pytest.mark.skipif(IS_WINDOWS, reason="describe lo que pasa fuera de Windows")
def test_fuera_de_windows_no_se_toca_nada():
    assert icono.anfitrion() == icono.SIN_CONSOLA
    assert icono.aplicar() == "solo Windows"


@pytest.mark.skipif(not IS_WINDOWS, reason="la consola de Windows es de Windows")
def test_bajo_windows_terminal_no_se_intenta_y_se_dice_por_que(monkeypatch):
    # Comprobado ejecutándolo dentro de una pestaña de verdad: ahí
    # `GetConsoleWindow` devuelve una ventana oculta de ConPTY, y `SetConsoleIcon`
    # sobre ella contestaría que todo ha ido bien sin que se vea ningún cambio.
    # Eso es peor que no hacer nada, porque es una respuesta falsa. Así que no se
    # intenta y se devuelve el motivo.
    monkeypatch.setattr(icono, "anfitrion", lambda: icono.TERMINAL)
    motivo = icono.aplicar()
    assert "Terminal" in motivo
    # Devuelve un motivo, no una cadena vacia: la cadena vacia significaria que se
    # ha puesto el icono, y bajo Terminal no se pone.
    assert motivo != ""


@pytest.mark.skipif(not IS_WINDOWS, reason="la consola de Windows es de Windows")
def test_sin_ventana_de_consola_no_se_intenta(monkeypatch):
    monkeypatch.setattr(icono, "anfitrion", lambda: icono.SIN_CONSOLA)
    assert icono.aplicar() == "no hay ventana de consola"


@pytest.mark.skipif(IS_WINDOWS, reason="describe lo que pasa fuera de Windows")
def test_el_titulo_fuera_de_windows_no_se_toca():
    assert icono.poner_titulo() == "solo Windows"


@pytest.mark.skipif(not IS_WINDOWS, reason="el titulo de la consola es de Windows")
def test_el_titulo_se_pone_en_los_dos_anfitriones(monkeypatch):
    # Esta es la diferencia con el icono, y es la mitad del problema que se ve al
    # abrir el .exe con doble clic: `SetConsoleTitleW` funciona TAMBIEN en Windows
    # Terminal, porque el titulo de la pestana sigue al del proceso. Comprobado
    # dentro de los dos anfitriones de verdad: la pestana pasa de llamarse con la
    # ruta del ejecutable a llamarse con el nombre y la version del programa.
    #
    # Por eso `poner_titulo` no pregunta por el anfitrion y `aplicar` si.
    monkeypatch.setattr(icono, "anfitrion", lambda: icono.TERMINAL)
    assert icono.poner_titulo() in ("", "no hay ventana de consola")


def test_el_titulo_por_defecto_lleva_el_nombre_y_la_version():
    # Se construye con APP_NAME y APP_VERSION y no con una cadena escrita a mano,
    # por lo mismo que el VERSIONINFO: para que no pueda quedarse atras.
    from quilate.const import APP_NAME
    assert APP_NAME in f"{APP_NAME} {APP_VERSION}"


def test_poner_titulo_nunca_levanta_una_excepcion(monkeypatch):
    monkeypatch.setattr(icono, "_ventana_de_consola", lambda: 0)
    assert isinstance(icono.poner_titulo(), str)


def test_aplicar_nunca_levanta_una_excepcion(monkeypatch):
    # Se llama sin mirar el resultado, al principio del arranque. Un icono no es
    # motivo para que el programa no arranque.
    monkeypatch.setattr(icono, "anfitrion", lambda: icono.CONHOST)
    monkeypatch.setattr(icono, "_origen_del_icono",
                        lambda: Path("no/existe/quilate.ico"))
    assert isinstance(icono.aplicar(), str)


# ================================== el lanzamiento del proceso elevado ==

@pytest.mark.skipif(not IS_WINDOWS,
                    reason="fuera de Windows la consulta elevada se ataja antes")
def test_el_powershell_elevado_no_lleva_las_palabras_de_siempre(monkeypatch):
    """La reja de la limpieza del Problema 2.

    `-ExecutionPolicy Bypass` no hacía nada: la política de ejecución gobierna los
    ficheros de guion, y aquí se pasa `-EncodedCommand`, que no es un fichero.
    `-WindowStyle Hidden` era redundante, porque la ventana ya se crea oculta con
    `nShow = SW_HIDE` en ShellExecuteEx. Las dos juntas dejaban en la línea de
    órdenes de un proceso que acaba de pasar por UAC el retrato exacto de lo que
    buscan las reglas de detección de PowerShell abusivo, y a cambio de nada.
    """
    from quilate import elevacion

    capturado = []

    def falso_lanzar(exe, parametros):
        capturado.append((exe, parametros))
        return False        # como un UAC denegado: no se espera a ninguna tubería

    monkeypatch.setattr(elevacion, "_lanzar_elevado", falso_lanzar)
    elevacion.consulta_elevada({"prueba": "Get-Date"}, timeout=1)

    assert len(capturado) == 1
    exe, parametros = capturado[0]

    # Sigue siendo el powershell del sistema, por ruta absoluta. Lo cubre
    # test_binarios.py, y se reafirma aquí porque esto es lo que se eleva.
    assert exe.lower().endswith("powershell.exe")

    # Lo que ya no está.
    assert "ExecutionPolicy" not in parametros
    assert "Bypass" not in parametros
    assert "WindowStyle" not in parametros

    # Y lo que sí tiene que seguir estando. `-EncodedCommand` se queda: no es
    # ofuscación, es UTF-16LE en base64, que es el mecanismo documentado para que
    # el entrecomillado no destroce el guion por el camino.
    assert "-EncodedCommand" in parametros
    assert "-NoProfile" in parametros
    assert "-NonInteractive" in parametros


@pytest.mark.skipif(not IS_WINDOWS,
                    reason="fuera de Windows la consulta elevada se ataja antes")
def test_el_latido_llega_hasta_la_espera(monkeypatch):
    """La espera avisa de que sigue viva mientras el proceso con permisos trabaja.

    Sin esto, entre aceptar el UAC y recibir la respuesta pasaban hasta treinta
    segundos sin que se imprimiera nada, y una línea a medias que no avanza no se
    distingue de un cuelgue. Aquí se dice que sí al UAC pero no se lanza a nadie,
    así que la espera se agota entera: el latido tiene que haber sonado varias
    veces antes de rendirse.
    """
    from quilate import elevacion

    monkeypatch.setattr(elevacion, "_lanzar_elevado", lambda *a: True)
    latidos = []
    lote = elevacion.consulta_elevada({"prueba": "Get-Date"}, timeout=1,
                                      latido=lambda: latidos.append(1))

    # Con un plazo de 1 s y un latido cada 0,2 s salen unos cinco. Se pide «más
    # de uno» y no un número exacto porque el reparto depende de lo ocupada que
    # esté la máquina, y un test que dependa de eso falla solo de vez en cuando.
    assert len(latidos) > 1, f"solo ha latido {len(latidos)} vez/veces"
    assert not lote["prueba"].ok
    assert "no ha contestado" in lote["prueba"].error


@pytest.mark.skipif(not IS_WINDOWS, reason="_ps_raw se ataja fuera de Windows")
def test_las_consultas_sin_elevar_tampoco_llevan_la_politica(monkeypatch):
    # Son unas cuantas por ejecución, y la palabra salía en todas.
    from quilate import platform_utils

    capturado = []

    def falso_run_cmd(args, **kwargs):
        capturado.append(args)
        return platform_utils.CmdResult("")

    monkeypatch.setattr(platform_utils, "run_cmd", falso_run_cmd)
    platform_utils._ps_raw("Get-Date")

    assert capturado
    args = capturado[0]
    assert "-ExecutionPolicy" not in args
    assert "Bypass" not in args
    assert "-NoProfile" in args
