"""Ficha por componente.

Une, pieza a pieza, el inventario + la nota medida + las mejoras que le
corresponden. Es la vista que responde a "que tengo, que tal va y que puedo
hacer con ello" sin tener que cruzar tres secciones a mano.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .audit import Auditor, Finding, SEVERITY_ORDER
from .benchmark import BenchResult, Benchmark, SCORE_CAP, WEIGHTS
from .console import grade, human_bytes
from .projection import combine_gains
from .sysinfo import SystemInfo


# (clave, etiqueta, componentes de los hallazgos, claves del benchmark)
COMPONENT_GROUPS: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    ("cpu", "Procesador", ("cpu_single", "cpu_multi"), ("cpu_single", "cpu_multi")),
    ("memory", "Memoria RAM", ("memory",), ("memory",)),
    ("disk", "Almacenamiento", ("disk",), ("disk_write", "disk_read", "disk_iops")),
    ("gpu", "Gráfica", (), ()),
    ("system", "Sistema y software", ("system",), ()),
]

COMPONENT_TO_GROUP = {"cpu_single": "cpu", "cpu_multi": "cpu", "memory": "memory",
                      "disk": "disk", "system": "system"}

# El campo `component` de un hallazgo existe para la proyección: dice a qué score
# sintético se aplica la ganancia. gpu_driver es "system" porque no hay ninguna
# medida de GPU que corregir. En la ficha, en cambio, el sitio natural para leerlo
# es junto a la gráfica, así que se reasigna por id sin tocar la proyección.
FINDING_GROUP_OVERRIDES = {"gpu_driver": "gpu"}


def finding_group(f: Finding) -> str:
    return FINDING_GROUP_OVERRIDES.get(f.id) or COMPONENT_TO_GROUP.get(f.component, "system")


@dataclass
class ComponentCard:
    key: str
    label: str
    specs: list[tuple[str, str]] = field(default_factory=list)
    tests: list[BenchResult] = field(default_factory=list)
    measurable: bool = False   # ¿tiene nota sintética cuando el benchmark corre?
    score: float | None = None
    letter: str = "—"
    findings: list[Finding] = field(default_factory=list)
    gain: float = 0.0
    projected_score: float | None = None


def _component_specs(key: str, si: SystemInfo, bench: Benchmark | None,
                     auditor: Auditor) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    if key == "cpu":
        out.append(("Modelo", si.cpu_name or "Desconocido"))
        out.append(("Núcleos / hilos", f"{si.cpu_cores} / {si.cpu_threads}"))
        if si.cpu_max_mhz:
            out.append(("Frecuencia máxima", f"{si.cpu_max_mhz:.0f} MHz"))
        samples = bench.freq_samples if bench else []
        if samples:
            sustained = statistics.median(samples)
            pct = f" ({sustained / si.cpu_max_mhz * 100:.0f}% del máximo)" if si.cpu_max_mhz else ""
            out.append(("Frecuencia sostenida", f"{sustained:.0f} MHz{pct}"))
        temps = bench.thermal_samples if bench else []
        out.append(("Temperatura bajo carga", f"{max(temps):.0f} °C" if temps else "no disponible"))
        if bench and bench.scaling_efficiency is not None:
            out.append(("Escalado multihilo", f"{bench.scaling_efficiency:.0f}%"))

    elif key == "memory":
        total_gb = si.ram_total / 1024**3
        out.append(("Capacidad total", f"{total_gb:.1f} GB"))
        if si.ram_total:
            used_pct = (1 - si.ram_available / si.ram_total) * 100
            out.append(("Libre al medir", f"{human_bytes(si.ram_available)} "
                                          f"({used_pct:.0f}% en uso)"))
        if si.ram_speed_mhz:
            out.append(("Velocidad", f"{si.ram_speed_mhz} MT/s"))
        sticks = [s for s in si.ram_sticks if s["capacity"] > 0]
        if sticks:
            out.append(("Módulos instalados",
                        f"{len(sticks)}" + (" (single channel)" if len(sticks) == 1 else "")))
            for s in sticks:
                desc = human_bytes(s["capacity"])
                if s["speed"]:
                    desc += f" @ {s['speed']} MT/s"
                extra = " ".join(x for x in (s["vendor"], s["part"]) if x)
                out.append((f"  {s['slot']}", f"{desc}{'  ·  ' + extra if extra else ''}"))
        else:
            out.append(("Módulos instalados", "sin datos (requiere Windows)"))

    elif key == "disk":
        out.append(("Unidad de sistema", f"{si.system_drive}  ·  {si.system_drive_media}"))
        seen: set[tuple] = set()
        for d in si.disks:
            for c in d.get("candidates", []):
                ident = (c.get("name"), c.get("media"), c.get("health"))
                if ident in seen or not c.get("name"):
                    continue
                seen.add(ident)
                media = c.get("media")
                out.append(("Disco físico", f"{c['name']}  ·  {media or 'tipo n/d'}"
                                            f"  ·  salud {c.get('health') or 'n/d'}"))
        for d in si.disks:
            if d["total"] > 5 * 1024**3:
                out.append((f"Volumen {d['mount']}",
                            f"{human_bytes(d['free'])} libres de {human_bytes(d['total'])} "
                            f"({100 - d['percent']:.0f}% libre)  [{d['fstype']}]"))

    elif key == "gpu":
        for g in si.gpus:
            out.append(("Adaptador", str(g.get("name"))))
            age = g.get("driver_age_days")
            out.append(("  Driver", f"{g.get('driver')}  ·  {g.get('driver_date') or 'fecha n/d'}"
                                    + (f"  ({age} días)" if age is not None else "")))
            if g.get("vram"):
                out.append(("  VRAM declarada", human_bytes(g["vram"])))
            if g.get("resolution"):
                out.append(("  Modo de pantalla", str(g["resolution"])))

    elif key == "system":
        out.append(("Sistema operativo", si.os_name))
        out.append(("Versión", si.os_build))
        if si.os_install_date:
            out.append(("Instalado el", f"{si.os_install_date}  "
                                        f"({(si.os_age_days or 0) / 365.25:.1f} años)"))
        if si.bios_date:
            out.append(("BIOS", si.bios_date))
        out.append(("Tiempo encendido", f"{si.uptime_hours:.1f} h"))
        out.append(("Formato", "portátil" if si.is_laptop else "sobremesa"))
        startup = getattr(auditor, "startup_items", [])
        if startup:
            out.append(("Programas de inicio", str(len(startup))))
        top = getattr(auditor, "top_processes", [])
        if top:
            out.append(("Procesos más pesados",
                        ", ".join(f"{p['name']} ({human_bytes(p['rss'])})" for p in top[:3])))
        out.append(("Privilegios", "administrador" if si.is_admin else "usuario estándar"))
        out.append(("Python", si.python_version))

    return out


def build_component_cards(si: SystemInfo, bench: Benchmark | None,
                          auditor: Auditor) -> list[ComponentCard]:
    comp_scores = bench.component_scores() if bench else {}
    cards: list[ComponentCard] = []
    for key, label, comps, bench_keys in COMPONENT_GROUPS:
        card = ComponentCard(key=key, label=label)
        card.measurable = any(k in WEIGHTS for k in comps)
        card.specs = _component_specs(key, si, bench, auditor)
        if bench:
            card.tests = [bench.results[k] for k in bench_keys if k in bench.results]

        scored = {k: comp_scores[k] for k in comps if k in comp_scores}
        if scored:
            # Mismo techo y pesos que la nota global, para que las notas por
            # componente sumen exactamente la puntuación total.
            total_w = sum(WEIGHTS[k] for k in scored)
            card.score = sum(min(v, SCORE_CAP) * WEIGHTS[k] for k, v in scored.items()) / total_w
            card.letter = grade(card.score)[0]

        card.findings = sorted([f for f in auditor.findings if finding_group(f) == key],
                               key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.gain))
        card.gain = combine_gains([f.gain for f in card.findings if f.gain > 0])
        if card.score is not None:
            card.projected_score = card.score * (1 + card.gain)

        if card.specs or card.tests or card.findings:
            cards.append(card)
    return cards


def _no_score_text(card: ComponentCard) -> str:
    """Distingue «no medido en esta ejecución» de «no tiene medida posible»."""
    return "sin medir en esta ejecución" if card.measurable else "sin nota sintética"
