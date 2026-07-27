"""Interfaz de linea de comandos y orquestacion de la ejecucion."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from .audit import Auditor
from .benchmark import Benchmark
from .console import (C, banner, enable_ansi, section, spinner_done,
                      spinner_step)
from .const import APP_NAME, AUTHOR, IS_WINDOWS, WEBSITE_URL
from .export import export_html, export_json, export_plan
from .platform_utils import is_admin
from .projection import project_improvement
from .report import print_report
from .storage_scan import ScanResult, default_roots, scan_large_files
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
               "  python quilate.py --disk-size 1024 --html informe.html --export-plan\n",
    )
    p.add_argument("--quick", action="store_true", help="benchmark rápido (menor precisión)")
    p.add_argument("--no-bench", action="store_true", help="solo auditoría, sin benchmark")
    p.add_argument("--no-disk", action="store_true", help="omitir las pruebas de disco")
    p.add_argument("--disk-size", type=int, default=512, metavar="MB",
                   help="tamaño del fichero de prueba de disco (por defecto 512)")
    p.add_argument("--disk-path", metavar="RUTA", default=None,
                   help="carpeta donde hacer el test de disco (por defecto: temporal del sistema)")
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
    p.add_argument("--no-color", action="store_true", help="desactivar colores ANSI")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    enable_ansi()
    if args.no_color:
        C.disable()

    banner()
    if not is_admin():
        print(f"\n  {C.YELLOW}⚠ Sin permisos de administrador: algunas comprobaciones "
              f"(SMART, TRIM, servicios) pueden no estar disponibles.{C.RESET}")
        print(f"  {C.DIM}Para un análisis completo, abre la terminal como administrador.{C.RESET}")

    section("Recopilando información del sistema")
    spinner_step("Inventario de hardware y SO".ljust(38))
    t0 = time.perf_counter()
    si = collect_system_info()
    spinner_done(f"{time.perf_counter() - t0:.1f} s")

    bench: Benchmark | None = None
    if not args.no_bench:
        bench = Benchmark(quick=args.quick, disk_size_mb=args.disk_size,
                          skip_disk=args.no_disk, target_dir=args.disk_path)
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

    auditor = Auditor(si, bench, scan)
    try:
        auditor.run()
    except KeyboardInterrupt:
        print(f"\n  {C.YELLOW}Auditoría interrumpida.{C.RESET}")

    projection = project_improvement(bench, auditor.findings)
    print_report(si, bench, auditor, projection)

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
    return 0


def run() -> int:
    """Punto de entrada: envuelve main() para salir limpio con Ctrl+C."""
    try:
        return main()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Cancelado por el usuario.{C.RESET}\n")
        return 130
