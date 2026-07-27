"""Proyeccion de mejora: combina ganancias y las aplica a las notas medidas."""

from __future__ import annotations

from typing import Any

from .audit import Finding, SEVERITY_ORDER
from .benchmark import Benchmark, SCORE_CAP, WEIGHTS


def combine_gains(gains: list[float]) -> float:
    """Combina mejoras con rendimientos decrecientes: 1 - Π(1 - g)."""
    remaining = 1.0
    for g in gains:
        remaining *= (1 - max(0.0, min(0.9, g)))
    return 1 - remaining


def project_improvement(bench: Benchmark | None, findings: list[Finding]) -> dict[str, Any]:
    by_component: dict[str, list[float]] = {}
    by_category: dict[str, list[float]] = {}
    for f in findings:
        if f.gain <= 0:
            continue
        by_component.setdefault(f.component, []).append(f.gain)
        by_category.setdefault(f.category, []).append(f.gain)

    component_gain = {k: combine_gains(v) for k, v in by_component.items()}
    category_gain = {k: combine_gains(v) for k, v in by_category.items()}

    result: dict[str, Any] = {
        "component_gain": component_gain,
        "category_gain": category_gain,
        "system_gain": component_gain.get("system", 0.0),
    }

    if bench:
        current = bench.component_scores()
        projected = {}
        for key, score in current.items():
            gain = component_gain.get(key, 0.0)
            projected[key] = score * (1 + gain)
        cur_overall = bench.overall()
        total_w = sum(WEIGHTS[k] for k in projected) or 1.0
        # Mismo techo que en overall(): sin él, un componente con una medida
        # anómala (caché, ramdisk) convierte la proyección en un número absurdo.
        proj_overall = sum(min(projected[k], SCORE_CAP) * WEIGHTS[k]
                           for k in projected) / total_w
        # Las mejoras de "system" no aparecen en los scores sintéticos pero sí en la experiencia
        experiential = proj_overall * (1 + component_gain.get("system", 0.0) * 0.5)
        result.update({
            "current_components": current,
            "projected_components": projected,
            "current_overall": cur_overall,
            "projected_overall": proj_overall,
            "projected_experiential": experiential,
            "headroom_pct": (proj_overall / cur_overall - 1) * 100 if cur_overall else 0.0,
            "experiential_pct": (experiential / cur_overall - 1) * 100 if cur_overall else 0.0,
        })
    return result


def priority_rank(f: Finding) -> tuple:
    effort_w = {"bajo": 0, "medio": 1, "alto": 2}.get(f.effort, 1)
    return (-f.gain / (1 + effort_w * 0.6), SEVERITY_ORDER.get(f.severity, 9))
