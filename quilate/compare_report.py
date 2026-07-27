"""Salida por consola de la comparación entre dos ejecuciones."""

from __future__ import annotations

from typing import Any

from .console import BOX_W, C, _wrap, banner, section

_VERDICT_COLOR = {
    "mejora": "GREEN",
    "empeora": "RED",
    "dentro del margen": "GREY",
    "sin base": "GREY",
    "solo en una de las dos": "GREY",
}


def _color(nombre: str) -> str:
    return getattr(C, _VERDICT_COLOR.get(nombre, "GREY"), "")


def _fmt(valor: float | None, unidad: str) -> str:
    if valor is None:
        return "—"
    if unidad == "IOPS":
        return f"{valor:,.0f}"
    return f"{valor:,.2f}"


def print_comparison(cmp: dict[str, Any], nombre_antes: str, nombre_despues: str) -> None:
    banner()
    section("Comparación de dos ejecuciones")

    meta = cmp["meta"]
    print(f"  {C.DIM}antes  {C.RESET}{nombre_antes}   "
          f"{C.DIM}{meta['before']['generated_at'] or 'sin fecha'} "
          f"· v{meta['before']['version'] or '?'}{C.RESET}")
    print(f"  {C.DIM}después{C.RESET} {nombre_despues}   "
          f"{C.DIM}{meta['after']['generated_at'] or 'sin fecha'} "
          f"· v{meta['after']['version'] or '?'}{C.RESET}")
    if not meta["same_machine"]:
        print(f"\n  {C.YELLOW}⚠ No son el mismo equipo: {'; '.join(meta['machine_differences'])}."
              f"{C.RESET}")
        print(f"    {C.DIM}Sirve para contrastar dos máquinas, pero no es un antes y después."
              f"{C.RESET}")

    # --- Nota global ---
    total = cmp["overall"]
    if total["before"] and total["after"]:
        signo = "+" if (total["delta_pct"] or 0) >= 0 else ""
        color = C.GREEN if (total["delta_pct"] or 0) > 0 else C.RED
        print(f"\n  {C.BOLD}Puntuación global{C.RESET}  {total['before']:.0f} → "
              f"{C.BOLD}{total['after']:.0f} pts{C.RESET}   "
              f"{color}{signo}{total['delta_pct']:.1f}%{C.RESET}")

    # --- Prueba a prueba, con el margen decidiendo ---
    section("Prueba a prueba")
    print(f"  {C.DIM}El veredicto no lo decide la diferencia sino si supera el margen de "
          f"las dos\n  medidas. Una mejora dentro del margen no es una mejora.{C.RESET}\n")
    print(f"  {'PRUEBA'.ljust(22)}{'ANTES'.rjust(12)}{'DESPUÉS'.rjust(12)}"
          f"{'CAMBIO'.rjust(9)}{'MARGEN'.rjust(8)}   VEREDICTO")
    print(f"  {C.GREY}{'─' * (BOX_W - 2)}{C.RESET}")
    for fila in cmp["tests"]:
        delta = ("—" if fila["delta_pct"] is None
                 else f"{fila['delta_pct']:+.1f}%")
        umbral = "?" if not fila["margin_known"] else f"±{fila['threshold']:.0f}%"
        c = _color(fila["verdict"])
        print(f"  {fila['label'][:22].ljust(22)}"
              f"{_fmt(fila['before'], fila['unit']).rjust(12)}"
              f"{_fmt(fila['after'], fila['unit']).rjust(12)}"
              f"{delta.rjust(9)}{umbral.rjust(8)}   {c}{fila['verdict']}{C.RESET}")
    if any(not f["margin_known"] for f in cmp["tests"]):
        print(f"\n  {C.DIM}«?» en margen: alguna de las dos ejecuciones no registró "
              f"dispersión para esa prueba\n  (versión anterior a la 2.2, o una prueba que "
              f"no se repitió). Se supone un margen amplio\n  para no dar por buena una "
              f"diferencia pequeña.{C.RESET}")

    # --- Hallazgos ---
    section("Qué ha cambiado en la auditoría")
    h = cmp["findings"]
    for etiqueta, clave, color in (("Resueltos", "resueltos", C.GREEN),
                                   ("Nuevos", "nuevos", C.RED),
                                   ("Siguen ahí", "persisten", C.YELLOW)):
        items = h[clave]
        print(f"  {color}{etiqueta}{C.RESET} ({len(items)})")
        for f in items[:8]:
            print(f"    {C.DIM}·{C.RESET} {f.get('title', f.get('id'))}")
        if len(items) > 8:
            print(f"    {C.DIM}… y {len(items) - 8} más{C.RESET}")
        print()

    # --- Calibración de la proyección ---
    cal = cmp["calibration"]
    if cal:
        section("La proyección frente a la realidad")
        print(f"  {'Punto de partida'.ljust(30)} {cal['baseline']:>6.0f} pts")
        print(f"  {'Lo que Quilate proyectó'.ljust(30)} {cal['predicted']:>6.0f} pts   "
              f"{C.DIM}(+{cal['predicted_gain_pct']:.1f}%){C.RESET}")
        print(f"  {'Lo que se ha medido'.ljust(30)} {C.BOLD}{cal['achieved']:>6.0f} pts{C.RESET}   "
              f"{C.DIM}({cal['achieved_gain_pct']:+.1f}%){C.RESET}")
        if cal["realised"] is not None:
            pct = cal["realised"] * 100
            color = C.GREEN if pct >= 70 else C.YELLOW if pct >= 30 else C.RED
            print(f"\n  {color}Se ha materializado el {pct:.0f}% de la mejora proyectada."
                  f"{C.RESET}")
            for line in _wrap(
                    "La proyección supone que se aplican todas las recomendaciones y que "
                    "cada una rinde lo estimado. Si el porcentaje es bajo, mira primero "
                    "cuáles siguen apareciendo en la lista de arriba: lo más probable es "
                    "que no llegaran a aplicarse.", 70):
                print(f"  {C.DIM}{line}{C.RESET}")

    # --- Cobertura ---
    cob = cmp["coverage"]
    pendientes = set(cob["after"]["unverified"] or []) | set(cob["before"]["unverified"] or [])
    if pendientes:
        section("Cobertura")
        for momento, datos in (("antes", cob["before"]), ("después", cob["after"])):
            if datos["conclusive"] is None:
                print(f"  {momento.ljust(8)} {C.DIM}sin datos de cobertura (versión antigua)"
                      f"{C.RESET}")
                continue
            print(f"  {momento.ljust(8)} {datos['conclusive']}/{datos['total']} comprobaciones "
                  f"con veredicto")
        print(f"\n  {C.DIM}Sin comprobar en alguna de las dos: "
              f"{', '.join(sorted(pendientes))}.{C.RESET}")
        print(f"  {C.DIM}Un hallazgo que desaparece porque dejó de poder comprobarse no es "
              f"un hallazgo resuelto.{C.RESET}")

    # --- Condiciones de medida ---
    ruido = []
    for momento, datos in (("antes", cmp["ambient"]["before"]),
                           ("después", cmp["ambient"]["after"])):
        peor = max((v.get("cpu_pct", 0) for v in (datos or {}).values()), default=0)
        if peor:
            ruido.append(f"{momento}: {peor:.0f}% de CPU ajena")
    if ruido:
        print(f"\n  {C.DIM}Condiciones de medida → {' · '.join(ruido)}.{C.RESET}")
    print()
