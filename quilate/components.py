"""Ficha por componente.

Une, pieza a pieza, el inventario + la nota medida + las mejoras que le
corresponden. Es la vista que responde a "que tengo, que tal va y que puedo
hacer con ello" sin tener que cruzar tres secciones a mano.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .audit import SEGURIDAD, SEVERITY_ORDER, Auditor, Finding
from .benchmark import BenchResult, Benchmark, SCORE_CAP, WEIGHTS
from .console import grade, human_bytes
from .projection import combine_gains
from .sensors import temperature_source
from .sysinfo import KIND_LABELS, SystemInfo, gpu_label, local_volumes


# (clave, etiqueta, componentes de los hallazgos, claves del benchmark)
COMPONENT_GROUPS: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    ("cpu", "Procesador", ("cpu_single", "cpu_multi"), ("cpu_single", "cpu_multi")),
    ("memory", "Memoria RAM", ("memory",), ("memory",)),
    ("disk", "Almacenamiento", ("disk",), ("disk_write", "disk_read", "disk_iops")),
    ("gpu", "Gráfica", ("gpu",), ("gpu_compute", "gpu_vram", "gpu_pcie")),
    ("network", "Red", ("network",), ()),
    ("system", "Sistema y software", ("system",), ()),
]

COMPONENT_TO_GROUP = {"cpu_single": "cpu", "cpu_multi": "cpu", "memory": "memory",
                      "disk": "disk", "gpu": "gpu", "system": "system"}

# El campo `component` de un hallazgo existe para la proyección: dice a qué score
# sintético se aplica la ganancia. En la ficha, en cambio, lo que importa es
# dónde se lee mejor, y no siempre coinciden. `gpu_driver` es "system" porque no
# hay ninguna medida de GPU que corregir actualizando el driver; los hallazgos de
# red también, porque no hay nota sintética de red a la que aplicarles la
# ganancia. Se reasignan por id, sin tocar la proyección.
FINDING_GROUP_OVERRIDES = {"gpu_driver": "gpu",
                           "wifi_downgrade": "network", "wifi_signal": "network",
                           "wifi_banda": "network", "ethernet_lento": "network",
                           "dns_lento": "network", "red_perdida": "network"}


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
    # Dos listas y no una, porque son dos cosas que se miden con reglas
    # distintas. `findings` son mejoras: prometen rendimiento y tienen ganancia
    # estimada. `riesgos` son de seguridad: no aceleran nada, van con `gain=0.0`
    # y se ordenan por gravedad. Juntarlas obligaba a titular el bloque entero
    # «Mejoras aplicables», que sobre «SMB1 activo» o «sin cifrar el disco» es
    # sencillamente falso.
    findings: list[Finding] = field(default_factory=list)
    riesgos: list[Finding] = field(default_factory=list)
    gain: float = 0.0
    projected_score: float | None = None


def _component_specs(key: str, si: SystemInfo, bench: Benchmark | None,
                     auditor: Auditor) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    if key == "cpu":
        out.append(("Modelo", si.cpu_name or "Desconocido"))
        out.append(("Núcleos / hilos", f"{si.cpu_cores} / {si.cpu_threads}"))
        base = si.cpu_base_mhz or si.cpu_max_mhz
        if base:
            # Es el reloj base, no el máximo: Win32_Processor no publica el boost.
            out.append(("Frecuencia base", f"{base:.0f} MHz"))
        cargada = bench.freq_under_load if bench else None
        if cargada is None:
            samples = bench.freq_samples if bench else []
            cargada = statistics.median(samples) if samples else None
        if cargada:
            if bench and "nominal" in bench.freq_source:
                detalle = f"{cargada:.0f} MHz (nominal: este equipo no expone la real)"
            else:
                pct = f"  ·  {cargada / base * 100:.0f}% de la base" if base else ""
                detalle = f"{cargada:.0f} MHz{pct}"
            out.append(("Frecuencia bajo carga", detalle))
        temps = bench.thermal_samples if bench else []
        if temps:
            source = temperature_source()
            out.append(("Temperatura bajo carga",
                        f"{max(temps):.0f} °C" + (f"  ·  {source}" if source else "")))
        else:
            out.append(("Temperatura bajo carga",
                        "sin sensor accesible (instala LibreHardwareMonitor)"))
        if bench and bench.scaling_efficiency is not None:
            out.append(("Escalado multihilo", f"{bench.scaling_efficiency:.0f}%"))
        if bench:
            for key_metric in ("sustained", "freq_under_load"):
                m = bench.metrics.get(key_metric)
                if m:
                    out.append((m["label"], f"{m['value']} {m['unit']}".strip()))

    elif key == "memory":
        total_gb = si.ram_total / 1024**3
        out.append(("Capacidad total", f"{total_gb:.1f} GB"))
        if si.ram_total:
            used_pct = (1 - si.ram_available / si.ram_total) * 100
            out.append(("Libre al medir", f"{human_bytes(si.ram_available)} "
                                          f"({used_pct:.0f}% en uso)"))
        if si.ram_speed_mhz:
            rated = si.ram_speed_rated_mhz
            out.append(("Velocidad", f"{si.ram_speed_mhz} MT/s"
                        + (f"  ·  módulos de {rated} MT/s, perfil XMP/EXPO sin activar"
                           if rated and rated > si.ram_speed_mhz else "")))
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
        if bench and bench.memory_hierarchy:
            out.append(("Jerarquía de caché",
                        " · ".join(f"{lv['level']} {lv['gbs']:.0f} GB/s"
                                   for lv in bench.memory_hierarchy)))

    elif key == "disk":
        out.append(("Unidad de sistema", f"{si.system_drive}  ·  {si.system_drive_media}"))
        for p in si.physical_disks:
            out.append(("Disco físico",
                        f"{p['name']}  ·  {p['media']}  ·  {p['bus']}  ·  "
                        f"{human_bytes(p['size'])}  ·  salud {p['health'] or 'n/d'}"))
        for d in local_volumes(si):
            out.append((f"Volumen {d['mount']}",
                        f"{human_bytes(d['free'])} libres de {human_bytes(d['total'])} "
                        f"({100 - d['percent']:.0f}% libre)  [{d['fstype']}]"))
        for d in si.disks:
            if d["ignored"] and d["total"] > 5 * 1024**3:
                out.append((f"Volumen {d['mount']}",
                            f"{d['label'] or 'sin etiqueta'} — {KIND_LABELS.get(d['kind'], d['kind'])}"
                            f", excluido de la auditoría"))
        if bench and bench.metrics.get("disk_latency"):
            m = bench.metrics["disk_latency"]
            out.append((m["label"], f"{m['value']} {m['unit']}"))

    elif key == "gpu":
        for g in si.gpus:
            out.append(("Adaptador", gpu_label(g)))
            age = g.get("driver_age_days")
            out.append(("  Driver", f"{g.get('driver')}  ·  {g.get('driver_date') or 'fecha n/d'}"
                                    + (f"  ({age} días)" if age is not None else "")))
            if g.get("vram"):
                detail = human_bytes(g["vram"])
                if g.get("vram_used"):
                    detail += f"  ·  {human_bytes(g['vram_used'])} en uso"
                if g.get("vram_source"):
                    detail += f"  ·  según {g['vram_source']}"
                out.append(("  VRAM", detail))
            if g.get("temperature") is not None:
                live = f"{g['temperature']:.0f} °C"
                if g.get("utilization") is not None:
                    live += f"  ·  {g['utilization']:.0f}% de uso"
                if g.get("clock_mhz"):
                    live += f"  ·  {g['clock_mhz']:.0f} MHz"
                if g.get("power_w"):
                    live += f"  ·  {g['power_w']:.0f} W"
                out.append(("  Estado actual", live))
            if g.get("resolution"):
                out.append(("  Modo de pantalla", str(g["resolution"])))
        # Cuál de las gráficas se puso a calcular. En un portátil con integrada y
        # dedicada hay dos adaptadores en el inventario y solo una da la nota:
        # sin esta línea, la puntuación no dice a qué pieza pertenece.
        medida = (bench.gpu_info if bench else {}) or {}
        if medida.get("device"):
            dev = medida["device"]
            detalle = dev.get("name") or "sin nombre"
            if dev.get("compute_units"):
                detalle += f"  ·  {dev['compute_units']} unidades de cómputo"
            if dev.get("clock_mhz"):
                detalle += f"  ·  {dev['clock_mhz']:.0f} MHz"
            out.append(("Medida por OpenCL", detalle))
            if dev.get("driver"):
                out.append(("  Runtime OpenCL", str(dev["driver"])))
            if medida.get("compute_iters"):
                out.append(("  Vueltas del kernel",
                            f"{medida['compute_iters']:,}".replace(",", ".")
                            + "  ·  calibradas para este dispositivo"))
        elif bench and getattr(bench, "gpu_unavailable", ""):
            # «Sin nota» y «no se ha podido medir» no son lo mismo, y la razón
            # concreta (sin OpenCL, driver roto, GPU ocupada) es lo accionable.
            out.append(("Medida por OpenCL", f"no medible: {bench.gpu_unavailable}"))

    elif key == "network":
        red = getattr(auditor, "network", {}) or {}
        for a in red.get("connected") or []:
            velocidad = f"{a['link_mbps']:,.0f} Mbps" if a.get("link_mbps") else "sin negociar"
            out.append(("Enlace activo", f"{a['name']}  ·  {velocidad}  ·  {a['description']}"))
        wifi = red.get("wifi") or {}
        if wifi:
            detalle = [wifi.get("radio") or "wifi"]
            if wifi.get("band_ghz"):
                detalle.append(f"{wifi['band_ghz']} GHz")
            if wifi.get("channel"):
                detalle.append(f"canal {wifi['channel']}")
            if wifi.get("rssi_dbm") is not None:
                detalle.append(f"{wifi['rssi_dbm']} dBm ({wifi.get('signal_pct', '?')}%)")
            out.append(("Wifi", "  ·  ".join(detalle)))
        latencia = red.get("latency") or {}
        for destino in latencia.get("targets") or []:
            marca = (f"{destino['median_ms']:.1f} ms" if destino["median_ms"]
                     else "sin respuesta")
            if destino.get("jitter_ms"):
                marca += f"  ·  ±{destino['jitter_ms']:.1f} ms"
            if destino["loss_pct"]:
                marca += f"  ·  {destino['loss_pct']}% de pérdida"
            out.append((f"  Latencia a {destino['name']}", marca))
        dns = red.get("dns") or {}
        if dns.get("median_ms"):
            out.append(("Resolución DNS", f"{dns['median_ms']:.1f} ms de mediana"
                        + (f"  ·  {dns['failures']} fallos de {dns['queried']}"
                           if dns.get("failures") else "")))
        elif red and not red.get("active"):
            out.append(("Latencia y DNS", "no medidos (--no-net)"))

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
        arranque = getattr(auditor, "boot_seconds", None)
        if arranque:
            out.append(("Duración del arranque", f"{arranque:.0f} s  ·  medido por Windows"))
        elif getattr(auditor, "boot_report", {}).get("error"):
            out.append(("Duración del arranque", "no medible (requiere administrador)"))
        startup = getattr(auditor, "startup_items", [])
        if startup:
            on = sum(1 for i in startup if i.get("enabled"))
            off = len(startup) - on
            out.append(("Programas de inicio",
                        f"{on} activos" + (f"  ·  {off} desactivados" if off else "")))
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

        # El corte es por categoría y no por qué comprobación lo emitió:
        # `check_antivirus` vive entre las de seguridad pero `av_stack` es un
        # hallazgo de fluidez —dos motores en tiempo real cuestan E/S— y su
        # sitio son las mejoras, con su ganancia y todo.
        del_grupo = [f for f in auditor.findings if finding_group(f) == key]
        card.findings = sorted([f for f in del_grupo if f.category != SEGURIDAD],
                               key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.gain))
        card.riesgos = sorted([f for f in del_grupo if f.category == SEGURIDAD],
                              key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.title))
        card.gain = combine_gains([f.gain for f in card.findings if f.gain > 0])
        if card.score is not None:
            card.projected_score = card.score * (1 + card.gain)

        if card.specs or card.tests or card.findings or card.riesgos:
            cards.append(card)
    return cards


def _no_score_text(card: ComponentCard) -> str:
    """Distingue «no medido en esta ejecución» de «no tiene medida posible»."""
    return "sin medir en esta ejecución" if card.measurable else "sin nota sintética"
