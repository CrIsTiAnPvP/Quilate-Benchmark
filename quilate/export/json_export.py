"""Exportacion a JSON: datos crudos para comparar ejecuciones."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..audit import Auditor
from ..benchmark import (Benchmark, REFERENCE, REFERENCE_DATE, REFERENCE_MACHINE,
                         REFERENCE_ORIGIN, reference_age_months, reference_is_stale)
from ..components import build_component_cards
from ..const import APP_NAME, APP_VERSION, AUTHOR, WEBSITE_URL
from ..report import build_verdict
from ..sensors import cpu_temperature, gpu_telemetry, temperature_report, temperature_source
from ..storage_scan import ScanResult, candidate_bytes
from ..sysinfo import SystemInfo


def _scan_payload(scan: ScanResult | None) -> dict | None:
    if scan is None:
        return None
    safe, review = candidate_bytes(scan)
    data = asdict(scan)
    data["reclaimable_safe"] = safe
    data["reclaimable_review"] = review
    return data


def build_payload(si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                  projection: dict[str, Any]) -> dict[str, Any]:
    """Los datos crudos de una ejecución.

    Está separado del volcado a fichero porque el histórico guarda un resumen
    de esto mismo, y duplicar la construcción garantizaría que las dos versiones
    se separaran a la primera de cambio.
    """
    payload = {
        "meta": {
            "tool": APP_NAME, "version": APP_VERSION, "author": AUTHOR, "website": WEBSITE_URL,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "quick": bool(bench and bench.quick),
        },
        "system": asdict(si),
        "reference_baseline": REFERENCE,
        # La escala con la que se han dado las notas, fechada: sin esto, un JSON
        # de hace tres años parece comparable con uno de hoy y no lo es.
        "reference_meta": {
            "date": REFERENCE_DATE,
            "machine": REFERENCE_MACHINE,
            "age_months": reference_age_months(),
            "stale": reference_is_stale(),
            "origin": REFERENCE_ORIGIN,
        },
        "network": getattr(auditor, "network", {}),
        "gpu": (bench.gpu_info if bench else {}) or
               {"unavailable": (bench.gpu_unavailable if bench else "")},
        "benchmark": {k: asdict(v) for k, v in (bench.results.items() if bench else [])},
        "metrics": bench.metrics if bench else {},
        "memory_hierarchy": bench.memory_hierarchy if bench else [],
        "load_snapshots": bench.load_snapshots if bench else [],
        # Sin el margen y sin saber qué más estaba corriendo, dos ejecuciones
        # se pueden restar pero no comparar.
        "dispersion": getattr(bench, "dispersion", {}) if bench else {},
        "ambient_load": getattr(bench, "ambient_load", {}) if bench else {},
        "scores": {
            "components": bench.component_scores() if bench else {},
            "overall": bench.overall() if bench else None,
        },
        "components": [asdict(c) for c in build_component_cards(si, bench, auditor)],
        "findings": [asdict(f) for f in auditor.findings],
        # Sin esto, «0 hallazgos» y «0 hallazgos sobre 5 comprobaciones ciegas»
        # producían exactamente el mismo JSON.
        "coverage": {
            "checks_conclusive": auditor.checks_run,
            "checks_total": getattr(auditor, "checks_total", auditor.checks_run),
            "unverified": [{"check": c, "reason": r}
                           for c, r in getattr(auditor, "unverified", [])],
            "not_applicable": [{"check": c, "reason": r}
                               for c, r in getattr(auditor, "not_applicable", [])],
        },
        "projection": projection,
        # La conclusión en una frase. La daban la consola y el HTML, y el JSON
        # obligaba a reconstruirla: quien procese estos datos merece leer la
        # misma lectura que el usuario, no una propia.
        "verdict": dict(zip(("summary", "notes"), build_verdict(si, bench, auditor, projection))),
        "notes": list(getattr(auditor, "notes", [])),
        "storage_scan": _scan_payload(getattr(auditor, "scan", None)),
        "sensors": {
            "cpu_temperature": cpu_temperature(),
            "cpu_temperature_source": temperature_source(),
            "cpu_temperature_attempts": [{"source": s, "result": r}
                                         for s, r in temperature_report()],
            "gpu": gpu_telemetry(),
        },
        "top_processes": getattr(auditor, "top_processes", []),
        "startup_items": getattr(auditor, "startup_items", []),
        # Se vuelca el evento crudo a propósito: si el esquema del log cambia en
        # una versión de Windows, el diagnóstico está en el informe del usuario.
        "boot": {
            "seconds": getattr(auditor, "boot_seconds", None),
            "report": getattr(auditor, "boot_report", {}),
        },
    }
    return payload


def export_json(path: Path, si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                projection: dict[str, Any]) -> None:
    payload = build_payload(si, bench, auditor, projection)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
