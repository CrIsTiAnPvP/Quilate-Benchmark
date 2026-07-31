"""Exportacion a HTML: informe autocontenido, sin recursos externos.

Todo va embebido (CSS, iconos SVG y un punado de JS) para que el fichero se
pueda enviar por correo o abrir sin conexion y siga funcionando igual.

Este fichero monta el documento: decide que secciones hay, en que orden y
con que envoltorio. El orden no es cosmetico —la seguridad va antes del plan
y el veredicto al final— y tenerlo en un solo sitio es lo que permite leerlo
de una sentada.

La fachada mantiene los nombres que se importaban de `html_export` cuando
era un modulo suelto, por eso el paquete se llama igual: ni un import de
fuera cambia.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ...audit import Auditor, SEVERITY_ORDER, security_findings
from ...benchmark import Benchmark
from ...components import build_component_cards, finding_group
from ...const import APP_NAME, APP_VERSION, AUTHOR, WEBSITE, WEBSITE_URL
from ...projection import priority_rank
from ...storage_scan import candidate_bytes
from ...sysinfo import SystemInfo
from ...veredicto import build_verdict

from .bloques import (_benchmark_table, _component_strip, _conditions_block,
                      _hero, _html_component_cards, _inventory, _metrics_block,
                      _network_block, _projection_tables, _sidebar,
                      _storage_scan_block, _system_state_block, _worst)
from .estilos import HTML_CSS
from .guion import HTML_JS
from .piezas import (NAV_LABELS, Seccion, _e, _favicon, _human, _icon, _logo,
                     _slug, _sprite, reiniciar_glosario)
# Reexportado y no usado aqui: los tests lo piden como atributo de este modulo,
# que es el nombre por el que `html_export` se conoce desde fuera.
from .piezas import _term            # noqa: F401

# La superficie que se importa de aqui desde fuera del paquete, medida por AST
# sobre modulos y tests. Los tres privados los piden los tests por su nombre.
__all__ = ["export_html", "Seccion", "HTML_CSS", "HTML_JS", "_favicon", "_logo"]


def _section(sec: Seccion) -> str:
    cnt = f'<span class="cnt">{_e(sec.count)}</span>' if sec.count else ""
    # La casilla y el botón viven dentro del <summary>: el JS corta ahí la
    # propagación del clic para que marcar o exportar no pliegue la sección.
    # Va envuelta en un <label> con texto propio —oculto en pantallas
    # estrechas, pero siempre presente para un lector de pantalla— porque un
    # checkbox suelto no dice qué marca, y aquí marcarlo no selecciona la
    # sección: la añade a un fichero que se va a descargar.
    pick = (f'<label class="pick" for="pick-{sec.sid}">'
            f'<input type="checkbox" id="pick-{sec.sid}" data-pick="{sec.sid}" '
            f'aria-label="Incluir «{_e(sec.label)}» en la exportación por secciones">'
            f'<span class="ptxt">Exportar</span></label>')
    export = (f'<button class="exp" data-export="{sec.sid}" type="button" '
              f'title="Descargar solo esta sección como fichero HTML suelto">'
              f'{_icon("i-download")}Descargar</button>')
    return (f'<details class="sec t-{sec.tono}" id="{sec.sid}" data-title="{_e(sec.label)}" '
            f'data-slug="{_slug(sec.label)}" data-icon="{sec.icon}" '
            f'data-group="{sec.grupo}" open>'
            f'<summary><span class="stitle">{_icon(sec.icon)}{_e(sec.label)}</span>'
            f"{cnt}{pick}{export}"
            f'{_icon("i-chev", "ic chev")}</summary>'
            f'<div class="body">{sec.inner}</div></details>')


def export_html(path: Path, si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                projection: dict[str, Any]) -> None:
    # El glosario marca solo la primera aparición de cada término, así que su
    # memoria es por informe: sin esto, generar dos seguidos dejaría el segundo
    # sin ninguna explicación.
    reiniciar_glosario()

    cards = build_component_cards(si, bench, auditor)
    findings = sorted(auditor.findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.gain))
    actionable = sorted([f for f in auditor.findings if f.gain > 0], key=priority_rank)

    secs: list[Seccion] = [
        Seccion("inventario", "Inventario", "i-box", _inventory(si)),
    ]
    if bench and bench.results:
        secs.append(Seccion("benchmark", "Benchmark", "i-chart",
                            _benchmark_table(bench) + _metrics_block(bench)
                            + _conditions_block(bench),
                            f"{len(bench.results)} pruebas"))
    secs.append(Seccion("componentes", "Ficha por componente", "i-cpu",
                        _html_component_cards(cards), f"{len(cards)} componentes",
                        _worst(auditor.findings), len(auditor.findings)))

    estado = _system_state_block(auditor, bench)
    if estado:
        arranque = getattr(auditor, "boot_seconds", None)
        secs.append(Seccion("estado", "Estado del sistema", "i-clock", estado,
                            f"arranque {arranque:.0f} s" if arranque else ""))

    red = _network_block(getattr(auditor, "network", {}) or {})
    if red:
        wifi = (getattr(auditor, "network", {}) or {}).get("wifi") or {}
        de_red = [c for c in cards if c.key == "network"]
        hallazgos_red = de_red[0].findings if de_red else []
        secs.append(Seccion("red", "Red", "i-net", red, wifi.get("radio", ""),
                            _worst(hallazgos_red), len(hallazgos_red)))

    scan = getattr(auditor, "scan", None)
    if scan is not None and scan.available and (scan.files or scan.special):
        safe, review = candidate_bytes(scan)
        secs.append(Seccion("archivos", "Archivos grandes", "i-folder",
                            _storage_scan_block(scan),
                            f"{_human(safe + review)} prescindibles"))
    if projection.get("current_components"):
        secs.append(Seccion("proyeccion", "Proyección de mejora", "i-trend",
                            _projection_tables(projection),
                            f"+{projection.get('headroom_pct', 0.0):.0f}% sintético"))

    # Antes del plan y fuera de él: el plan ordena por retorno dividido por
    # esfuerzo, y estos hallazgos no tienen retorno que ordenar.
    riesgos = security_findings(auditor.findings)
    if riesgos:
        tarjetas = ""
        for f in riesgos:
            pasos = "".join(f"<li>{_e(p)}</li>" for p in f.steps)
            tarjetas += (f'<div class="card finding" id="s-{_e(f.id)}">'
                         f"<h3>{_e(f.title)}</h3>"
                         f'<div class="tags">'
                         f'<span class="badge b-{_e(f.severity)}">{_e(f.severity)}</span>'
                         f" &nbsp; esfuerzo {_e(f.effort)} · riesgo {_e(f.risk)}</div>"
                         f"<p>{_e(f.detail)}</p>"
                         + (f"<ul>{pasos}</ul>" if pasos else "") + "</div>")
        secs.append(Seccion("seguridad", "Seguridad", "i-alert",
                            '<p class="hint">Esto no acelera el equipo: son riesgos. No '
                            "entran en la proyección de mejora porque no hay mejora que "
                            "proyectar.</p>" + tarjetas,
                            f"{len(riesgos)} riesgos",
                            _worst(riesgos), len(riesgos)))

    if actionable:
        trs = ""
        for i, f in enumerate(actionable, 1):
            trs += (f'<tr><td>{i}</td><td><a href="#h-{_e(f.id)}">{_e(f.title)}</a></td>'
                    f'<td class="gain">+{f.gain * 100:.0f}%</td>'
                    f"<td>{_e(f.effort)}</td><td>{_e(f.risk)}</td>"
                    f'<td><span class="badge b-{_e(f.severity)}">{_e(f.severity)}</span></td></tr>')
        secs.append(Seccion("plan", "Plan de acción priorizado", "i-list",
                            '<div class="card"><div class="tw"><table>'
                            "<tr><th>#</th><th>Acción</th><th>Ganancia est.</th>"
                            "<th>Esfuerzo</th><th>Riesgo</th><th>Severidad</th></tr>"
                            + trs + "</table></div>"
                            '<p class="tags">Ordenado por retorno estimado dividido por '
                            "esfuerzo. Aplica de arriba hacia abajo y vuelve a medir tras "
                            "cada bloque.</p></div>",
                            f"{len(actionable)} acciones",
                            _worst(actionable), len(actionable)))

    if findings:
        # Aquí va el diagnóstico, no el procedimiento: los pasos viven en la
        # ficha del componente, dentro de «Cómo aplicar estas mejoras». Repetirlos
        # obligaría a mantener dos copias y alargaría esta sección sin aportar.
        labels = {c.key: c.label for c in cards}
        # Los riesgos de seguridad ya se cuentan enteros —detalle y pasos— en su
        # propia sección, unos centímetros más arriba. Aquí salían otra vez con
        # el mismo párrafo palabra por palabra: el mismo hallazgo contado dos
        # veces en la misma página. Se quedan con su sitio y su ancla, porque el
        # recuento de hallazgos los incluye y porque quien llega desde un enlace
        # `#h-…` tiene que aterrizar en algo, pero el texto largo vive en un solo
        # sitio y desde aquí se remite a él.
        ids_riesgo = {f.id for f in riesgos}
        detail = ""
        for f in findings:
            cabecera = (f'<div class="card finding" id="h-{_e(f.id)}"><h3>{_e(f.title)}</h3>'
                        f'<div class="tags">'
                        f'<span class="badge b-{_e(f.severity)}">{_e(f.severity)}</span>')
            if f.id in ids_riesgo:
                detail += (cabecera + f" &nbsp; {_e(f.category)}</div>"
                           f'<p class="xref"><a href="#s-{_e(f.id)}">{_icon("i-shield")}'
                           f"Qué es y cómo se corrige, en Seguridad</a></p></div>")
                continue
            gain = (f'<span class="gain">Mejora estimada +{f.gain * 100:.0f}%</span> '
                    f'<span style="color:var(--dim)">({_e(f.gain_note)})</span>') if f.gain else ""
            group = finding_group(f)
            enlace = ""
            if f.steps and group in labels:
                enlace = (f'<p class="steps-link"><a href="#c-{_e(group)}">{_icon("i-wrench")}'
                          f"Los {len(f.steps)} pasos para solucionarlo están en la ficha de "
                          f"{_e(labels[group])}</a></p>")
            detail += (cabecera
                       + f" &nbsp; {_e(f.category)} · esfuerzo {_e(f.effort)} · riesgo "
                       f"{_e(f.risk)} &nbsp; {gain}</div><p>{_e(f.detail)}</p>{enlace}</div>")
        secs.append(Seccion("hallazgos", "Hallazgos en detalle", "i-alert", detail,
                            f"{len(findings)} hallazgos",
                            _worst(findings), len(findings)))

    verdict, extra = build_verdict(si, bench, auditor, projection)
    extras = "".join(f"<li>{_e(x)}</li>" for x in extra)
    secs.append(Seccion("veredicto", "Veredicto", "i-award",
                        f'<div class="card verdict">{_logo("brandmark wm")}'
                        f"<p>{_e(verdict)}</p>"
                        f'{"<ul>" + extras + "</ul>" if extras else ""}</div>'))

    # La navegación va agrupada por el mismo criterio que reparte las secciones
    # en `data-group`: primero lo que explica de dónde sale la nota, después lo
    # que hay que hacer con ella. Doce enlaces seguidos y sin jerarquía son doce
    # candidatos igual de probables, y la mitad de ellos no se leen nunca en la
    # primera pasada.
    #
    # La marca del corte es una clase en el primer enlace del grupo, no un
    # elemento suelto entre dos: un separador con entidad propia se queda colgado
    # al final de la fila en cuanto la barra envuelve. El detalle está en la hoja
    # de estilo, en `.ini-grupo`.
    nav = f'<a href="#resumen" data-target="resumen">{_icon("i-zap")}Resumen</a>'
    separado = False
    for s in secs:
        corte = ""
        if s.grupo.startswith("accion") and not separado:
            corte, separado = ' class="ini-grupo"', True
        # Punto de severidad en la propia navegación: dónde está lo urgente sin
        # tener que entrar a mirar sección por sección.
        punto = f'<span class="sev s-{_e(s.severity)}"></span>' if s.severity else ""
        nav += (f'<a href="#{s.sid}"{corte} data-target="{s.sid}" title="{_e(s.label)}">'
                f"{_icon(s.icon)}{_e(NAV_LABELS.get(s.sid, s.label))}{punto}</a>")
    body = "".join(_section(s) for s in secs)

    # Bandeja de selección: mientras se eligen secciones hay que poder ver
    # cuáles van y quitar una sin salir a buscarla por la página.
    presets = (
        f'<div class="presets">'
        f'<button type="button" data-preset="all">{_icon("i-check", "ic sm")}Todo</button>'
        f'<button type="button" data-preset="none">{_icon("i-x", "ic sm")}Ninguno</button>'
        f'<button type="button" data-preset="accion">{_icon("i-alert", "ic sm")}'
        f"Solo lo accionable</button>"
        f'<button type="button" data-preset="tecnico">{_icon("i-layers", "ic sm")}'
        f"Solo diagnóstico técnico</button></div>"
    )
    tray = (
        f'<aside class="tray" id="tray" aria-label="Secciones seleccionadas para exportar">'
        f'<h4>{_icon("i-download", "ic sm")}Se exportarán'
        f'<span class="n" id="tray-count">0</span></h4>'
        f'<ul id="tray-list"></ul>{presets}'
        f'<div class="row"><button class="btn gold" type="button" id="tray-export">'
        f'{_icon("i-download")}Descargar</button>'
        f'<button class="btn" type="button" data-preset="none">Vaciar</button></div></aside>'
    )

    date = f"{datetime.now():%d/%m/%Y %H:%M}"
    html = (
        '<!DOCTYPE html>\n<html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Informe de rendimiento · {_e(si.hostname)}</title>"
        f"{_favicon()}"
        f"<style>{HTML_CSS}</style></head>"
        f'<body data-host="{_e(_slug(si.hostname))}" data-stamp="{date}">'
        f"{_sprite()}"
        # Lo primero que encuentra el tabulador, antes del logo y de la
        # navegación entera. Sin esto, llegar al informe con teclado obliga a
        # pasar por los ~12 enlaces de la barra superior en cada carga, y quien
        # usa lector de pantalla los oye todos. Va con la clase `.vh`, que ya
        # existe: invisible hasta que recibe el foco.
        '<a class="vh skip" href="#contenido">Saltar al contenido</a>'
        '<header class="topbar"><div class="prog"></div><div class="tb">'
        f'<a class="logo" href="#resumen" data-target="resumen">{_logo()}'
        "<span>Quilate <b>Suite</b></span></a>"
        f"<nav>{nav}</nav>"
        '<button class="btn" type="button" id="export-sel" disabled '
        'title="Marca secciones con su casilla para exportarlas juntas en un solo fichero">'
        f'{_icon("i-download")}<span>Exportar (0)</span></button>'
        '<button class="btn" type="button" id="toggle-all" data-open="1">'
        f'{_icon("i-list")}<span>Colapsar todo</span></button>'
        "</div>"
        f'<div class="crumb"><div class="cin">{_icon("i-chev", "ic sm")}'
        '<span>Estás en</span><b id="crumb-name">Resumen</b>'
        '<span class="pct" id="crumb-pct">0% leído</span></div></div>'
        "</header>"
        '<div class="layout">'
        f"{_sidebar(si, bench, auditor, projection, secs)}"
        '<main class="main" id="contenido">'
        '<header class="page">'
        f'{_logo("brandmark hero")}'
        '<div class="htxt"><div class="kicker">Quilate Suite</div>'
        "<h1>Informe de rendimiento y optimización</h1>"
        # Solo la fecha. El equipo y el sistema salían aquí, en el panel lateral
        # y otra vez en Inventario: tres veces los mismos dos datos en la primera
        # pantalla. Quien recibe un extracto suelto sigue viendo de qué equipo es
        # por el <title>, que lleva el nombre del host.
        f'<div class="meta">Generado el {date}</div>'
        "</div></header>"
        f"{_hero(bench, auditor, projection)}{_component_strip(bench)}{body}"
        "</main></div>"
        f'<footer><span class="fbrand">{_logo("brandmark foot")}'
        f"{_e(APP_NAME)} v{APP_VERSION} · escala de referencia: "
        f"100 pts = gama media reciente</span>"
        f'<span>{_e(AUTHOR)} — <a href="{WEBSITE_URL}">{_e(WEBSITE)}</a></span></footer>'
        f"{tray}"
        '<button class="btn" type="button" id="top" title="Volver arriba" '
        "onclick=\"window.scrollTo({top:0,behavior:'smooth'})\">"
        f'{_icon("i-up")}</button>'
        f"<script>{HTML_JS}</script></body></html>"
    )
    path.write_text(html, encoding="utf-8")
