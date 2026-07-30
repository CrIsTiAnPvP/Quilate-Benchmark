"""Cada bloque del informe: el que sabe que significan los datos.

Aqui es donde se anade una seccion nueva. Cada funcion recibe los datos ya
medidos y devuelve el HTML de su bloque, sin tocar el documento ni saber en
que orden va a colocarse: de eso se encarga el `__init__`.

Es el fichero que depende de todo lo demas —benchmark, componentes,
inventario, auditoria— porque es el unico que traduce un dato en una frase.
"""

from __future__ import annotations

from typing import Any

from ...audit import Auditor, SEVERITY_ORDER
from ...benchmark import (BUSY_CPU_PCT, Benchmark, REFERENCE, REFERENCE_DATE,
                          REFERENCE_MACHINE, REFERENCE_ORIGIN,
                          reference_age_months, reference_is_stale)
from ...components import COMPONENT_TO_GROUP, ComponentCard, _no_score_text
from ...console import COMPONENT_LABELS, grade
from ...sensors import temperature_report, temperature_source
from ...storage_scan import RECLAIMABLE, REVIEWABLE, ScanResult, candidate_bytes
from ...sysinfo import KIND_LABELS, SystemInfo, gpu_label
from .piezas import (COMPONENT_ICONS, SEVERITY_LABELS, Seccion, _e, _gauge,
                     _html_bar, _human, _icon, _logo, _score_color, _term)


def _hero(bench: Benchmark | None, auditor: Auditor, projection: dict[str, Any]) -> str:
    boxes = []
    if bench and bench.results:
        overall = bench.overall()
        letter, _ = grade(overall)
        proj = projection.get("projected_overall", overall)
        exp_pct = projection.get("experiential_pct", 0.0)
        boxes.append(f'<div class="box" style="--edge:{_score_color(overall)}">'
                     f'<div class="n" style="color:{_score_color(overall)}">{overall:.0f}</div>'
                     f'<div class="l">Puntuación actual · nota {letter}</div>'
                     f'<div class="foot">100 = equipo de gama media</div></div>')
        boxes.append(f'<div class="box" style="--edge:var(--ok)">'
                     f'<div class="n" style="color:var(--ok)">{proj:.0f}</div>'
                     f'<div class="l">Tras optimizar</div>'
                     f'<div class="foot">+{projection.get("headroom_pct", 0.0):.0f}% sobre la '
                     f"nota de hoy</div></div>")
        boxes.append(f'<div class="box" style="--edge:var(--brand)">'
                     f'<div class="n" style="color:var(--brand)">+{exp_pct:.0f}%</div>'
                     f'<div class="l">Fluidez percibida estimada</div>'
                     f'<div class="foot">lo que se nota al usarlo, no lo que sube la nota'
                     f"</div></div>")
    sin_datos = len(getattr(auditor, "unverified", []))
    graves = sum(1 for f in auditor.findings if f.severity in ("critical", "high"))
    borde = ("var(--bad)" if graves else "var(--warn)" if auditor.findings else "var(--ok)")
    pie = (f"{graves} de severidad alta o crítica" if graves
           else "ninguno grave" if auditor.findings else "nada que corregir")
    if sin_datos:
        pie += f" · {sin_datos} sin comprobar"
    boxes.append(f'<div class="box" style="--edge:{borde}"><div class="n">'
                 f'{len(auditor.findings)}</div>'
                 f'<div class="l">Hallazgos en {auditor.checks_run} pruebas concluyentes</div>'
                 f'<div class="foot">{pie}</div></div>')
    return f'<div class="hero" id="resumen">{"".join(boxes)}</div>'


def _component_strip(bench: Benchmark | None) -> str:
    """Las notas por componente, una debajo de otra y ordenadas de peor a mejor.

    Es la respuesta a «¿por dónde falla?», y para eso basta el orden y la cifra.
    La barra con la referencia marcada vive en la tabla de Benchmark, que además
    trae la medida bruta y el margen: aquí la repetía a media pantalla de
    distancia sin añadir nada que no estuviera ya en el número.
    """
    comp = bench.component_scores() if bench else {}
    if not comp:
        return ""
    peor = min(comp, key=lambda k: comp[k])
    filas = ""
    for clave, nota in sorted(comp.items(), key=lambda x: x[1]):
        letra, _ = grade(nota)
        marca = ('<span class="badge b-medium">cuello de botella</span>'
                 if clave == peor and len(comp) > 1 else "")
        filas += (f'<div class="cs-row"><div class="cs-name">'
                  f'{_icon(COMPONENT_ICONS.get(COMPONENT_TO_GROUP.get(clave, clave), "i-sys"))}'
                  f"<span>{_e(COMPONENT_LABELS.get(clave, clave))}</span>{marca}</div>"
                  f'<div class="cs-num" style="color:{_score_color(nota)}">{nota:.0f}'
                  f'<span class="cs-let">{_e(letra)}</span></div></div>')
    return (f'<div class="strip"><div class="lbl">Nota por componente</div>{filas}'
            f'<div class="hint">100 puntos son los de la referencia: un equipo de gama '
            f"media reciente. La medida de cada prueba, su margen y la comparación con "
            f'esa referencia están en <a href="#benchmark">Benchmark</a>.</div></div>')


def _findings_panel(auditor: Auditor, bench: Benchmark | None) -> str:
    """Recuento por severidad y cuello de botella.

    Aquí solo van las cifras totales. Se probó a desglosarlo por secciones y no
    cuadraba: los mismos cinco hallazgos aparecen en la ficha por componente, en
    el plan de acción y en el detalle, así que los recuentos por sección se
    solapaban y sumaban el triple del total que hay justo encima. Dónde está cada
    uno ya lo dicen los puntos de color de la navegación.
    """
    counts: dict[str, int] = {}
    for f in auditor.findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    chips = "".join(f'<span class="badge b-{_e(s)}">{n} {_e(SEVERITY_LABELS.get(s, s))}</span>'
                    # `.get` y no indexación: una severidad que no esté declarada
                    # ordena la última, pero no tira el informe entero. Es la
                    # convención del resto del proyecto; estos dos recuentos —el
                    # del HTML y el de la consola— eran los únicos que indexaban.
                    for s, n in sorted(counts.items(),
                                       key=lambda x: SEVERITY_ORDER.get(x[0], 9)))
    hint = ""
    comp = bench.component_scores() if bench else {}
    if comp:
        weakest = min(comp, key=lambda k: comp[k])
        hint = (f'<div class="hint">Cuello de botella: '
                f"<b>{_e(COMPONENT_LABELS.get(weakest, weakest))}</b> "
                f"({comp[weakest]:.0f} pts)</div>")
    return (f'<div class="panel"><div class="lbl">Hallazgos</div>'
            f'<div class="chips">{chips or "ninguno"}</div>{hint}</div>')


def _sidebar(si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
             projection: dict[str, Any], secs: list[Seccion] | None = None) -> str:
    panels = []

    if bench and bench.results:
        overall = bench.overall()
        letter, _ = grade(overall)
        delta = ""
        if projection.get("projected_overall"):
            delta = (f'<div class="delta">Tras optimizar '
                     f'<b style="color:var(--ok)">{projection["projected_overall"]:.0f} pts</b>'
                     f' · fluidez percibida +{projection.get("experiential_pct", 0):.0f}%</div>')
        # El isotipo de filigrana al fondo: es la única cifra que resume todo el
        # informe, y el sitio donde la marca no compite con ningún dato.
        panels.append(f'<div class="panel mark">{_logo("brandmark wm")}'
                      f'<div class="lbl">Puntuación global</div>'
                      f"{_gauge(overall, letter)}"
                      f'<div class="sub" style="text-align:center">La marca de arriba son '
                      f'los 100 puntos de la {_term("referencia", "referencia")}</div>'
                      f"{delta}</div>")
    else:
        panels.append('<div class="panel mark"><div class="lbl">Puntuación global</div>'
                      '<div class="sub">Ejecutado sin benchmark: informe solo de auditoría.'
                      "</div></div>")

    panels.append(_findings_panel(auditor, bench))

    ram = f"{si.ram_total / 1024**3:.1f} GB"
    if si.ram_speed_mhz:
        ram += f" @ {si.ram_speed_mhz} MT/s"
    # Cuatro datos para saber de qué equipo se está hablando mientras se lee el
    # resto, y nada más. La lista completa —disco, GPU con su driver, módulos de
    # memoria, volúmenes, BIOS— está en Inventario con bastante más detalle;
    # repetir aquí siete de esas filas con el texto recortado no era un resumen,
    # era la misma tabla peor contada.
    equipo = [
        ("i-sys", f"{si.hostname}  ·  {'portátil' if si.is_laptop else 'sobremesa'}"),
        ("i-shield", si.os_name),
        ("i-cpu", f"{si.cpu_name} ({si.cpu_cores}C/{si.cpu_threads}T)"),
        ("i-ram", ram),
    ]
    items = "".join(f"<li>{_icon(k)}<span>{_e(v)}</span></li>" for k, v in equipo)
    panels.append(f'<div class="panel"><div class="lbl">Equipo</div>'
                  f'<ul class="mini">{items}</ul>'
                  f'<p class="xref"><a href="#inventario">{_icon("i-box")}'
                  f"Inventario completo</a></p></div>")

    return f'<aside class="side">{"".join(panels)}</aside>'


def _inventory(si: SystemInfo) -> str:
    """Inventario completo, con el mismo detalle que la consola: TODOS los
    volúmenes y discos físicos, no solo la unidad de sistema."""
    rows = [
        ("Equipo", f"{si.hostname}  ·  {'portátil' if si.is_laptop else 'sobremesa'}"),
        ("Sistema operativo", f"{si.os_name} · {si.os_build}"),
    ]
    if si.os_install_date:
        rows.append(("Instalación", f"{si.os_install_date} "
                                    f"({(si.os_age_days or 0) / 365.25:.1f} años)"))
    if si.bios_date:
        rows.append(("BIOS", si.bios_date))
    rows += [
        ("Tiempo encendido", f"{si.uptime_hours:.1f} h"),
        # Es el reloj base: Win32_Processor no publica la frecuencia de boost.
        ("CPU", f"{si.cpu_name} — {si.cpu_cores} núcleos / {si.cpu_threads} hilos"
                + (f" · base {si.cpu_base_mhz or si.cpu_max_mhz:.0f} MHz"
                   if (si.cpu_base_mhz or si.cpu_max_mhz) else "")),
        ("Memoria", f"{si.ram_total / 1024**3:.1f} GB"
                    + (f" @ {si.ram_speed_mhz} MT/s" if si.ram_speed_mhz else "")
                    + (f" · {si.ram_channels} módulo(s)" if si.ram_channels else "")
                    + (f" · módulos de {si.ram_speed_rated_mhz} MT/s, perfil XMP/EXPO sin activar"
                       if si.ram_speed_rated_mhz and si.ram_speed_mhz
                       and si.ram_speed_rated_mhz > si.ram_speed_mhz else "")),
    ]
    for g in si.gpus:
        rows.append(("GPU", f"{gpu_label(g)} · driver {g.get('driver')} "
                            f"({g.get('driver_date') or 'fecha n/d'})"))
    rows += [
        ("Disco de sistema", f"{si.system_drive} · {si.system_drive_media}"),
        ("Privilegios", "administrador" if si.is_admin
                        else "usuario estándar (algunas comprobaciones limitadas)"),
        ("Python", si.python_version),
    ]
    kvs = "".join(f'<div class="k">{_e(k)}</div><div>{_e(v)}</div>' for k, v in rows)
    out = [f'<div class="card"><div class="kvs">{kvs}</div></div>']

    if si.ram_sticks:
        trs = ""
        for s in si.ram_sticks:
            marca = " ".join(x for x in (s.get("vendor"), s.get("part")) if x) or "—"
            real = s.get("speed") or 0
            nominal = s.get("rated_speed") or 0
            vel = f"{real} MT/s" if real else "—"
            if nominal and real and nominal > real:
                vel += f' <span class="tags">(soporta {nominal})</span>'
            trs += (f"<tr><td>{_e(s.get('slot') or '?')}</td>"
                    f"<td>{_human(s.get('capacity') or 0)}</td><td>{vel}</td>"
                    f"<td>{_e(marca)}</td></tr>")
        out.append('<div class="card"><div class="sub-h">Módulos de memoria</div><div class="tw">'
                   "<table><tr><th>Ranura</th><th>Capacidad</th><th>Velocidad</th>"
                   "<th>Fabricante y modelo</th></tr>" + trs + "</table></div></div>")

    phys = ""
    hay_fiabilidad = any(p.get("power_on_hours") is not None or p.get("temperature") is not None
                         for p in si.physical_disks)
    for p in si.physical_disks:
        health = str(p.get("health") or "n/d")
        color = "var(--ok)" if health.lower() in ("healthy", "sano", "0") else "var(--bad)"
        phys += (f"<tr><td>{_e(p['name'])}</td><td>{_e(p['media'])}</td>"
                 f"<td>{_e(p['bus'])}</td><td>{_human(p['size'])}</td>"
                 f'<td style="color:{color}">{_e(health)}</td>')
        if hay_fiabilidad:
            # El desgaste solo significa algo en un disco de estado sólido.
            es_ssd = "SSD" in str(p.get("media") or "").upper()
            desgaste = p.get("wear")
            horas = p.get("power_on_hours")
            grados = p.get("temperature")
            errores = (p.get("read_errors") or 0) + (p.get("write_errors") or 0)
            phys += (f"<td>{f'{desgaste}%' if es_ssd and desgaste is not None else '—'}</td>"
                     f"<td>{f'{horas:,} h'.replace(',', '.') if horas is not None else '—'}</td>"
                     f"<td>{f'{grados} °C' if grados is not None else '—'}</td>"
                     f'<td style="color:{"var(--bad)" if errores else "inherit"}">'
                     f"{errores if errores else '—'}</td>")
        phys += "</tr>"
    if phys:
        extra = ("<th>Desgaste</th><th>Horas</th><th>Temp.</th><th>Errores</th>"
                 if hay_fiabilidad else "")
        nota = ("" if hay_fiabilidad else
                '<p class="scan-note">Los contadores de desgaste, horas de uso y errores '
                "necesitan privilegios de administrador y no se han podido leer.</p>")
        out.append('<div class="card"><div class="sub-h">Discos físicos</div>' + nota
                   + '<div class="tw"><table>'
                   "<tr><th>Unidad</th><th>Tipo</th><th>Conexión</th><th>Capacidad</th>"
                   "<th>Salud</th>" + extra + "</tr>" + phys + "</table></div></div>")

    vols = ""
    for d in si.disks:
        if d["total"] <= 5 * 1024**3:
            continue
        name = f"<b>{_e(d['mount'])}</b>"
        if d["label"]:
            name += f'<div class="tags">{_e(d["label"])}</div>'
        if d["ignored"]:
            # Un volumen de nube o de red informa de un tamaño que no es del
            # equipo: se muestra para que se vea, pero sin barra ni semáforo,
            # porque «liberar espacio» ahí no significa nada.
            kind = KIND_LABELS.get(d["kind"], d["kind"])
            vols += (f"<tr><td>{name}</td><td>{_e(d['fstype'])}</td>"
                     f"<td>{_human(d['free'])}</td><td>{_human(d['total'])}</td>"
                     f'<td colspan="2"><span class="badge b-info">{_e(kind)}</span> '
                     f'<span style="color:var(--dim)">excluido de la auditoría</span></td></tr>')
            continue
        free_pct = 100 - d["percent"]
        color = "var(--bad)" if free_pct < 10 else "var(--warn)" if free_pct < 20 else "var(--ok)"
        disk = d.get("physical") or {}
        media = f'<div class="tags">{_e(disk.get("media", ""))} · {_e(disk.get("bus", ""))}</div>' \
            if disk else ""
        vols += (f"<tr><td>{name}</td><td>{_e(d['fstype'])}{media}</td>"
                 f"<td>{_human(d['free'])}</td><td>{_human(d['total'])}</td>"
                 f'<td style="color:{color}">{free_pct:.0f}% libre</td>'
                 f'<td><div class="track"><div class="fill" style="width:{d["percent"]:.0f}%;'
                 f'background:{color}"></div></div></td></tr>')
    if vols:
        out.append('<div class="card"><div class="sub-h">Volúmenes</div><div class="tw">'
                   "<table><tr><th>Unidad</th><th>Formato</th><th>Libre</th><th>Total</th>"
                   "<th>Estado</th><th>Ocupación</th></tr>" + vols + "</table></div></div>")
    return "".join(out)


def _benchmark_table(bench: Benchmark) -> str:
    dispersion = getattr(bench, "dispersion", {})
    # Las claves de dispersión de CPU no coinciden con las de resultado: los
    # cuatro subtests monohilo se agregan en una sola fila.
    equivalencias = {"cpu_single": ("sieve", "float", "hash", "compress")}
    trs = ""
    for key, r in bench.results.items():
        letter, _ = grade(r.score)
        measure = f"{r.raw:,.0f}" if r.unit == "IOPS" else f"{r.raw:,.2f}"
        detail = f'<div class="tags">{_e(r.detail)}</div>' if r.detail else ""
        relacionadas = [dispersion[k] for k in equivalencias.get(key, (key,))
                        if k in dispersion]
        margen = "—"
        if relacionadas:
            peor = max(relacionadas, key=lambda d: d["spread_pct"])
            color = "var(--dim)" if peor["stable"] else "var(--warn)"
            margen = (f'<span style="color:{color}" title="dispersión entre '
                      f'{peor["runs"]} medidas de «{_e(peor["label"])}»">'
                      f'±{peor["spread_pct"]:.0f}%</span>')
        trs += (f"<tr><td>{_e(r.name)}{detail}</td><td>{measure} {_e(r.unit)}</td>"
                f"<td>{margen}</td>"
                f"<td><b>{r.score:.0f}</b></td><td>{letter}</td>"
                f"<td>{_html_bar(r.score)}</td></tr>")
    overall = bench.overall()
    letter, _ = grade(overall)
    inestables = bench.unstable() if hasattr(bench, "unstable") else []
    aviso = ""
    if inestables:
        aviso = ('<p class="scan-note" style="color:var(--warn)">Medidas poco estables: '
                 + ", ".join(f"{_e(d['label'])} (±{d['spread_pct']:.0f}%)" for d in inestables)
                 + ". La misma prueba dio resultados distintos entre tramos, así que esas "
                   "cifras valen como orden de magnitud pero no para comparar con otra "
                   "ejecución.</p>")
    ocupada = bench.busy_during_run() if hasattr(bench, "busy_during_run") else 0.0
    if ocupada >= BUSY_CPU_PCT:
        aviso += ('<p class="scan-note" style="color:var(--warn)">El equipo no estaba en '
                  f"reposo: un {ocupada:.0f}% de CPU la consumían otros procesos con el "
                  "benchmark parado.</p>")
    # Una tabla sin filas de GPU no distingue «este equipo no tiene» de «no se
    # ha mirado». La razón concreta va aquí, junto a la ausencia.
    if getattr(bench, "gpu_unavailable", ""):
        aviso += (f'<p class="scan-note">La gráfica no se ha podido medir por '
                  f'{_term("OpenCL", "opencl")}: {_e(bench.gpu_unavailable)}. Su peso se '
                  "reparte entre el resto de componentes, así que la nota global no la "
                  "penaliza.</p>")
    return ('<div class="card"><div class="tw"><table>'
            "<tr><th>Prueba</th><th>Medida</th><th>Margen</th><th>Puntos</th><th>Nota</th>"
            "<th>Relativo a la referencia</th></tr>" + trs
            + f"<tr><td><b>Puntuación global</b></td><td></td><td></td>"
              f'<td style="color:{_score_color(overall)}"><b>{overall:.0f}</b></td>'
              f"<td><b>{letter}</b></td><td>{_html_bar(overall)}</td></tr>"
              "</table></div>"
            + aviso
            + f'<p class="scan-note">El {_term("margen", "margen")} es cuánto varió cada '
              "prueba consigo misma entre repeticiones o tramos del mismo trabajo. Un "
              "número sin margen no revela nunca que está contaminado.</p></div>")


def _howto_subcard(card: ComponentCard) -> str:
    """Subtarjeta plegada con el «cómo» de cada mejora del componente.

    Va cerrada por defecto a propósito: la ficha se lee de un vistazo para
    decidir, y solo se despliega el procedimiento del que se vaya a aplicar.
    """
    steps_total = sum(len(f.steps) for f in card.findings)
    if not steps_total:
        return ""
    blocks = ""
    for i, f in enumerate(card.findings, 1):
        if not f.steps:
            continue
        steps = "".join(f"<li>{_e(s)}</li>" for s in f.steps)
        gain = (f'<span class="gain">+{f.gain * 100:.0f}%</span> ' if f.gain else "")
        blocks += (f'<div class="howto-item"><h4>{i}. {_e(f.title)}</h4>'
                   f'<div class="tags">{gain}<span class="badge b-{_e(f.severity)}">{_e(f.severity)}'
                   f"</span> &nbsp; esfuerzo {_e(f.effort)} · riesgo {_e(f.risk)}"
                   f"{' · ' + _e(f.gain_note) if f.gain_note else ''}</div>"
                   f"<ol>{steps}</ol></div>")
    procedures = sum(1 for f in card.findings if f.steps)
    count = (f"{procedures} procedimiento{'s' if procedures != 1 else ''} · "
             f"{steps_total} paso{'s' if steps_total != 1 else ''}")
    return (f'<details class="howto"><summary>{_icon("i-wrench")}'
            f'Cómo aplicar estas mejoras<span class="cnt">{count}</span>'
            f'{_icon("i-chev", "ic chev")}</summary>'
            f'<div class="howto-body">{blocks}</div></details>')


def _html_component_cards(cards: list[ComponentCard]) -> str:
    parts = []
    for card in cards:
        if card.score is not None:
            note = (f'<span class="note" style="color:{_score_color(card.score)}">'
                    f"{card.score:.0f} pts · nota {card.letter}</span>")
        else:
            # Distintivo y no texto en el sitio de la cifra: con cuatro tarjetas
            # diciendo «146 pts · nota S» y dos diciendo una frase en gris, las
            # dos sin nota parecían las que no habían podido medirse. No es que
            # falte el dato, es que esa categoría no entra en la puntuación.
            note = (f'<span class="badge b-info" title="{_e(_no_score_text(card))}">'
                    f"no puntúa</span>")

        icon = _icon(COMPONENT_ICONS.get(card.key, "i-sys"), "ic lg")
        block = [f'<div class="card" id="c-{_e(card.key)}"><div class="chead">'
                 f"<h3>{icon}{_e(card.label)}</h3>{note}</div>"]

        if card.specs:
            specs = "".join(f'<div class="k">{_e(k)}</div><div>{_e(v)}</div>'
                            for k, v in card.specs)
            block.append(f'<div class="kvs">{specs}</div>')

        if card.tests:
            # No se repite la tabla: era la de Benchmark con una columna menos y
            # las mismas cifras, que ya salían además en la tira del resumen. Lo
            # que la ficha aporta es lo de al lado —las specs, qué se puede
            # mejorar y con qué riesgo—, así que de la medida solo queda el
            # nombre de las pruebas que la componen y el camino a su detalle.
            nombres = " · ".join(_e(r.name) for r in card.tests)
            block.append(f'<div class="sub-h">Pruebas que dan esta nota</div>'
                         f'<p class="scan-note">{nombres}</p>'
                         f'<p class="xref"><a href="#benchmark">{_icon("i-chart")}'
                         f"Medida, margen y comparación con la referencia en Benchmark</a></p>")

        if card.findings:
            head = "Mejoras aplicables"
            if card.gain > 0.005:
                head += f' — <span class="gain">+{card.gain * 100:.0f}% combinado</span>'
                if card.projected_score is not None:
                    head += (f' <span style="color:var(--dim)">({card.score:.0f} → '
                             f"{card.projected_score:.0f} pts)</span>")
            items = ""
            for f in card.findings:
                gain = (f'<span class="gain">+{f.gain * 100:.0f}%</span>' if f.gain
                        else '<span style="color:var(--dim)">sin ganancia directa</span>')
                items += (f'<li>{gain} &nbsp; <a href="#h-{_e(f.id)}">{_e(f.title)}</a>'
                          f'<div class="tags"><span class="badge b-{_e(f.severity)}">{_e(f.severity)}'
                          f"</span> &nbsp; {_e(f.category)} · esfuerzo {_e(f.effort)} · riesgo "
                          f"{_e(f.risk)} · {_e(f.gain_note)}</div></li>")
            block.append(f'<div class="sub-h">{head}</div><ul class="imp">{items}</ul>')
            block.append(_howto_subcard(card))
        elif not card.riesgos:
            # Solo cuando no hay ni lo uno ni lo otro. Con riesgos delante, un
            # «sin nada pendiente» en verde sobre la ficha de un equipo con SMB1
            # activo y sin cifrar es exactamente el aviso que no hay que dar.
            block.append('<div class="sub-h">Mejoras aplicables</div>'
                         '<p class="ok-note">Sin mejoras pendientes.</p>')

        if card.riesgos:
            items = ""
            for f in card.riesgos:
                items += (f'<li><a href="#s-{_e(f.id)}">{_e(f.title)}</a>'
                          f'<div class="tags">'
                          f'<span class="badge b-{_e(f.severity)}">{_e(f.severity)}</span>'
                          f" &nbsp; esfuerzo {_e(f.effort)} · riesgo {_e(f.risk)}</div></li>")
            block.append(
                '<div class="sub-h">Riesgos de este componente</div>'
                '<p class="scan-note">No son mejoras de rendimiento y por eso van '
                'aparte: arreglarlos no sube la nota. Los pasos están en la '
                'sección <a href="#seguridad">Seguridad</a>.</p>'
                f'<ul class="imp">{items}</ul>')

        block.append("</div>")
        parts.append("".join(block))
    return "".join(parts)


def _conditions_block(bench: Benchmark) -> str:
    """En qué condiciones se midió: qué más corría y cómo llegó el equipo al
    final de la carga.

    Las dos cosas se guardaban en el JSON y no se enseñaban en ninguna parte, así
    que el margen de una prueba salía marcado en amarillo sin que se pudiera ver
    la causa. La causa suele tener nombre y estar en esta tabla.
    """
    ambiente = getattr(bench, "ambient_load", {}) or {}
    fotos = getattr(bench, "load_snapshots", []) or []
    out = ""

    filas = ""
    for momento, datos in ambiente.items():
        culpables = ", ".join(f"{_e(n)} ({p:.0f}%)" for n, p in datos.get("top") or []) or "—"
        color = "var(--warn)" if datos.get("cpu_pct", 0) >= BUSY_CPU_PCT else "inherit"
        filas += (f"<tr><td>{_e(momento.capitalize())}</td>"
                  f'<td style="color:{color}"><b>{datos.get("cpu_pct", 0):.1f}%</b></td>'
                  f"<td>{culpables}</td></tr>")
    if filas:
        out += ('<div class="card"><div class="sub-h">Carga ajena durante la sesión</div>'
                f'<p class="scan-note">La {_term("carga ajena", "carga-ajena")} se mide con '
                "el benchmark parado, justo antes y justo después: lo que aparezca aquí lo "
                "consumía otro programa. Es lo que decide si esta ejecución se puede "
                "comparar con otra.</p><div class=\"tw\"><table>"
                "<tr><th>Momento</th><th>CPU ajena</th><th>Procesos que la usaban</th></tr>"
                + filas + "</table></div></div>")

    utiles = [f for f in fotos if any(f.get(k) is not None for k in
                                      ("cpu_mhz", "cpu_temp", "gpu_temp", "gpu_power_w"))]
    if utiles:
        def celda(foto: dict, clave: str, fmt: str) -> str:
            valor = foto.get(clave)
            return format(valor, fmt) if valor is not None else "—"

        filas = ""
        for foto in utiles:
            fuente = foto.get("cpu_mhz_source") or ""
            nota = f'<div class="tags">{_e(fuente)}</div>' if fuente else ""
            filas += (f"<tr><td>{_e(str(foto.get('moment') or '—').capitalize())}{nota}</td>"
                      f"<td>{celda(foto, 'cpu_mhz', '.0f')} MHz</td>"
                      f"<td>{celda(foto, 'cpu_temp', '.0f')} °C</td>"
                      f"<td>{celda(foto, 'gpu_temp', '.0f')} °C</td>"
                      f"<td>{celda(foto, 'gpu_power_w', '.0f')} W</td></tr>")
        out += ('<div class="card"><div class="sub-h">Sensores antes y después de la carga'
                '</div><p class="scan-note">La misma foto en dos momentos. Si la frecuencia '
                "baja o la temperatura se dispara entre una y otra, el equipo se está "
                "limitando solo y la nota de la prueba larga lo refleja.</p>"
                '<div class="tw"><table><tr><th>Momento</th><th>CPU</th><th>Temp. CPU</th>'
                "<th>Temp. GPU</th><th>Consumo GPU</th></tr>" + filas + "</table></div></div>")
    return out


def _metrics_block(bench: Benchmark) -> str:
    """Métricas que no puntúan pero explican el porqué de la puntuación."""
    if not bench.metrics and not bench.memory_hierarchy:
        return ""
    out = ""
    if bench.memory_hierarchy:
        peak = max(lv["gbs"] for lv in bench.memory_hierarchy) or 1
        trs = ""
        for lv in bench.memory_hierarchy:
            trs += (f"<tr><td><b>{_e(lv['level'])}</b></td><td>bloques de "
                    f"{_human(lv['size'])}</td><td>{lv['gbs']:.1f} GB/s</td>"
                    f'<td><div class="track"><div class="fill" '
                    f'style="width:{lv["gbs"] / peak * 100:.0f}%;background:var(--acc)">'
                    f"</div></div></td></tr>")
        out += ('<div class="card"><div class="sub-h">Jerarquía de memoria</div>'
                '<p class="scan-note">Ancho de banda de copia según el tamaño del bloque. '
                'Mientras el dato cabe en caché la cifra se mantiene; la caída final es el '
                'salto a la RAM.</p><div class="tw"><table>' + trs + "</table></div></div>")
    if bench.metrics:
        trs = ""
        for m in bench.metrics.values():
            value = f"{m['value']} {m['unit']}".strip()
            note = f'<div class="tags">{_e(m["note"])}</div>' if m["note"] else ""
            trs += f"<tr><td>{_e(m['label'])}{note}</td><td><b>{_e(value)}</b></td></tr>"
        out += ('<div class="card"><div class="sub-h">Métricas de diagnóstico</div>'
                '<div class="tw"><table>' + trs + "</table></div></div>")
    return out


def _system_state_block(auditor: Auditor, bench: Benchmark | None) -> str:
    """Estado del sistema que la auditoría mide pero no cabía en un hallazgo:
    arranque, programas de inicio uno a uno, procesos residentes y de dónde salió
    (o no) cada sensor."""
    out = []

    # Va la primera: define hasta dónde llega lo que el resto del informe afirma.
    sin_datos = getattr(auditor, "unverified", [])
    no_aplican = getattr(auditor, "not_applicable", [])
    if sin_datos or no_aplican:
        cuerpo = ""
        if sin_datos:
            filas = "".join(f"<tr><td>{_e(c)}</td><td>{_e(r)}</td></tr>" for c, r in sin_datos)
            cuerpo += ('<div class="tw"><table><tr><th>Comprobación</th>'
                       "<th>Por qué no hay veredicto</th></tr>" + filas + "</table></div>")
        if no_aplican:
            cuerpo += ('<p class="scan-note">No aplican a este equipo: '
                       + ", ".join(f"{_e(c)} ({_e(r)})" for c, r in no_aplican) + ".</p>")
        out.append('<div class="card"><div class="sub-h">Cobertura de la auditoría</div>'
                   f'<p class="scan-note">La {_term("cobertura", "cobertura")} de esta '
                   f"ejecución es de {auditor.checks_run} sobre "
                   f'{getattr(auditor, "checks_total", auditor.checks_run)} comprobaciones. '
                   "Las de abajo no llegaron a un veredicto: no significan «correcto», "
                   "significan que no hay dato con el que opinar.</p>" + cuerpo + "</div>")

    boot = getattr(auditor, "boot_report", {}) or {}
    segundos = getattr(auditor, "boot_seconds", None)
    if segundos or boot.get("error"):
        if segundos:
            arranques = len([b for b in boot.get("boots", [])
                             if str(b["fields"].get("BootIsRebootAfterInstall") or "0") != "1"])
            cuerpo = (f'<div class="kvs"><div class="k">Duración</div>'
                      f"<div><b>{segundos:.0f} s</b> de mediana sobre {arranques} arranques</div>")
            # El mismo lector que usa la auditoría, y por el mismo motivo: los
            # campos vienen de un XML del registro de eventos y uno puede llegar
            # vacío o con texto donde debería haber milisegundos. `_event_ms`
            # descarta lo que no sea un número en vez de reventar el informe
            # entero por un elemento de arranque mal declarado.
            retrasos = sorted(
                ((str(d["fields"].get("Name") or d["fields"].get("FriendlyName") or ""),
                  Auditor._event_ms(d["fields"], "TotalTime", "DegradationTime"),
                  d.get("kind") or "") for d in boot.get("delays", [])),
                key=lambda x: -(x[1] or 0.0))
            vistos, filas = set(), ""
            for nombre, ms, tipo in retrasos:
                if not nombre or nombre in vistos or not ms:
                    continue
                vistos.add(nombre)
                filas += (f"<tr><td>{_e(nombre)}</td><td>{_e(tipo)}</td>"
                          f"<td>{ms / 1000:.1f} s</td></tr>")
                if len(vistos) >= 12:
                    break
            cuerpo += "</div>"
            if filas:
                cuerpo += ('<div class="sub-h">Lo que más lo retrasa</div><div class="tw">'
                           "<table><tr><th>Elemento</th><th>Tipo</th><th>Retraso</th></tr>"
                           + filas + "</table></div>")
        else:
            cuerpo = ('<p class="scan-note">No se ha podido medir: '
                      f"{_e(boot['error'])}.</p>")
        out.append('<div class="card"><div class="sub-h">Arranque medido por Windows</div>'
                   '<p class="scan-note">Lo cronometra el propio sistema en cada encendido. '
                   "Se descartan los reinicios por actualización, que siempre son lentos.</p>"
                   + cuerpo + "</div>")

    startup = getattr(auditor, "startup_items", [])
    if startup:
        filas = ""
        for item in sorted(startup, key=lambda i: (not i.get("enabled"), i["name"].lower())):
            activo = item.get("enabled")
            estado = ('<span class="badge b-medium">activo</span>' if activo
                      else '<span class="badge b-info">desactivado</span>')
            filas += (f"<tr><td>{_e(item['name'])}</td>"
                      f"<td>{_e(item.get('location') or '')}</td><td>{estado}</td></tr>")
        activos = sum(1 for i in startup if i.get("enabled"))
        out.append('<div class="card"><div class="sub-h">Programas de inicio</div>'
                   f'<p class="scan-note">{activos} activos de {len(startup)}. Los desactivados '
                   "siguen registrados pero Windows no los ejecuta.</p><div class=\"tw\">"
                   "<table><tr><th>Programa</th><th>Origen</th><th>Estado</th></tr>"
                   + filas + "</table></div></div>")

    procesos = getattr(auditor, "top_processes", [])
    if procesos:
        filas = "".join(f"<tr><td>{_e(p['name'])}</td><td>{_human(p['rss'])}</td></tr>"
                        for p in procesos)
        out.append('<div class="card"><div class="sub-h">Procesos que más memoria ocupan</div>'
                   '<div class="tw"><table><tr><th>Proceso</th><th>Memoria</th></tr>'
                   + filas + "</table></div></div>")

    intentos = temperature_report()
    if intentos:
        fuente = temperature_source()
        if fuente:
            # Con alguna fuente viva la tabla sí distingue: dice cuál respondió y
            # qué le pasó a cada una de las otras, que es lo que hace falta para
            # saber si el dato es de fiar o es el último recurso.
            filas = "".join(f"<tr><td>{_e(s)}</td><td>{_e(r)}</td></tr>" for s, r in intentos)
            cuerpo = ('<div class="tw"><table><tr><th>Fuente</th><th>Resultado</th></tr>'
                      + filas + "</table></div>")
            aviso = f'<p class="scan-note">Fuente en uso: <b>{_e(fuente)}</b>.</p>'
        else:
            # Sin ninguna, la tabla eran seis filas idénticas diciendo «sin
            # datos» debajo de un párrafo que ya lo había dicho: media pantalla
            # dedicada a un dato que no existe. Los nombres siguen estando, en
            # una línea, porque saber qué se intentó es lo único que aportaban.
            cuerpo = ""
            aviso = ('<p class="scan-note">Ninguna de las '
                     f"{len(intentos)} fuentes respondió "
                     f'({_e(", ".join(s for s, _ in intentos))}). Leer la temperatura de CPU '
                     "en Windows exige un driver en modo kernel; instalar "
                     "LibreHardwareMonitor y dejarlo abierto la hace accesible.</p>")
        out.append('<div class="card"><div class="sub-h">Sensores de temperatura</div>'
                   + aviso + cuerpo + "</div>")

    if bench:
        filas = ""
        for k, v in REFERENCE.items():
            origen = REFERENCE_ORIGIN.get(k, "")
            filas += (f"<tr><td>{_e(k)}</td><td>{v}</td>"
                      f'<td class="tags">{_e(origen)}</td></tr>')
        caducada = ""
        if reference_is_stale():
            caducada = ('<p class="scan-note" style="color:var(--warn)">La escala se fijó hace '
                        f"{reference_age_months() / 12:.1f} años. Sigue midiendo bien, pero "
                        "«100 puntos = gama media» ya no describe lo que se vende hoy: "
                        "la nota está inflada respecto a un equipo actual.</p>")
        out.append('<div class="card"><div class="sub-h">Escala de referencia</div>'
                   f'<p class="scan-note">Los 100 puntos de la '
                   f'{_term("referencia", "referencia")} equivalen a estos valores, fijados '
                   f"en <b>{_e(REFERENCE_DATE)}</b> sobre un equipo tipo "
                   f"{_e(REFERENCE_MACHINE)}. "
                   "Los tiempos van en segundos; el resto, en su unidad.</p>"
                   + caducada + '<div class="tw">'
                   "<table><tr><th>Prueba</th><th>Referencia</th><th>De dónde sale</th></tr>"
                   + filas + "</table></div></div>")
    return "".join(out)


def _network_block(red: dict[str, Any]) -> str:
    """Enlace de red. Sin SSID, sin BSSID y sin MAC: identifican la red y el
    equipo, no dicen nada del rendimiento, y esto se comparte."""
    if not red.get("adapters"):
        return ""
    filas = ""
    for a in red["adapters"]:
        velocidad = f"{a['link_mbps']:,.0f} Mbps" if a.get("link_mbps") else "—"
        conectado = a["status"].lower() in ("up", "conectado")
        estado = (f'<span class="badge b-{"low" if conectado else "info"}">'
                  f'{_e(a["status"])}</span>')
        filas += (f"<tr><td>{_e(a['name'])}</td><td>{_e(a['description'])}</td>"
                  f"<td>{velocidad}</td><td>{estado}</td></tr>")
    out = ('<div class="card"><div class="sub-h">Adaptadores</div><div class="tw">'
           "<table><tr><th>Interfaz</th><th>Adaptador</th><th>Enlace</th><th>Estado</th></tr>"
           + filas + "</table></div></div>")

    wifi = red.get("wifi") or {}
    if wifi:
        kv = [("Generación del enlace", wifi.get("radio", "—")),
              ("Velocidad negociada", f"{wifi['rate_mbps']:,.0f} Mbps"
               if wifi.get("rate_mbps") else "—"),
              ("Banda", f"{wifi['band_ghz']} GHz" if wifi.get("band_ghz") else "—"),
              ("Canal", str(wifi.get("channel") or "—")),
              ("Señal", f"{wifi.get('rssi_dbm', '—')} dBm"
                        + (f" ({wifi['signal_pct']}%)" if wifi.get("signal_pct") else ""))]
        out += ('<div class="card"><div class="sub-h">Enlace wifi</div>'
                '<p class="scan-note">No se recogen el nombre de la red, el punto de '
                "acceso ni la dirección física: identifican tu red y no aportan nada "
                'sobre el rendimiento.</p><div class="kvs">'
                + "".join(f'<div class="k">{_e(k)}</div><div>{_e(v)}</div>' for k, v in kv)
                + "</div></div>")

    latencia = red.get("latency") or {}
    if latencia.get("targets"):
        filas = ""
        for t in latencia["targets"]:
            medida = f"{t['median_ms']:.1f} ms" if t["median_ms"] else "sin respuesta"
            jitter = f"{t['jitter_ms']:.1f} ms" if t.get("jitter_ms") else "—"
            filas += (f"<tr><td>{_e(t['name'])}</td><td>{_e(t['host'])}</td>"
                      f"<td>{medida}</td><td>{jitter}</td><td>{t['loss_pct']}%</td></tr>")
        dns = red.get("dns") or {}
        nota = (f'<p class="scan-note">Resolución DNS: <b>{dns["median_ms"]:.1f} ms</b> '
                f'de mediana.</p>' if dns.get("median_ms") else "")
        out += ('<div class="card"><div class="sub-h">Latencia</div>'
                '<p class="scan-note">Tiempo del saludo TCP contra resolutores DNS '
                "públicos. Mide el camino hasta internet, no la velocidad de bajada.</p>"
                + nota + '<div class="tw"><table><tr><th>Destino</th><th>Dirección</th>'
                "<th>Latencia</th><th>Variación</th><th>Pérdida</th></tr>"
                + filas + "</table></div></div>")
    elif not red.get("active"):
        out += ('<div class="card"><p class="scan-note">La latencia y el DNS no se han '
                "medido: esta ejecución usó <code>--no-net</code>, que impide abrir "
                "conexiones a servidores de terceros.</p></div>")
    return out


def _storage_scan_block(scan: ScanResult) -> str:
    safe, review = candidate_bytes(scan)
    head = (f'<p class="scan-note">Umbral {_human(scan.min_size)} · '
            f"{scan.scanned_files:,} ficheros y {scan.scanned_dirs:,} carpetas revisados en "
            f"{scan.elapsed:.0f} s".replace(",", ".") + "</p>")
    if scan.truncated:
        head += ('<p class="scan-note" style="color:var(--warn)">Rastreo parcial: se agotó el '
                 "presupuesto de tiempo. Amplíalo con <code>--scan-time 60</code>.</p>")
    if not scan.files and not scan.special:
        return head + '<p class="ok-note">Nada por encima del umbral.</p>'

    out = f'<div class="card">{head}'
    if scan.by_category:
        rows = ""
        biggest = max(v["size"] for v in scan.by_category.values()) or 1
        for cat, data in scan.by_category.items():
            if cat in RECLAIMABLE:
                tag = '<span class="badge b-low">se puede borrar</span>'
                color = "var(--ok)"
            elif cat in REVIEWABLE:
                tag = '<span class="badge b-medium">revisar</span>'
                color = "var(--warn)"
            else:
                tag = ""
                color = "var(--dim)"
            files = data.get("files") or []
            listado = ""
            for f in files:
                listado += (f"<tr><td>{_human(f['size'])}</td><td>{f['age_days']} días</td>"
                            f'<td class="pathcell">{_e(f["path"])}</td></tr>')
            restantes = data["count"] - len(files)
            pie = (f'<p class="scan-note">…y {restantes} más de esta categoría.</p>'
                   if restantes > 0 else "")
            # Cada categoría se despliega para ver exactamente qué hay dentro:
            # decidir si sobra un grupo entero solo se puede con los nombres.
            rows += (
                f'<details class="cat"><summary>'
                f'<span class="cat-name">{_e(cat.capitalize())}</span>'
                f'<span class="cat-size">{_human(data["size"])}</span>'
                f'<span class="cat-count">{data["count"]} ficheros</span>'
                f"<span class=\"cat-tag\">{tag}</span>"
                f'<span class="track cat-bar"><span class="fill" '
                f'style="width:{data["size"] / biggest * 100:.0f}%;background:{color}"></span>'
                f"</span>"
                f'{_icon("i-chev", "ic chev")}</summary>'
                f'<div class="cat-body"><div class="tw"><table>'
                f"<tr><th>Tamaño</th><th>Sin tocar</th><th>Ruta</th></tr>{listado}</table></div>"
                f"{pie}</div></details>")
        out += ('<div class="sub-h">Por tipo</div>'
                '<p class="scan-note">Despliega una categoría para ver qué ficheros la '
                "componen.</p>" + rows
                + f'<p class="scan-note" style="margin-top:14px">Total '
                  f'{_human(scan.total_large)}: <span class="gain">{_human(safe)}</span> es '
                  f'basura y <span style="color:var(--warn)">{_human(review)}</span> son '
                  f"candidatos a revisar antes de borrar.</p>")
    out += "</div>"

    if scan.files:
        # Los cinco primeros y no la lista entera: las categorías de arriba ya
        # traen estos mismos ficheros, y con el nombre de la categoría delante.
        # Lo único que la lista plana añadía era el orden global por tamaño, y
        # para eso bastan cinco filas; las otras quince eran las de arriba otra
        # vez, sin agrupar.
        top = sorted(scan.files, key=lambda f: -f["size"])[:5]
        trs = ""
        for f in top:
            trs += (f"<tr><td>{_human(f['size'])}</td><td>{_e(f['category'])}</td>"
                    f"<td>{f['age_days']} días</td>"
                    f'<td class="pathcell">{_e(f["path"])}</td></tr>')
        resto = len(scan.files) - len(top)
        pie = (f'<p class="scan-note">Los otros {resto} que pasan del umbral están arriba, '
               "dentro de su categoría.</p>" if resto > 0 else "")
        out += ('<div class="card"><div class="sub-h">Los cinco más grandes</div>'
                '<div class="tw"><table><tr><th>Tamaño</th><th>Tipo</th><th>Sin tocar</th>'
                "<th>Ruta</th></tr>" + trs + "</table></div>" + pie + "</div>")

    if scan.special:
        trs = ""
        for s in scan.special:
            trs += (f"<tr><td>{_e(s['name'])}</td>"
                    f"<td>{_human(s['size']) if s['size'] else '—'}</td>"
                    f"<td>{_e(s['note'])}</td></tr>")
        out += ('<div class="card"><div class="sub-h">Archivos de sistema</div>'
                '<p class="scan-note">Ocupan mucho y tienen su función: no se borran a mano.'
                '</p><div class="tw"><table><tr><th>Archivo</th><th>Tamaño</th><th>Qué es</th>'
                "</tr>" + trs + "</table></div></div>")
    return out


def _projection_tables(projection: dict[str, Any]) -> str:
    # Solo lo que cambia. La tabla listaba los cinco componentes con su nota de
    # hoy —las mismas cifras que la tira del resumen y que Benchmark, por tercera
    # vez— para acabar diciendo «—» de ganancia en casi todos. Una tabla de
    # antes y después en la que cuatro de cinco filas son iguales a los dos lados
    # obliga a leerla entera para descubrir que solo importaba una.
    actuales = projection["current_components"]
    trs, sin_margen = "", []
    for k, cur in actuales.items():
        gain = projection["component_gain"].get(k, 0.0)
        etiqueta = COMPONENT_LABELS.get(k, k)
        if not gain:
            sin_margen.append(etiqueta)
            continue
        proj = projection["projected_components"][k]
        trs += (f"<tr><td>{_e(etiqueta)}</td><td>{cur:.0f} pts</td>"
                f"<td>{proj:.0f} pts</td>"
                f'<td class="gain">+{gain * 100:.0f}%</td>'
                f"<td>{_html_bar(proj)}</td></tr>")
    out = ""
    if trs:
        out += ('<div class="card"><div class="tw"><table>'
                "<tr><th>Componente</th><th>Ahora</th><th>Optimizado</th><th>Ganancia</th>"
                "<th></th></tr>" + trs + "</table></div>"
                + (f'<p class="scan-note">Los otros {len(sin_margen)} componentes no tienen '
                   f'mejoras pendientes: {_e(", ".join(sin_margen))}.</p>'
                   if sin_margen else "")
                + "</div>")
    elif sin_margen:
        out += ('<div class="card"><p class="ok-note">Ningún componente tiene mejoras de '
                "rendimiento pendientes: "
                f'{_e(", ".join(sin_margen))} ya rinden lo que pueden dar.</p></div>')

    # Fuera de la tabla: era la única fila sin cifras a ambos lados, con un
    # `colspan` que rompía la lectura por columnas de una tabla cuyo sentido es
    # justo comparar columna con columna.
    sysgain = projection.get("system_gain", 0.0)
    if sysgain:
        out += ('<div class="card"><div class="sub-h">Arranque y fluidez</div>'
                '<p class="scan-note">No tiene prueba sintética que lo puntúe: se nota al '
                "encender y al abrir programas, no en la nota global.</p>"
                f'<p><span class="gain">+{sysgain * 100:.0f}%</span> estimado</p>'
                f"{_html_bar(sysgain * 100, ref=False)}</div>")

    cats = ""
    for cat, gain in sorted(projection.get("category_gain", {}).items(), key=lambda x: -x[1]):
        cats += (f"<tr><td>{_e(cat.capitalize())}</td>"
                 f'<td class="gain">+{gain * 100:.0f}%</td>'
                 f"<td>{_html_bar(min(100, gain * 100), ref=False)}</td></tr>")
    if cats:
        out += ('<div class="card"><div class="sub-h">Margen por área</div><div class="tw">'
                "<table>" + cats + "</table></div></div>")
    return out


def _worst(findings: list) -> str:
    """La severidad más grave de un grupo de hallazgos, o cadena vacía."""
    if not findings:
        return ""
    return min((f.severity for f in findings), key=lambda s: SEVERITY_ORDER.get(s, 9))


