"""Comparación de dos ejecuciones guardadas en JSON.

Quilate estima cuánto mejoraría el equipo si se aplican sus recomendaciones.
Esa cifra era hasta ahora una promesa que nadie contrastaba: se aplicaba el
plan, se volvía a medir, y la comparación se hacía a ojo entre dos informes.

Aquí se hace la resta, pero sobre todo se hace la parte que importa: decidir si
la diferencia significa algo. Una mejora del 3% entre dos ejecuciones cuyas
medidas bailan un 12% cada una no es una mejora, es ruido con buena prensa. Y
la proyección de la ejecución antigua se enfrenta a lo que realmente pasó, que
es la única forma de saber si el modelo acierta.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .console import COMPONENT_LABELS   # noqa: F401  (reexportado: lo usa compare_report)

# Margen que se supone cuando el JSON no trae dispersión (ejecuciones anteriores
# a la v2.2). Es deliberadamente amplio: sin dato de margen, afirmar que una
# diferencia pequeña es real sería exactamente el error que esto viene a evitar.
MARGEN_DESCONOCIDO_PCT = 10.0


class RunLoadError(Exception):
    """El fichero no es un JSON de Quilate utilizable."""


def load_run(path: Path) -> dict:
    try:
        datos = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RunLoadError(f"no existe: {path}") from None
    except json.JSONDecodeError as exc:
        raise RunLoadError(f"{path} no es un JSON válido ({exc.msg})") from None
    if not isinstance(datos, dict) or "meta" not in datos or "scores" not in datos:
        raise RunLoadError(f"{path} no parece un export de Quilate "
                           "(falta «meta» o «scores»)")
    return datos


def _margen(run: dict, clave: str) -> float | None:
    """Dispersión relativa registrada para una prueba, en %."""
    d = (run.get("dispersion") or {}).get(clave)
    if not d:
        return None
    return float(d.get("spread_pct") or 0.0)


def _margen_combinado(antes: dict, despues: dict, clave: str) -> tuple[float, bool]:
    """Umbral por debajo del cual una diferencia no es distinguible del ruido.

    Se suman los semirrecorridos de las dos ejecuciones en vez de combinarlos en
    cuadratura: es el criterio conservador, y aquí equivocarse por exceso solo
    cuesta un «no concluyente», mientras que equivocarse por defecto significa
    celebrar una mejora que no existe.
    """
    a, d = _margen(antes, clave), _margen(despues, clave)
    conocido = a is not None and d is not None
    umbral = ((MARGEN_DESCONOCIDO_PCT if a is None else a)
              + (MARGEN_DESCONOCIDO_PCT if d is None else d)) / 2
    if not conocido:
        # Promediar el margen supuesto con uno conocido y pequeño lo suaviza y
        # acaba dando por buena una diferencia que nadie puede respaldar. Si
        # falta el dato de una de las dos, el supuesto manda como suelo.
        umbral = max(umbral, MARGEN_DESCONOCIDO_PCT)
    return umbral, conocido


def _delta_pct(antes: float, despues: float) -> float | None:
    if not antes:
        return None
    return (despues - antes) / antes * 100


# Pruebas donde bajar es mejorar (ninguna por ahora: todas son «más es mejor»),
# declarado explícitamente para que añadir una latencia no invierta el signo sin
# que nadie se dé cuenta.
MENOS_ES_MEJOR: set[str] = set()


def _veredicto(delta: float | None, umbral: float, clave: str) -> str:
    if delta is None:
        return "sin base"
    if clave in MENOS_ES_MEJOR:
        delta = -delta
    if abs(delta) <= umbral:
        return "dentro del margen"
    return "mejora" if delta > 0 else "empeora"


def comparar_pruebas(antes: dict, despues: dict) -> list[dict]:
    """Una fila por prueba del benchmark, con su veredicto."""
    ba = antes.get("benchmark") or {}
    bd = despues.get("benchmark") or {}
    filas = []
    for clave in sorted(set(ba) | set(bd)):
        ra, rd = ba.get(clave), bd.get(clave)
        if not ra or not rd:
            filas.append({"key": clave,
                          "label": (ra or rd).get("name", clave),
                          "unit": (ra or rd).get("unit", ""),
                          "before": (ra or {}).get("raw"),
                          "after": (rd or {}).get("raw"),
                          "delta_pct": None, "threshold": 0.0,
                          "margin_known": False,
                          "verdict": "solo en una de las dos"})
            continue
        umbral, conocido = _margen_combinado(antes, despues, clave)
        delta = _delta_pct(float(ra["raw"]), float(rd["raw"]))
        filas.append({
            "key": clave,
            "label": rd.get("name", clave),
            "unit": rd.get("unit", ""),
            "before": float(ra["raw"]),
            "after": float(rd["raw"]),
            "delta_pct": delta,
            "threshold": umbral,
            "margin_known": conocido,
            "verdict": _veredicto(delta, umbral, clave),
        })
    return filas


def comparar_componentes(antes: dict, despues: dict) -> list[dict]:
    ca = (antes.get("scores") or {}).get("components") or {}
    cd = (despues.get("scores") or {}).get("components") or {}
    filas = []
    for clave in sorted(set(ca) | set(cd)):
        a, d = ca.get(clave), cd.get(clave)
        filas.append({
            "key": clave,
            "label": COMPONENT_LABELS.get(clave, clave),
            "before": a, "after": d,
            "delta_pct": _delta_pct(a, d) if a and d else None,
        })
    return filas


def comparar_hallazgos(antes: dict, despues: dict) -> dict[str, list[dict]]:
    """Qué se arregló, qué sigue y qué ha aparecido nuevo."""
    fa = {f["id"]: f for f in antes.get("findings") or []}
    fd = {f["id"]: f for f in despues.get("findings") or []}
    return {
        "resueltos": [fa[i] for i in sorted(set(fa) - set(fd))],
        "persisten": [fd[i] for i in sorted(set(fa) & set(fd))],
        "nuevos": [fd[i] for i in sorted(set(fd) - set(fa))],
    }


def _cobertura(run: dict) -> dict:
    cov = run.get("coverage") or {}
    return {
        "conclusive": cov.get("checks_conclusive"),
        "total": cov.get("checks_total"),
        "unverified": [u.get("check") for u in cov.get("unverified") or []],
    }


def mismo_equipo(antes: dict, despues: dict) -> tuple[bool, list[str]]:
    """Si las dos medidas son del mismo hardware. Comparar equipos distintos no
    está prohibido —sirve para contrastar dos máquinas— pero no puede pasar por
    un «antes y después»."""
    diferencias = []
    for campo, etiqueta in (("hostname", "equipo"), ("cpu_name", "CPU"),
                            ("ram_total", "RAM total")):
        a = (antes.get("system") or {}).get(campo)
        d = (despues.get("system") or {}).get(campo)
        if a and d and a != d:
            diferencias.append(f"{etiqueta}: «{a}» → «{d}»")
    return not diferencias, diferencias


def calibracion(antes: dict, despues: dict) -> dict | None:
    """Lo que la primera ejecución prometió frente a lo que de verdad pasó.

    Es la razón de ser de todo esto: sin contrastarla, la proyección de mejora
    es una afirmación que el propio programa nunca comprueba.
    """
    proj = antes.get("projection") or {}
    prometido = proj.get("projected_overall")
    partida = proj.get("current_overall") or (antes.get("scores") or {}).get("overall")
    logrado = (despues.get("scores") or {}).get("overall")
    if not (prometido and partida and logrado):
        return None
    margen_prometido = prometido - partida
    margen_logrado = logrado - partida
    return {
        "baseline": partida,
        "predicted": prometido,
        "achieved": logrado,
        "predicted_gain_pct": margen_prometido / partida * 100,
        "achieved_gain_pct": margen_logrado / partida * 100,
        # Qué fracción de la mejora prometida se materializó. Negativo significa
        # que el equipo fue a peor.
        "realised": (margen_logrado / margen_prometido) if margen_prometido else None,
    }


def compare_runs(antes: dict, despues: dict) -> dict[str, Any]:
    coincide, diferencias = mismo_equipo(antes, despues)
    return {
        "meta": {
            "before": {"generated_at": (antes.get("meta") or {}).get("generated_at"),
                       "version": (antes.get("meta") or {}).get("version")},
            "after": {"generated_at": (despues.get("meta") or {}).get("generated_at"),
                      "version": (despues.get("meta") or {}).get("version")},
            "same_machine": coincide,
            "machine_differences": diferencias,
        },
        "overall": {
            "before": (antes.get("scores") or {}).get("overall"),
            "after": (despues.get("scores") or {}).get("overall"),
            "delta_pct": _delta_pct((antes.get("scores") or {}).get("overall") or 0,
                                    (despues.get("scores") or {}).get("overall") or 0),
        },
        "tests": comparar_pruebas(antes, despues),
        "components": comparar_componentes(antes, despues),
        "findings": comparar_hallazgos(antes, despues),
        "coverage": {"before": _cobertura(antes), "after": _cobertura(despues)},
        "calibration": calibracion(antes, despues),
        "ambient": {"before": (antes.get("ambient_load") or {}),
                    "after": (despues.get("ambient_load") or {})},
    }
