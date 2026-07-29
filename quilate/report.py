"""Informe de consola: inventario, benchmark, ficha, hallazgos y veredicto."""

from __future__ import annotations

import platform
import sys
from datetime import datetime
from typing import Any

from .audit import Auditor, SEVERITY_ORDER, security_findings, sev_label
from .benchmark import (Benchmark, PY_ADJUST, REFERENCE_DATE, REFERENCE_MACHINE,
                        reference_age_months, reference_is_stale)
from .components import ComponentCard, _no_score_text, build_component_cards
from .console import (BOX_W, C, _wrap, bar, component_label, grade, human_bytes,
                      kv, section)
from .const import APP_NAME, APP_VERSION, AUTHOR, WEBSITE_URL
from .projection import priority_rank
from .storage_scan import RECLAIMABLE, REVIEWABLE, ScanResult, candidate_bytes
from .sysinfo import SystemInfo, gpu_label


def print_component_cards(cards: list[ComponentCard]) -> None:
    section("Ficha por componente")
    print(f"  {C.DIM}Cada pieza con su inventario, la nota que ha sacado y las mejoras "
          f"que le corresponden.{C.RESET}\n")
    for card in cards:
        if card.score is not None:
            letter, color = grade(card.score)
            head = f"{color}{C.BOLD}{card.score:>5.0f} pts  ·  {letter.ljust(2)}{C.RESET} " \
                   f"{bar(card.score, 18)}"
        else:
            head = f"{C.GREY}{_no_score_text(card)}{C.RESET}"
        print(f"  {C.BOLD}{card.label.upper().ljust(22)}{C.RESET} {head}")

        for k, v in card.specs:
            print(f"      {C.GREY}{str(k).ljust(24, '.')}{C.RESET} {v}")

        if card.tests:
            print(f"      {C.CYAN}Pruebas medidas:{C.RESET}")
            for r in card.tests:
                measure = f"{r.raw:,.0f} {r.unit}" if r.unit == "IOPS" else f"{r.raw:,.2f} {r.unit}"
                letter, color = grade(r.score)
                print(f"        {r.name.ljust(22)}{measure.rjust(16)}   "
                      f"{color}{r.score:>5.0f} pts  {letter}{C.RESET}")

        if card.findings:
            total = f"  ·  {C.GREEN}+{card.gain * 100:.0f}% combinado{C.RESET}" if card.gain > 0.005 else ""
            print(f"      {C.CYAN}Mejoras aplicables ({len(card.findings)}){C.RESET}{total}")
            for f in card.findings:
                tag = (f"{C.GREEN}+{f.gain * 100:>3.0f}%{C.RESET}" if f.gain > 0
                       else f"{C.GREY} n/a{C.RESET}")
                print(f"        {tag}  {f.title}")
                print(f"              {sev_label(f.severity)}  {C.DIM}·  esfuerzo {f.effort}"
                      f"  ·  riesgo {f.risk}{C.RESET}")
            if card.projected_score is not None and card.gain > 0.005:
                print(f"        {C.DIM}→ tras aplicarlas: {C.RESET}{C.GREEN}"
                      f"{card.projected_score:.0f} pts{C.RESET}")
        elif not card.riesgos:
            # Solo si no hay ni mejoras ni riesgos. Un «sin nada pendiente» en
            # verde sobre un equipo con SMB1 activo y sin cifrar es el aviso que
            # no hay que dar, y aquí pasaba lo mismo que en el HTML.
            print(f"      {C.GREEN}Sin mejoras pendientes.{C.RESET}")

        if card.riesgos:
            print(f"      {C.RED}Riesgos de este componente "
                  f"({len(card.riesgos)}){C.RESET}"
                  f"{C.DIM}  ·  no suben la nota: ver «Seguridad»{C.RESET}")
            for f in card.riesgos:
                print(f"        {sev_label(f.severity)}  {f.title}")
        print()


def print_storage_scan(scan: ScanResult | None) -> None:
    """Lo encontrado por el rastreo de archivos grandes."""
    if scan is None or not scan.available:
        return
    section("Archivos grandes")
    cover = (f"{scan.scanned_files:,} ficheros y {scan.scanned_dirs:,} carpetas en "
             f"{scan.elapsed:.0f} s".replace(",", "."))
    print(f"  {C.DIM}Umbral: {human_bytes(scan.min_size)} · revisados {cover}{C.RESET}")
    if scan.truncated:
        print(f"  {C.YELLOW}Rastreo parcial: se agotó el presupuesto de tiempo. "
              f"Amplíalo con --scan-time 60 para cubrir más.{C.RESET}")
    if not scan.files and not scan.special:
        print(f"\n  {C.GREEN}✓ Nada por encima del umbral. No hay grasa que recortar aquí."
              f"{C.RESET}")
        return

    if scan.by_category:
        safe, review = candidate_bytes(scan)
        print(f"\n  {C.BOLD}Por tipo{C.RESET}")
        for cat, data in scan.by_category.items():
            if cat in RECLAIMABLE:
                mark = f"{C.GREEN}se puede borrar{C.RESET}"
            elif cat in REVIEWABLE:
                mark = f"{C.YELLOW}revisar{C.RESET}"
            else:
                mark = ""
            print(f"    {C.GREY}{cat.capitalize().ljust(24, '.')}{C.RESET} "
                  f"{human_bytes(data['size']).rjust(10)}  "
                  f"{C.DIM}{data['count']} ficheros{C.RESET}  {mark}")
        print(f"    {C.GREY}{'TOTAL'.ljust(24, '.')}{C.RESET} "
              f"{human_bytes(scan.total_large).rjust(10)}")
        if safe or review:
            print(f"    {C.DIM}De ese total, {C.RESET}{C.GREEN}{human_bytes(safe)}{C.RESET}"
                  f"{C.DIM} es basura y {C.RESET}{C.YELLOW}{human_bytes(review)}{C.RESET}"
                  f"{C.DIM} son candidatos a revisar.{C.RESET}")

    if scan.files:
        print(f"\n  {C.BOLD}Los más grandes{C.RESET}")
        for f in scan.files[:12]:
            path = f["path"]
            if len(path) > 62:
                path = path[:30] + "…" + path[-31:]
            print(f"    {human_bytes(f['size']).rjust(9)}  {C.DIM}{f['category'][:20].ljust(21)}"
                  f"{f['age_days']:>5}d{C.RESET}  {path}")

    if scan.special:
        print(f"\n  {C.BOLD}Archivos de sistema{C.RESET}  {C.DIM}(no los borres a mano){C.RESET}")
        for s in scan.special:
            size = human_bytes(s["size"]) if s["size"] else "—"
            print(f"    {size.rjust(9)}  {s['name'].ljust(16)} {C.DIM}{s['note']}{C.RESET}")


def print_report(si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                 projection: dict[str, Any]) -> None:
    # --- Inventario ---
    section("Inventario del equipo")
    kv("Equipo", f"{si.hostname}{'  (portátil)' if si.is_laptop else ''}")
    kv("Sistema operativo", f"{si.os_name} · {si.os_build}")
    if si.os_install_date:
        kv("Instalado el", f"{si.os_install_date}  ({(si.os_age_days or 0) / 365.25:.1f} años)")
    kv("Tiempo encendido", f"{si.uptime_hours:.1f} h")
    kv("CPU", si.cpu_name)
    kv("Núcleos / hilos", f"{si.cpu_cores} / {si.cpu_threads}"
                          + (f"  ·  máx {si.cpu_max_mhz:.0f} MHz" if si.cpu_max_mhz else ""))
    ram_desc = f"{si.ram_total / 1024**3:.1f} GB"
    if si.ram_speed_mhz:
        ram_desc += f" @ {si.ram_speed_mhz} MT/s"
    if si.ram_channels:
        ram_desc += f" · {si.ram_channels} módulo(s)"
    kv("Memoria", ram_desc)
    for g in si.gpus:
        kv("GPU", f"{gpu_label(g)}  ·  driver {g.get('driver')} ({g.get('driver_date') or '?'})")
    kv("Disco de sistema", f"{si.system_drive}  ·  {si.system_drive_media}")
    for d in si.disks:
        if d["total"] > 5 * 1024**3:
            kv(f"  {d['mount']}",
               f"{human_bytes(d['free'])} libres de {human_bytes(d['total'])}  "
               f"({100 - d['percent']:.0f}% libre)  [{d['fstype']}]")
    kv("Privilegios", "administrador" if si.is_admin else
       f"{C.YELLOW}usuario estándar (algunas comprobaciones limitadas){C.RESET}")

    # --- Resultados del benchmark ---
    if bench and bench.results:
        section("Resultados del benchmark")
        # La descripción sale de la constante para que no pueda quedarse
        # describiendo un equipo distinto del que fija la escala.
        print(f"  {C.DIM}Escala: 100 pts = {REFERENCE_MACHINE}{C.RESET}")
        print(f"  {C.DIM}Fijada en {REFERENCE_DATE}{C.RESET}")
        note = f"Python {platform.python_version()}"
        if PY_ADJUST != 1.0:
            note += f" · factor de compensación de intérprete ×{PY_ADJUST}"
        print(f"  {C.DIM}{note}{C.RESET}")
        if getattr(sys, "frozen", False):
            # El arranque de los procesos hijo —caro en el .exe, donde cada uno
            # levanta el intérprete empaquetado— ya no entra en el cronómetro del
            # test multihilo: se hace antes, con una tarea vacía. Aun así conviene
            # comparar ejecuciones del mismo tipo.
            print(f"  {C.DIM}Ejecutándose desde el .exe. El arranque de los procesos hijo queda "
                  f"fuera de la medición, pero compara siempre .exe con .exe.{C.RESET}")
        print()
        if getattr(bench, "disk_on_ram", False):
            print(f"  {C.YELLOW}Los resultados de disco se midieron sobre un sistema de "
                  f"ficheros en RAM: ignóralos.{C.RESET}\n")
        if getattr(bench, "disk_cache_suspect", False):
            print(f"  {C.YELLOW}Parte del test de disco salía de la caché del sistema (medía "
                  f"RAM, no disco) y se ha excluido de la nota.{C.RESET}")
            print(f"  {C.DIM}Prueba con --disk-size mayor que la RAM libre, o --disk-path en "
                  f"otra unidad.{C.RESET}\n")
        if reference_is_stale():
            años = reference_age_months() / 12
            print(f"  {C.YELLOW}⚠ La escala de referencia se fijó en {REFERENCE_DATE} "
                  f"(hace {años:.1f} años).{C.RESET}")
            print(f"  {C.DIM}Sigue midiendo bien, pero «100 puntos = gama media» ya no "
                  f"describe lo que se vende hoy:\n  la nota está inflada respecto a un "
                  f"equipo actual.{C.RESET}\n")
        dispersion = getattr(bench, "dispersion", {})
        # Los cuatro subtests monohilo se agregan en una sola fila: su margen es
        # el del subtest que más bailó.
        equivalencias = {"cpu_single": ("sieve", "float", "hash", "compress")}
        print(f"  {'PRUEBA'.ljust(24)}{'MEDIDA'.rjust(16)}  {'±'.rjust(6)}  {'PTS'.rjust(5)}  NOTA")
        print(f"  {C.GREY}{'─' * (BOX_W - 2)}{C.RESET}")
        for key, res in bench.results.items():
            letter, color = grade(res.score)
            measure = f"{res.raw:,.2f} {res.unit}" if res.unit != "IOPS" else f"{res.raw:,.0f} IOPS"
            relacionadas = [dispersion[k] for k in equivalencias.get(key, (key,))
                            if k in dispersion]
            margen = " " * 6
            if relacionadas:
                peor = max(relacionadas, key=lambda d: d["spread_pct"])
                tono = C.GREY if peor["stable"] else C.YELLOW
                margen = f"{tono}{peor['spread_pct']:>5.0f}%{C.RESET}"
            print(f"  {res.name.ljust(24)}{measure.rjust(16)}  {margen}  "
                  f"{color}{res.score:>5.0f}{C.RESET}  {color}{letter.ljust(3)}{C.RESET}"
                  f"{bar(res.score, 14)}")
            if res.detail:
                print(f"    {C.GREY}↳ {res.detail}{C.RESET}")

        overall = bench.overall()
        letter, color = grade(overall)
        print(f"\n  {C.BOLD}PUNTUACIÓN GLOBAL{C.RESET}  "
              f"{color}{C.BOLD}{overall:.0f} pts   ·   nota {letter}{C.RESET}   {bar(overall, 24)}")

        comp = bench.component_scores()
        weakest = min(comp, key=lambda k: comp[k]) if comp else None
        if weakest:
            print(f"  {C.YELLOW}▸ Cuello de botella principal: {component_label(weakest)} "
                  f"({comp[weakest]:.0f} pts){C.RESET}")

        # --- Métricas que no puntúan pero explican la puntuación ---
        if bench.memory_hierarchy or bench.metrics:
            print(f"\n  {C.BOLD}Métricas de diagnóstico{C.RESET}")
            if bench.memory_hierarchy:
                levels = "   ".join(f"{lv['level']} {C.CYAN}{lv['gbs']:.0f}{C.RESET} GB/s"
                                    for lv in bench.memory_hierarchy)
                print(f"    {C.GREY}{'Jerarquía de memoria'.ljust(26, '.')}{C.RESET} {levels}")
            for m in bench.metrics.values():
                value = f"{m['value']} {m['unit']}".strip()
                print(f"    {C.GREY}{m['label'].ljust(26, '.')}{C.RESET} {value}")
                if m["note"]:
                    for line in _wrap(m["note"], 62):
                        print(f"      {C.DIM}{line}{C.RESET}")

    # --- Ficha por componente ---
    print_component_cards(build_component_cards(si, bench, auditor))

    # --- Archivos grandes ---
    print_storage_scan(getattr(auditor, "scan", None))

    # --- Hallazgos ---
    section("Hallazgos de la auditoría")
    findings = sorted(auditor.findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.gain))
    if not findings:
        print(f"  {C.GREEN}✓ No se han detectado problemas de configuración relevantes "
              f"en las {auditor.checks_run} comprobaciones que sí pudieron completarse.{C.RESET}")
    else:
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        # `.get` y no indexación: ver el recuento hermano de `html_export`. Una
        # severidad no declarada ordena la última en vez de tirar el informe.
        summary = "  ".join(f"{sev_label(s)}: {n}" for s, n in
                            sorted(counts.items(),
                                   key=lambda x: SEVERITY_ORDER.get(x[0], 9)))
        print(f"  {auditor.checks_run} comprobaciones · {len(findings)} hallazgos     {summary}\n")
        for i, f in enumerate(findings, 1):
            sev = sev_label(f.severity)
            print(f"  {C.BOLD}{i}. {f.title}{C.RESET}")
            print(f"     {sev}  {C.GREY}·{C.RESET}  categoría: {f.category}  "
                  f"{C.GREY}·{C.RESET}  esfuerzo: {f.effort}  "
                  f"{C.GREY}·{C.RESET}  riesgo: {f.risk}")
            if f.gain > 0:
                print(f"     {C.GREEN}Mejora estimada: +{f.gain * 100:.0f}%{C.RESET} "
                      f"{C.DIM}({f.gain_note}){C.RESET}")
            for line in _wrap(f.detail, 70):
                print(f"     {C.DIM}{line}{C.RESET}")
            if f.steps:
                print(f"     {C.CYAN}Cómo solucionarlo:{C.RESET}")
                for s in f.steps:
                    for j, line in enumerate(_wrap(s, 66)):
                        print(f"       {'•' if j == 0 else ' '} {line}")
            print()

    # --- Proyección ---
    section("Proyección de mejora")
    if projection.get("current_overall"):
        cur = projection["current_overall"]
        proj = projection["projected_overall"]
        exp = projection["projected_experiential"]
        print(f"  {'Puntuación actual'.ljust(34)} {C.BOLD}{cur:>6.0f} pts{C.RESET}  {bar(cur, 22)}")
        print(f"  {'Tras optimizar (sintética)'.ljust(34)} {C.GREEN}{C.BOLD}{proj:>6.0f} pts{C.RESET}"
              f"  {bar(proj, 22)}   {C.GREEN}+{projection['headroom_pct']:.0f}%{C.RESET}")
        print(f"  {'Fluidez percibida estimada'.ljust(34)} {C.GREEN}{C.BOLD}{exp:>6.0f} pts{C.RESET}"
              f"  {bar(exp, 22)}   {C.GREEN}+{projection['experiential_pct']:.0f}%{C.RESET}")
        print()
        for key, score in projection.get("current_components", {}).items():
            gain = projection["component_gain"].get(key, 0.0)
            if gain > 0.005:
                print(f"    {component_label(key).ljust(20)} {score:>5.0f} → "
                      f"{C.GREEN}{projection['projected_components'][key]:>5.0f} pts "
                      f"(+{gain * 100:.0f}%){C.RESET}")
        sysgain = projection.get("system_gain", 0.0)
        if sysgain > 0.005:
            print(f"    {'Arranque / fluidez'.ljust(20)} {C.GREY}sin métrica sintética{C.RESET} → "
                  f"{C.GREEN}+{sysgain * 100:.0f}% estimado{C.RESET}")

    if projection.get("category_gain"):
        print(f"\n  {C.BOLD}Margen por área:{C.RESET}")
        for cat, gain in sorted(projection["category_gain"].items(), key=lambda x: -x[1]):
            print(f"    {cat.capitalize().ljust(18)} {C.GREEN}+{gain * 100:>5.0f}%{C.RESET} "
                  f"{bar(min(100, gain * 100), 20)}")

    # --- Seguridad ---
    # Antes del plan y no dentro: el plan promete retorno y estos hallazgos no
    # lo dan. Van primero porque un disco sin cifrar importa más que ocho puntos
    # de fluidez, aunque no haya forma de ponerle un porcentaje.
    riesgos = security_findings(auditor.findings)
    if riesgos:
        section("Seguridad")
        print(f"  {C.DIM}Esto no acelera el equipo: son riesgos. No entran en la "
              f"proyección\n  de mejora porque no hay mejora que proyectar.{C.RESET}\n")
        for f in riesgos:
            print(f"  {sev_label(f.severity)}  {C.BOLD}{f.title}{C.RESET}")
            for linea in _wrap(f.detail, BOX_W - 8):
                print(f"      {C.DIM}{linea}{C.RESET}")
            for paso in f.steps:
                print(f"      {C.GREY}·{C.RESET} {paso}")
            print()

    # --- Plan de acción ---
    section("Plan de acción priorizado")
    actionable = [f for f in auditor.findings if f.gain > 0]
    if not actionable:
        print(f"  {C.GREEN}Nada que priorizar: no hay acciones con retorno estimado.{C.RESET}")
    else:
        print(f"  {C.DIM}Ordenado por retorno estimado dividido por esfuerzo. "
              f"Aplica de arriba hacia abajo y vuelve a medir tras cada bloque.{C.RESET}\n")
        for i, f in enumerate(sorted(actionable, key=priority_rank), 1):
            tag = {"bajo": C.GREEN, "medio": C.YELLOW, "alto": C.RED}.get(f.effort, "")
            print(f"  {C.BOLD}{i:>2}.{C.RESET} {f.title}")
            print(f"      {C.GREEN}+{f.gain * 100:>3.0f}%{C.RESET}  "
                  f"esfuerzo {tag}{f.effort}{C.RESET}  ·  riesgo {f.risk}  ·  {f.category}")
        print(f"\n  {C.DIM}Recuerda: mide siempre antes y después. Aplicar diez cambios a la vez "
              f"impide saber cuál funcionó.{C.RESET}")

    # --- Red ---
    red = getattr(auditor, "network", {}) or {}
    if red.get("connected"):
        section("Red")
        for a in red["connected"]:
            velocidad = f"{a['link_mbps']:,.0f} Mbps" if a.get("link_mbps") else "sin negociar"
            kv(a["name"], f"{velocidad}  ·  {a['description']}")
        wifi = red.get("wifi") or {}
        if wifi:
            detalle = [wifi.get("radio", "wifi")]
            if wifi.get("band_ghz"):
                detalle.append(f"{wifi['band_ghz']} GHz")
            if wifi.get("channel"):
                detalle.append(f"canal {wifi['channel']}")
            if wifi.get("rssi_dbm") is not None:
                detalle.append(f"{wifi['rssi_dbm']} dBm ({wifi.get('signal_pct', '?')}%)")
            kv("Enlace wifi", "  ·  ".join(detalle))
        for destino in (red.get("latency") or {}).get("targets", []):
            marca = f"{destino['median_ms']:.1f} ms" if destino["median_ms"] else "sin respuesta"
            extra = f"  ·  {destino['loss_pct']}% de pérdida" if destino["loss_pct"] else ""
            kv(f"Latencia · {destino['name']}", marca + extra)
        dns = red.get("dns") or {}
        if dns.get("median_ms"):
            kv("Resolución DNS", f"{dns['median_ms']:.1f} ms")
        if not red.get("active"):
            print(f"  {C.DIM}Latencia y DNS no se han medido: se ejecutó con --no-net, "
                  f"que evita contactar con servidores externos.{C.RESET}")

    # --- Lo que no se ha podido comprobar ---
    # Va antes del veredicto a propósito: es la advertencia de hasta dónde llega
    # lo que el informe puede afirmar.
    unverified = getattr(auditor, "unverified", [])
    if unverified:
        section("Sin comprobar")
        print(f"  {C.DIM}Estas comprobaciones no llegaron a un veredicto. No significan "
              f"«correcto»: significan que no hay dato.{C.RESET}\n")
        for label, reason in unverified:
            print(f"  {C.YELLOW}?{C.RESET} {C.BOLD}{label}{C.RESET}")
            for line in _wrap(reason, 68):
                print(f"    {C.DIM}{line}{C.RESET}")
        skipped = getattr(auditor, "not_applicable", [])
        if skipped:
            print(f"\n  {C.DIM}No aplican a este equipo: "
                  f"{', '.join(label for label, _ in skipped)}.{C.RESET}")

    if auditor.notes:
        print(f"\n  {C.YELLOW}Notas:{C.RESET}")
        for n in auditor.notes:
            for line in _wrap(n, 72):
                print(f"    {C.DIM}{line}{C.RESET}")

    # --- Veredicto ---
    section("Veredicto")
    verdict, extra = build_verdict(si, bench, auditor, projection)
    for line in _wrap(verdict, 74):
        print(f"  {line}")
    for e in extra:
        print(f"  {C.CYAN}▸{C.RESET} {e}")

    print(f"\n{C.GREY}{'─' * BOX_W}{C.RESET}")
    print(f"{C.GOLD}{APP_NAME} v{APP_VERSION}{C.RESET}  ·  {AUTHOR}  ·  "
          f"{C.CYAN}{WEBSITE_URL}{C.RESET}")
    print(f"{C.DIM}Informe generado el {datetime.now():%d/%m/%Y %H:%M}{C.RESET}\n")


def build_verdict(si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                  projection: dict[str, Any]) -> tuple[str, list[str]]:
    ids = {f.id for f in auditor.findings}
    extra: list[str] = []

    if "smart_warn" in ids:
        return ("Antes de cualquier optimización: hay un disco con salud degradada. Haz copia de "
                "seguridad ahora. Optimizar un equipo con un disco a punto de fallar es perder "
                "el tiempo en el mejor caso y perder datos en el peor.", extra)

    if "hdd_system" in ids:
        extra.append("Prioridad absoluta: migrar el sistema a un SSD. Todo lo demás es secundario.")
        return ("El cuello de botella es físico, no de configuración. Con el sistema en un disco "
                "mecánico, los ajustes de registro y la limpieza de inicio aportarán mejoras "
                "marginales. Un SSD de 500 GB cuesta poco y multiplica la respuesta del equipo.",
                extra)

    if "thermal_critical" in ids or "freq_low" in ids:
        extra.append("Empieza por la refrigeración: limpieza y pasta térmica antes de tocar software.")
        return ("El equipo no está entregando el rendimiento de su hardware por motivos térmicos o "
                "de límites de potencia. Reinstalar Windows no cambiaría nada aquí.", extra)

    headroom = projection.get("headroom_pct", 0.0)
    exp = projection.get("experiential_pct", 0.0)
    critical = sum(1 for f in auditor.findings if f.severity in ("critical", "high"))

    if critical == 0 and headroom < 8:
        extra.append("Mantenimiento: limpieza física anual y drivers al día. Nada más que hacer.")
        return ("El equipo está bien ajustado. El margen de mejora por software es residual: si "
                "necesitas más rendimiento, el camino es hardware (RAM, SSD más rápido, CPU/GPU), "
                "no optimización.", extra)

    if "os_stale" in ids and critical >= 2:
        extra.append("Orden recomendado: 1) limpieza de inicio y espacio  2) medir de nuevo  "
                     "3) reinstalación limpia solo si sigue sin ir bien.")
        return (f"Hay margen real de mejora (aproximadamente +{exp:.0f}% en fluidez percibida). La "
                "instalación es antigua y está cargada, pero prueba primero los cambios reversibles: "
                "una reinstalación cuesta varias horas y conviene reservarla para cuando lo barato "
                "ya se ha agotado.", extra)

    extra.append("Aplica el plan de acción de arriba hacia abajo y vuelve a ejecutar este script "
                 "para verificar cada cambio.")
    return (f"Hay margen de mejora sin tocar hardware: se estima en torno a un +{headroom:.0f}% en "
            f"puntuación sintética y +{exp:.0f}% en fluidez percibida. La mayoría son cambios de "
            "esfuerzo bajo y reversibles.", extra)
