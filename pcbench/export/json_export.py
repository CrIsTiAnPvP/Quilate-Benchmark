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
from ..sysinfo import SystemInfo


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
        "scores": {
            "components": bench.component_scores() if bench else {},
            "overall": bench.overall() if bench else None,
        },
        "components": [asdict(c) for c in build_component_cards(si, bench, auditor)],
        "findings": [asdict(f) for f in auditor.findings],
        "projection": projection,
        "top_processes": getattr(auditor, "top_processes", []),
        "startup_items": getattr(auditor, "startup_items", []),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
