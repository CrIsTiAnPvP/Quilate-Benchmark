"""Interfaz de linea de comandos y orquestacion de la ejecucion."""

from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from pathlib import Path

from .audit import Auditor
from .benchmark import Benchmark
from .compare import RunLoadError, compare_runs, load_run
from .compare_report import print_comparison
from .console import (C, banner, clear_screen, configure_output, enable_ansi,
                      read_key, section, spinner_done, spinner_step)
from .const import APP_NAME, AUTHOR, IS_WINDOWS, WEBSITE_URL
from .export import build_payload, export_html, export_json, export_plan
from . import elevacion
from .platform_utils import is_admin
from .projection import project_improvement
from .report import print_report
from .storage_scan import ScanResult, default_roots, scan_large_files
from .history import append as history_append, report as history_report
from .history_report import print_history
from .network import DESTINOS as NET_TARGETS, collect as collect_network
from .sysinfo import collect_system_info


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="quilate",
        description=f"{APP_NAME} — benchmark, auditoría y estimación de mejora. "
                    f"{AUTHOR} · {WEBSITE_URL}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Ejemplos:\n"
               "  python quilate.py\n"
               "  python quilate.py --quick --no-color\n"
               "  python quilate.py --disk-size 1024 --html informe.html --export-plan\n"
               "  python quilate.py --compare antes.json despues.json\n",
    )
    p.add_argument("--compare", nargs=2, metavar=("ANTES", "DESPUÉS"),
                   help="comparar dos JSON de ejecuciones anteriores y salir "
                        "(no mide nada: contrasta lo ya medido)")
    p.add_argument("--history", action="store_true",
                   help="mostrar el histórico local de ejecuciones y su deriva, y salir")
    p.add_argument("--no-history", action="store_true",
                   help="no guardar esta ejecución en el histórico local")
    p.add_argument("--quick", action="store_true", help="benchmark rápido (menor precisión)")
    p.add_argument("--no-bench", action="store_true", help="solo auditoría, sin benchmark")
    p.add_argument("--no-disk", action="store_true", help="omitir las pruebas de disco")
    p.add_argument("--no-gpu", action="store_true",
                   help="omitir las pruebas de GPU (cómputo, VRAM y PCIe por OpenCL)")
    p.add_argument("--disk-size", type=int, default=512, metavar="MB",
                   help="tamaño del fichero de prueba de disco (por defecto 512)")
    p.add_argument("--disk-path", metavar="RUTA", default=None,
                   help="carpeta donde hacer el test de disco (por defecto: temporal del sistema)")
    p.add_argument("--no-net", action="store_true",
                   help="no medir latencia ni DNS (evita contactar con resolutores "
                        "públicos: 1.1.1.1, 8.8.8.8, 9.9.9.9)")
    p.add_argument("--check-drivers", action="store_true",
                   help="consultar en línea (Windows Update) si hay drivers más nuevos; "
                        "tarda 10-30 s")
    p.add_argument("--check-updates", action="store_true",
                   help="consultar en línea (Windows Update) si faltan actualizaciones de "
                        "seguridad; tarda 10-30 s")
    p.add_argument("--no-files", action="store_true",
                   help="omitir el rastreo de archivos grandes")
    p.add_argument("--scan-path", metavar="RUTA", action="append", default=None,
                   help="carpeta extra a rastrear (repetible; por defecto perfil y disco de sistema)")
    p.add_argument("--scan-time", type=float, default=30.0, metavar="SEG",
                   help="presupuesto de tiempo del rastreo de archivos (por defecto 30 s)")
    p.add_argument("--min-file-size", type=int, default=128, metavar="MB",
                   help="tamaño mínimo para considerar un archivo grande (por defecto 128 MB)")
    p.add_argument("--json", metavar="FICHERO", nargs="?", const="quilate_datos.json",
                   help="exportar los datos crudos a JSON")
    p.add_argument("--html", metavar="FICHERO", nargs="?", const="quilate_informe.html",
                   help="generar informe HTML")
    p.add_argument("--export-plan", metavar="FICHERO", nargs="?", const="plan_optimizacion.ps1",
                   help="generar script PowerShell de optimización (solo Windows)")
    p.add_argument("--elevate", action="store_true",
                   help="pedir permisos aunque no haya nadie delante para aceptarlos "
                        "(salida redirigida, tarea programada)")
    p.add_argument("--no-elevate", action="store_true",
                   help="no pedir permisos en ningún caso; las comprobaciones que los "
                        "necesitan saldrán como «sin comprobar»")
    p.add_argument("--no-color", action="store_true", help="desactivar colores ANSI")
    return p.parse_args()


def _interactive() -> bool:
    """True si hay un usuario delante: entrada y salida son un terminal."""
    try:
        return bool(sys.stdin and sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, OSError):
        return False


# Lo que se pierde diciendo que no, en el orden en que se echa de menos. No es
# una lista decorativa: pedir permisos sin decir para qué es lo que enseña a
# aceptar cualquier aviso sin leerlo.
_LO_QUE_NECESITA_PERMISOS = (
    "si el disco está cifrado y si el arranque seguro y el TPM están puestos",
    "si sigue activo SMB1, el protocolo de red que usó WannaCry",
    "cuánto tarda de verdad en arrancar y qué lo retrasa",
    "la salud fina de los discos: desgaste, horas y sectores defectuosos",
)


def _pedir_permisos(args: argparse.Namespace) -> None:
    """Pide una vez los permisos con los que se lee el lote, o dice por qué no.

    Quilate ya no se eleva entero. Antes pedía UAC al arrancar y a partir de ahí
    todo —el banco de pruebas, el rastreo de archivos, la escritura del informe—
    corría como administrador, que era mucho más de lo que hacía falta y dejaba
    los informes con propietario Administrador. Ahora el aviso sirve para un
    proceso aparte que lee ocho cosas y muere; ver `elevacion`.

    Se pide aquí, antes del inventario, y no cuando hagan falta: un diálogo de
    Windows a los dos minutos, cuando quien lo lanzó ya se ha ido a otra cosa,
    se queda esperando a nadie y tira por tierra media auditoría.
    """
    if not IS_WINDOWS or is_admin():
        return
    if args.no_elevate:
        print(f"  {C.DIM}Sin pedir permisos (--no-elevate): unas comprobaciones "
              f"quedarán sin respuesta.{C.RESET}")
        return
    if not _interactive() and not args.elevate:
        # Un aviso de UAC en una tarea programada o con la salida redirigida se
        # queda ahí parado hasta que alguien lo cierre. Mejor no sacarlo.
        print(f"  {C.DIM}Sin pedir permisos: no hay nadie delante que pueda "
              f"aceptarlos. Con --elevate se piden igualmente.{C.RESET}")
        return

    print(f"\n  {C.CYAN}▸{C.RESET} Windows va a pedirte permiso. Es para mirar, "
          f"{C.BOLD}solo leyendo{C.RESET}, cuatro cosas que de otro modo no se ven:")
    for cosa in _LO_QUE_NECESITA_PERMISOS:
        print(f"      {C.DIM}·{C.RESET} {cosa}")
    print(f"    {C.DIM}Si dices que no, el análisis sigue igual y esas salen "
          f"como «sin comprobar».{C.RESET}")

    elevacion.permitir_uac(True)
    lote = elevacion.recoger()
    contestadas = sum(1 for res in lote.values() if res.ok)
    if not contestadas:
        print(f"  {C.DIM}Sin permisos; se continúa sin ellos.{C.RESET}")
    else:
        print(f"  {C.GREEN}✓{C.RESET} Permisos concedidos "
              f"{C.DIM}(el proceso con permisos ya ha terminado).{C.RESET}")


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


def _run_comparison(rutas: list[str]) -> int:
    antes_path, despues_path = Path(rutas[0]), Path(rutas[1])
    try:
        antes, despues = load_run(antes_path), load_run(despues_path)
        comparacion = compare_runs(antes, despues)
    except RunLoadError as exc:
        return _no_se_puede_comparar(str(exc))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        # `load_run` criba lo que sabe cribar, pero el fichero viene de fuera y
        # puede faltarle cualquier otra cosa. Que eso salga como una traza de
        # Python, justo debajo del mensaje cuidado que ya existe para el fichero
        # que no es de Quilate, no tiene defensa.
        #
        # `AttributeError` es la red del esquema anidado: el módulo indexa dos y
        # tres niveles, y donde esperaba un objeto puede haber una cadena. Cribar
        # cada rincón del esquema no compensa; que ninguno saque una traza, sí.
        campo = exc.args[0] if isinstance(exc, KeyError) else exc
        return _no_se_puede_comparar(f"el fichero es de Quilate pero le faltan "
                                     f"datos: {campo}")
    print_comparison(comparacion, antes_path.name, despues_path.name)
    return 0


def _no_se_puede_comparar(motivo: str) -> int:
    print(f"\n  {C.RED}No se puede comparar: {motivo}{C.RESET}")
    print(f"  {C.DIM}Genera los ficheros con `quilate --json antes.json`, aplica los "
          f"cambios y vuelve a ejecutar con otro nombre.{C.RESET}\n")
    return 2


def main() -> int:
    args = parse_args()
    configure_output()
    clear_screen()
    enable_ansi()
    if args.no_color:
        C.disable()

    if args.history:
        print_history(history_report())
        return 0

    if args.compare:
        # Comparar no mide nada, así que ni inventario, ni benchmark, ni
        # permisos: se contrasta lo que ya está guardado y se sale.
        return _run_comparison(args.compare)

    banner()
    _pedir_permisos(args)

    section("Recopilando información del sistema")
    spinner_step("Inventario de hardware y SO".ljust(38))
    t0 = time.perf_counter()
    si = collect_system_info()
    spinner_done(f"{time.perf_counter() - t0:.1f} s")

    bench: Benchmark | None = None
    if not args.no_bench:
        bench = Benchmark(quick=args.quick, disk_size_mb=args.disk_size,
                          skip_disk=args.no_disk, target_dir=args.disk_path,
                          skip_gpu=args.no_gpu)
        try:
            bench.run_all()
        except KeyboardInterrupt:
            print(f"\n  {C.YELLOW}Benchmark interrumpido; se continúa con lo medido.{C.RESET}")

    scan: ScanResult | None = None
    if not args.no_files:
        roots = args.scan_path or default_roots(si.system_drive)
        section("Rastreo de almacenamiento")
        spinner_step(f"Archivos de más de {args.min_file_size} MB".ljust(38))
        try:
            scan = scan_large_files(roots, min_size=args.min_file_size * 1024**2,
                                    time_budget=args.scan_time)
            detail = (f"{scan.scanned_files:,} ficheros en {scan.elapsed:.0f} s"
                      f"{' · presupuesto agotado' if scan.truncated else ''}")
            spinner_done(detail, ok=not scan.truncated)
        except KeyboardInterrupt:
            print(f"\n  {C.YELLOW}Rastreo interrumpido.{C.RESET}")
        except Exception as exc:
            spinner_done(f"no se ha podido rastrear: {_motivo(exc)}", ok=False)

    section("Red")
    spinner_step("Enlace y adaptadores".ljust(38))
    try:
        red = collect_network(active=not args.no_net)
        enlace = next((a for a in red.get("connected", [])), None)
        spinner_done(f"{enlace['name']} · {enlace['link_mbps']:.0f} Mbps" if enlace
                     and enlace.get("link_mbps") else "sin enlace detectado",
                     ok=bool(enlace))
    except Exception as exc:
        red = {}
        spinner_done(f"no se ha podido consultar la red: {_motivo(exc)}", ok=False)
    if args.no_net:
        print(f"  {C.DIM}Latencia y DNS omitidas por --no-net.{C.RESET}")
    else:
        print(f"  {C.DIM}Latencia y DNS medidas contra resolutores públicos "
              f"({', '.join(h for h, _, _ in NET_TARGETS)}). Se cronometra el saludo "
              f"TCP; no se envía ningún dato. Omítelas con --no-net.{C.RESET}")

    auditor = Auditor(si, bench, scan, check_drivers=args.check_drivers, network=red,
                      check_updates=args.check_updates)
    try:
        auditor.run()
    except KeyboardInterrupt:
        print(f"\n  {C.YELLOW}Auditoría interrumpida.{C.RESET}")

    projection = project_improvement(bench, auditor.findings)
    print_report(si, bench, auditor, projection)

    # El histórico se guarda solo si hubo benchmark: una entrada sin puntuación
    # no aporta nada a una serie y ensuciaría las tendencias.
    if bench and bench.results and not args.no_history:
        destino = history_append(build_payload(si, bench, auditor, projection))
        if destino:
            print(f"  {C.DIM}Guardado en el histórico local ({destino}). "
                  f"Míralo con --history.{C.RESET}\n")

    # --- Exportaciones ---
    outputs = _exportaciones(args, si, bench, auditor, projection)
    if outputs:
        section("Ficheros generados")
        for o in outputs:
            print(f"  {C.GREEN}✓{C.RESET} {o}")
        if args.export_plan and IS_WINDOWS:
            print(f"\n  {C.YELLOW}Revisa el script antes de ejecutarlo. Cada bloque pide "
                  f"confirmación y el primero crea un punto de restauración.{C.RESET}")
        print()
    else:
        _export_menu(si, bench, auditor, projection)
    return 0


# --- Menú final ------------------------------------------------------------

DEFAULT_NAMES = {
    "html": "quilate_informe.html",
    "json": "quilate_datos.json",
    "plan": "plan_optimizacion.ps1",
}

_menu_shown = False   # lo consulta _wait_before_closing(): ver su docstring


def _menu_line(key: str, label: str, target: str, done: Path | None = None) -> None:
    mark = f"{C.GREEN}✓{C.RESET}" if done else " "
    # La columna de la tecla se pauta a 7 para que "[Enter]" no desalinee al resto.
    print(f"  {mark} {C.GOLD}{f'[{key}]'.ljust(7)}{C.RESET} "
          f"{label.ljust(18)} {C.DIM}{target}{C.RESET}")


def _exportar(kind: str, path: Path, si, bench, auditor, projection) -> str:
    """Escribe una exportación en la ruta dada y devuelve su detalle.

    Único sitio que sabe qué función genera cada tipo. Antes había dos listas
    paralelas de llamadas —la del menú y la de las banderas— y solo la del menú
    estaba protegida contra un fallo de escritura.
    """
    if kind == "html":
        export_html(path, si, bench, auditor, projection)
        return ""
    if kind == "json":
        export_json(path, si, bench, auditor, projection)
        return ""
    count = export_plan(path, si, bench, auditor)
    return f"{count} bloque{'s' if count != 1 else ''} automatizable" \
           f"{'s' if count != 1 else ''}"


def _exportaciones(args, si, bench, auditor, projection) -> list[str]:
    """Los ficheros pedidos por bandera, con el mismo cuidado que los del menú.

    Aquí no hay reserva a la carpeta personal como en `_write_export`: la ruta
    la ha elegido el usuario y escribir en otro sitio sería desobedecerle. Lo
    que sí se comparte es que un disco lleno o una carpeta sin permiso no salgan
    como una traza de Python después de haber corrido el análisis entero, y que
    el fallo de un fichero no se lleve por delante a los otros dos.
    """
    peticiones = [("json", "JSON", args.json), ("html", "HTML", args.html)]
    if args.export_plan:
        if IS_WINDOWS:
            peticiones.append(("plan", "PLAN", args.export_plan))
        else:
            print(f"  {C.YELLOW}--export-plan solo está disponible en Windows.{C.RESET}")

    outputs: list[str] = []
    for kind, etiqueta, ruta in peticiones:
        if not ruta:
            continue
        destino = Path(ruta)
        try:
            detalle = _exportar(kind, destino, si, bench, auditor, projection)
        except OSError as exc:
            print(f"  {C.RED}✗{C.RESET} No se ha podido escribir {destino}: "
                  f"{_motivo(exc)}.")
            continue
        outputs.append(f"{etiqueta}  → {destino.resolve()}"
                       + (f"  ({detalle})" if detalle else ""))
    return outputs


def _write_export(kind: str, si, bench, auditor, projection) -> tuple[Path, str] | None:
    """Genera un fichero y devuelve (ruta, detalle), o None si no se pudo.

    Se intenta primero en el directorio actual y, si no hay permiso —tipico si
    el .exe se ejecuta desde Archivos de programa—, en la carpeta del usuario,
    en vez de dar el fallo por perdido.
    """
    name = DEFAULT_NAMES[kind]
    error: OSError | None = None
    seen: list[Path] = []
    for base in (Path.cwd(), Path.home()):
        if base in seen:
            continue
        seen.append(base)
        path = base / name
        try:
            return path, _exportar(kind, path, si, bench, auditor, projection)
        except OSError as exc:
            error = exc
    # Nombrar las dos ubicaciones intentadas: tras dos fallos, decir solo «no se
    # pudo escribir» deja al usuario sin saber dónde se probó ni qué hacer.
    probadas = "\n".join(f"      · {ruta}" for ruta in seen)
    bandera = {"html": "--html", "json": "--json", "plan": "--plan"}[kind]
    print(f"{C.RED}✗{C.RESET}\n    No se ha podido escribir {name}: {_motivo(error)}.")
    print(f"    {C.DIM}Se ha intentado en:\n{probadas}\n"
          f"      Dile tú dónde con `{bandera} C:\\ruta\\que\\elijas\\{name}`.{C.RESET}")
    return None


def _export_menu(si, bench, auditor, projection) -> None:
    """Ofrece generar los informes cuando no se pidió ninguno por línea de comandos.

    Al abrir el .exe con doble clic no hay forma de pasar flags: el análisis se
    veía en pantalla y se perdía al cerrar la ventana. Aquí se generan los mismos
    ficheros que `--html`, `--json` y `--export-plan` reutilizando lo que ya está
    medido en memoria, sin repetir el benchmark.
    """
    global _menu_shown
    if not _interactive():
        return

    actions = [("h", "Informe HTML", "html"), ("j", "Datos JSON", "json")]
    if IS_WINDOWS:
        actions.append(("p", "Plan PowerShell", "plan"))
    keys = {key: kind for key, _, kind in actions}
    done: dict[str, Path] = {}
    _menu_shown = True

    section("Guardar el análisis")
    print(f"  {C.DIM}No se pidió ningún fichero por línea de comandos. Puedes generarlos\n"
          f"  ahora, con las medidas que ya están hechas.{C.RESET}\n")
    while True:
        for key, label, kind in actions:
            _menu_line(key, label, DEFAULT_NAMES[kind], done.get(kind))
        _menu_line("t", "Generar todo", "los ficheros de arriba")
        if "html" in done:
            _menu_line("a", "Abrir el informe", "en el navegador")
        _menu_line("Enter", "Salir", "cerrar Quilate")
        print(f"\n  {C.BOLD}Elige una opción:{C.RESET} ", end="", flush=True)

        try:
            key = read_key()
        except KeyboardInterrupt:
            key = "\x1b"
        print()

        if key in ("", "\x1b", "s", "q"):
            print()
            return
        if key == "a" and "html" in done:
            try:
                webbrowser.open(done["html"].as_uri())
            except Exception as exc:                      # navegador ausente o sin sesión
                print(f"  {C.YELLOW}No se pudo abrir el navegador: {exc}{C.RESET}")
            print()
            continue

        pending = [kind for _, _, kind in actions] if key == "t" else [keys.get(key)]
        if not any(pending):
            print(f"  {C.DIM}Opción no reconocida.{C.RESET}\n")
            continue

        for kind in pending:
            if kind is None:
                continue
            print(f"  {C.CYAN}▸{C.RESET} Generando {DEFAULT_NAMES[kind]} ", end="", flush=True)
            result = _write_export(kind, si, bench, auditor, projection)
            if result is None:
                continue
            path, detail = result
            done[kind] = path
            extra = f"  {C.DIM}({detail}){C.RESET}" if detail else ""
            print(f"{C.GREEN}✓{C.RESET} {path.resolve()}{extra}")
            if kind == "plan":
                print(f"    {C.YELLOW}Revísalo antes de ejecutarlo: cada bloque pide "
                      f"confirmación y el primero crea un punto de restauración.{C.RESET}")
        print()


def _wait_before_closing() -> None:
    """Al ejecutar el .exe con doble clic, la consola se cierra sola en cuanto
    termina el proceso y no da tiempo a leer nada. Solo aplica al empaquetado y
    con terminal interactivo: en línea de comandos o redirigido, estorbaría.

    Si el menú final llegó a mostrarse, la pausa ya la puso él y salir de allí es
    un acto deliberado del usuario: encadenar otro "pulsa Enter" sobraría.

    Ya no hay caso de «nos hemos relanzado elevados y esta ventana sobra»: el
    análisis entero ocurre aquí, y lo único que se va a otro proceso es el lote
    de consultas con permisos, que dura dos segundos y no imprime nada."""
    if _menu_shown or not getattr(sys, "frozen", False):
        return
    try:
        if _interactive():
            input(f"\n{C.DIM}Pulsa Enter para cerrar...{C.RESET}")
    except (EOFError, OSError):
        pass


def run() -> int:
    """Punto de entrada: envuelve main() para salir limpio con Ctrl+C."""
    try:
        return main()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Cancelado por el usuario.{C.RESET}\n")
        return 130
    finally:
        _wait_before_closing()
