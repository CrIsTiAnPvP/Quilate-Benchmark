"""Exportacion a JSON: datos crudos para comparar ejecuciones."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..audit import Auditor
from ..benchmark import Benchmark, REFERENCE
from ..components import build_component_cards
from ..const import APP_NAME, APP_VERSION, AUTHOR, WEBSITE_URL
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


def export_json(path: Path, si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                projection: dict[str, Any]) -> None:
    payload = {
        "meta": {
            "tool": APP_NAME, "version": APP_VERSION, "author": AUTHOR, "website": WEBSITE_URL,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "system": asdict(si),
        "reference_baseline": REFERENCE,
        "benchmark": {k: asdict(v) for k, v in (bench.results.items() if bench else [])},
        "metrics": bench.metrics if bench else {},
        "memory_hierarchy": bench.memory_hierarchy if bench else [],
        "load_snapshots": bench.load_snapshots if bench else [],
        "scores": {
            "components": bench.component_scores() if bench else {},
            "overall": bench.overall() if bench else None,
        },
        "components": [asdict(c) for c in build_component_cards(si, bench, auditor)],
        "findings": [asdict(f) for f in auditor.findings],
        "projection": projection,
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
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
