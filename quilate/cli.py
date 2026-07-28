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
from .platform_utils import is_admin, owns_console, relaunch_as_admin
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
                   help="pedir permisos de administrador aunque se lance desde una terminal")
    p.add_argument("--no-elevate", action="store_true",
                   help="no pedir permisos de administrador en ningún caso")
    p.add_argument("--no-color", action="store_true", help="desactivar colores ANSI")
    return p.parse_args()


_relaunched = False   # lo consulta _wait_before_closing(): ver su docstring


def _interactive() -> bool:
    """True si hay un usuario delante: entrada y salida son un terminal."""
    try:
        return bool(sys.stdin and sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, OSError):
        return False


def _ask_elevate() -> bool:
    """Pregunta antes de relanzarse desde una terminal ya abierta.

    Aquí el aviso de UAC no basta como pregunta: aunque se acepte, el análisis
    se va a una ventana nueva y esta terminal se queda mirando. Conviene
    decirlo antes, no después.
    """
    print(f"  {C.BOLD}¿Pedirlos ahora?{C.RESET} {C.DIM}El análisis se abrirá en una "
          f"ventana nueva.{C.RESET}")
    print(f"    {C.GOLD}[Enter]{C.RESET} Sí, pedirlos    "
          f"{C.GOLD}[N]{C.RESET} No, seguir sin permisos    ", end="", flush=True)
    try:
        key = read_key()
    except KeyboardInterrupt:
        key = "n"
    print()
    return key not in ("n", "\x1b")


def _try_elevate(args: argparse.Namespace) -> int | None:
    """Ofrece relanzarse como administrador.

    Devuelve None para continuar aquí sin permisos, o el código de salida con el
    que terminar si el trabajo se ha ido a la ventana elevada.

    Sin elevación se cae media auditoría (SMART, TRIM, servicios) sin que el
    usuario sepa por qué, y con doble clic no hay forma de pedirla salvo el menú
    contextual, que nadie usa. Se pide en los dos modos, pero de distinta manera:

    - Doble clic: somos los únicos de la consola, así que se va directo al aviso
      de UAC. Aceptar sustituye esta ventana por la elevada.
    - Terminal: se pregunta primero, porque relanzarse abre otra ventana.
    - Salida redirigida a un fichero o a una tubería: no se pide nada. Ahí no hay
      quien conteste, y la ventana nueva dejaría el destino vacío.

    Solo se espera al proceso elevado cuando no hay nadie delante, es decir con
    `--elevate` desde un script: allí el código de salida es lo único que le
    queda a quien nos invocó. Con un usuario delante, esperar dejaría dos
    ventanas abiertas —una trabajando y otra mirando— durante todo el análisis,
    que además termina en un menú interactivo. Mejor ceder el turno y salir.
    """
    global _relaunched
    if args.no_elevate or not IS_WINDOWS or not getattr(sys, "frozen", False):
        return None
    double_click = owns_console()
    if not double_click and not args.elevate:
        if not _interactive() or not _ask_elevate():
            return None

    print(f"  {C.CYAN}▸{C.RESET} Pidiendo permisos a Windows... "
          f"{C.DIM}acepta el aviso para el análisis completo.{C.RESET}")
    wait = not double_click and not _interactive()
    code = relaunch_as_admin(["--no-elevate"], wait=wait)
    if code is None:
        print(f"  {C.DIM}Permisos denegados; se continúa sin ellos.{C.RESET}")
        return None
    _relaunched = True
    if not double_click:
        print(f"  {C.GREEN}✓{C.RESET} "
              + ("Análisis completado en la ventana con permisos." if wait else
                 "El análisis continúa en la ventana con permisos; "
                 "esta ya puede cerrarse.\n"))
    return code


def _run_comparison(rutas: list[str]) -> int:
    antes_path, despues_path = Path(rutas[0]), Path(rutas[1])
    try:
        antes, despues = load_run(antes_path), load_run(despues_path)
        comparacion = compare_runs(antes, despues)
    except RunLoadError as exc:
        return _no_se_puede_comparar(str(exc))
    except (KeyError, TypeError, ValueError) as exc:
        # `load_run` criba lo que sabe cribar, pero el fichero viene de fuera y
        # puede faltarle cualquier otra cosa. Que eso salga como una traza de
        # Python, justo debajo del mensaje cuidado que ya existe para el fichero
        # que no es de Quilate, no tiene defensa.
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
    if not is_admin():
        print(f"\n  {C.YELLOW}⚠ Sin permisos de administrador: algunas comprobaciones "
              f"(SMART, TRIM, servicios) pueden no estar disponibles.{C.RESET}")
        code = _try_elevate(args)
        if code is not None:
            return code
        print(f"  {C.DIM}Para un análisis completo, abre la terminal como administrador.{C.RESET}")

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
            spinner_done(f"no disponible ({type(exc).__name__})", ok=False)

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
        spinner_done(f"no disponible ({type(exc).__name__})", ok=False)
    if args.no_net:
        print(f"  {C.DIM}Latencia y DNS omitidas por --no-net.{C.RESET}")
    else:
        print(f"  {C.DIM}Latencia y DNS medidas contra resolutores públicos "
              f"({', '.join(h for h, _, _ in NET_TARGETS)}). Se cronometra el saludo "
              f"TCP; no se envía ningún dato. Omítelas con --no-net.{C.RESET}")

    auditor = Auditor(si, bench, scan, check_drivers=args.check_drivers, network=red)
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
    outputs: list[str] = []
    if args.json:
        pj = Path(args.json)
        export_json(pj, si, bench, auditor, projection)
        outputs.append(f"JSON  → {pj.resolve()}")
    if args.html:
        ph = Path(args.html)
        export_html(ph, si, bench, auditor, projection)
        outputs.append(f"HTML  → {ph.resolve()}")
    if args.export_plan:
        if not IS_WINDOWS:
            print(f"  {C.YELLOW}--export-plan solo está disponible en Windows.{C.RESET}")
        else:
            pp = Path(args.export_plan)
            count = export_plan(pp, si, bench, auditor)
            outputs.append(f"PLAN  → {pp.resolve()}  ({count} bloques automatizables)")
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
            if kind == "html":
                export_html(path, si, bench, auditor, projection)
                return path, ""
            if kind == "json":
                export_json(path, si, bench, auditor, projection)
                return path, ""
            count = export_plan(path, si, bench, auditor)
            return path, f"{count} bloque{'s' if count != 1 else ''} automatizable" \
                         f"{'s' if count != 1 else ''}"
        except OSError as exc:
            error = exc
    print(f"{C.RED}✗{C.RESET}\n    No se pudo escribir {name}: {error}")
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
    un acto deliberado del usuario: encadenar otro "pulsa Enter" sobraría. Y si
    nos hemos relanzado con permisos, esta ventana ya no pinta nada: el informe
    sale en la otra y esta debe cerrarse sola."""
    if _menu_shown or _relaunched or not getattr(sys, "frozen", False):
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
