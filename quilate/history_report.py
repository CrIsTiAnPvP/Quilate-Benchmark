"""Salida por consola del histórico de ejecuciones."""

from __future__ import annotations

from typing import Any

from .console import BOX_W, C, _wrap, banner, section
from .history import MINIMO_PARA_TENDENCIA


def _chispograma(valores: list[float], ancho: int = 28) -> str:
    """Serie dibujada con bloques. Cabe en una línea y basta para ver la forma:
    si sube, si baja, o si es una nube de puntos sin dirección."""
    if len(valores) < 2:
        return ""
    muestras = valores[-ancho:]
    bajo, alto = min(muestras), max(muestras)
    if alto == bajo:
        return "▄" * len(muestras)
    barras = "▁▂▃▄▅▆▇█"
    return "".join(barras[int((v - bajo) / (alto - bajo) * (len(barras) - 1))]
                   for v in muestras)


def print_history(datos: dict[str, Any]) -> None:
    banner()
    section("Histórico de ejecuciones")
    print(f"  {C.DIM}{datos['path']}{C.RESET}")

    if not datos["runs"]:
        print(f"\n  {C.YELLOW}Todavía no hay ninguna ejecución guardada.{C.RESET}")
        print(f"  {C.DIM}Cada análisis completo añade una línea. Con "
              f"{MINIMO_PARA_TENDENCIA} ya se pueden buscar tendencias.{C.RESET}\n")
        return

    print(f"  {datos['runs']} ejecuciones · de {datos['first_at'][:10]} "
          f"a {datos['last_at'][:10]}\n")

    if not datos["enough"]:
        faltan = datos["needed"] - datos["runs"]
        print(f"  {C.YELLOW}Aún no hay suficientes para hablar de tendencia: "
              f"faltan {faltan}.{C.RESET}")
        print(f"  {C.DIM}Con dos puntos siempre se puede trazar una recta, y no "
              f"significa nada.{C.RESET}\n")

    if datos["series"]:
        print(f"  {'SERIE'.ljust(24)}{'INICIO'.rjust(9)}{'AHORA'.rjust(9)}"
              f"{'CAMBIO'.rjust(9)}   FORMA")
        print(f"  {C.GREY}{'─' * (BOX_W - 2)}{C.RESET}")
        entradas = datos["entries"]
        for s in datos["series"]:
            valores = [float(e[s["key"]]) for e in entradas
                       if isinstance(e.get(s["key"]), (int, float))]
            color = {"degradación": C.RED, "mejora": C.GREEN}.get(s["direction"], C.GREY)
            cambio = f"{s['change_pct']:+.1f}%"
            print(f"  {s['label'][:24].ljust(24)}{s['baseline']:>9.1f}{s['current']:>9.1f}"
                  f"{color}{cambio:>9}{C.RESET}   {C.DIM}{_chispograma(valores)}{C.RESET}")
        print(f"\n  {C.DIM}El signo ya está corregido: «+» siempre es mejor, también "
              f"en arranque y temperatura,\n  donde el número baja.{C.RESET}")

    if datos["degrading"]:
        print(f"\n  {C.RED}Va a peor:{C.RESET}")
        for s in datos["degrading"]:
            unidad = "s" if s["key"] == "boot_seconds" else "°C" if s["key"] == "cpu_temp" else "pts"
            print(f"    {C.BOLD}{s['label']}{C.RESET}: {s['baseline']:.1f} → "
                  f"{s['current']:.1f} {unidad} ({s['change_pct']:+.1f}%) "
                  f"{C.DIM}sobre {s['samples']} ejecuciones{C.RESET}")
        for line in _wrap(
                "Una degradación sostenida no se arregla con ajustes: suele ser el disco "
                "consumiendo vida útil, la pasta térmica degradándose o software que se "
                "ha ido acumulando. Compara dos ejecuciones concretas con --compare para "
                "ver en qué prueba está el cambio.", 72):
            print(f"  {C.DIM}{line}{C.RESET}")
    elif datos["enough"]:
        print(f"\n  {C.GREEN}Ninguna serie se está degradando.{C.RESET}")
    print()
