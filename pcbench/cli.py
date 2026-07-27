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
from .sysinfo import collect_system_info


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pcbench",
        description=f"{APP_NAME} — benchmark, auditoría y estimación de mejora. "
                    f"{AUTHOR} · {WEBSITE_URL}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Ejemplos:\n"
               "  python pcbench.py\n"
               "  python pcbench.py --quick --no-color\n"
               "  python pcbench.py --disk-size 1024 --html informe.html --export-plan\n",
    )
    p.add_argument("--quick", action="store_true", help="benchmark rápido (menor precisión)")
    p.add_argument("--no-bench", action="store_true", help="solo auditoría, sin benchmark")
    p.add_argument("--no-disk", action="store_true", help="omitir las pruebas de disco")
    p.add_argument("--disk-size", type=int, default=512, metavar="MB",
                   help="tamaño del fichero de prueba de disco (por defecto 512)")
    p.add_argument("--disk-path", metavar="RUTA", default=None,
                   help="carpeta donde hacer el test de disco (por defecto: temporal del sistema)")
    p.add_argument("--json", metavar="FICHERO", nargs="?", const="pcbench_datos.json",
                   help="exportar los datos crudos a JSON")
    p.add_argument("--html", metavar="FICHERO", nargs="?", const="pcbench_informe.html",
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

    auditor = Auditor(si, bench)
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
