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
    datos["benchmark"] = _pruebas_utilizables(datos.get("benchmark"))
    return datos


def _pruebas_utilizables(benchmark: Any) -> dict:
    """Solo las pruebas que traen una cifra con la que se pueda operar.

    A partir de aquí el módulo indexa a pelo, y el fichero viene de fuera: se
    trunca por un corte de luz a media escritura, se edita a mano, o lo genera
    una versión con otro esquema. Cualquiera de las tres cosas convertía la
    comparación entera en una traza de Python. Es el mismo criterio que
    `history.load()` aplica a las líneas corruptas: se descarta lo que no sirve
    y lo demás se compara igual.
    """
    if not isinstance(benchmark, dict):
        return {}
    utilizables = {}
    for clave, prueba in benchmark.items():
        if not isinstance(prueba, dict):
            continue
        raw = prueba.get("raw")
        # `bool` es subclase de `int` y no es la medida de nada.
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        utilizables[clave] = prueba
    return utilizables


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


def _por_id(run: dict) -> dict[str, dict]:
    """Hallazgos indexados por identificador, saltándose los que no lo traen.

    Sin `id` no hay forma de casar un hallazgo con el de la otra ejecución, así
    que descartarlo es lo único que se puede hacer con él —y desde luego mejor
    que dejar la comparación entera sin hacer.
    """
    return {f["id"]: f for f in run.get("findings") or []
            if isinstance(f, dict) and f.get("id")}


def comparar_hallazgos(antes: dict, despues: dict) -> dict[str, list[dict]]:
    """Qué se arregló, qué sigue y qué ha aparecido nuevo."""
    fa, fd = _por_id(antes), _por_id(despues)
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


# Peso de cada discrepancia. «alta» significa que las dos cifras salen de reglas
# distintas y restarlas no dice nada; «media», que la resta se sostiene pero hay
# que leerla sabiendo qué más ha cambiado.
GRAVE, LEVE = "alta", "media"


def _ajuste_python(version: Any) -> float | None:
    """El factor con el que se corrigió el intérprete en esa ejecución.

    `PY_ADJUST` vale 1.0 en 3.11 o superior y 1.35 por debajo, porque 3.11 es un
    25-40% más rápido en código puro. Comparar una ejecución desde el `.exe`
    —empaquetado con un intérprete— contra otra desde el código fuente con otro
    mete ese 35% en las tres pruebas de CPU monohilo, y no lo pone el hardware.
    """
    if not isinstance(version, str):
        return None
    partes = version.split(".")
    if len(partes) < 2:
        return None
    try:
        numero = (int(partes[0]), int(partes[1]))
    except ValueError:
        return None
    return 1.0 if numero >= (3, 11) else 1.35


def _motivo(clave: str, gravedad: str, texto: str) -> dict:
    return {"key": clave, "severity": gravedad, "text": texto}


def comparabilidad(antes: dict, despues: dict) -> list[dict]:
    """Todo lo que impide leer la resta de estas dos ejecuciones como un cambio.

    `mismo_equipo` cubría exactamente una de las formas de no ser comparables, y
    no la más frecuente: lo normal es comparar un equipo consigo mismo después
    de haber tocado algo. Lo que falsea la resta de verdad es medir dos veces con
    reglas distintas —el modo rápido usa 192 MB de disco en vez de 512, 4000 IOPS
    en vez de 12000 y una sola repetición por prueba—, puntuar contra dos escalas
    de referencia distintas, o corregir el intérprete con dos factores distintos.
    Nada de eso se veía, y todo estaba ya guardado en el JSON.
    """
    motivos: list[dict] = []
    sa, sd = antes.get("system") or {}, despues.get("system") or {}
    ma, md = antes.get("meta") or {}, despues.get("meta") or {}

    # --- El hardware: comparar dos máquinas es legítimo, pero no es un «antes y
    # después». Esto es lo único que se avisaba hasta ahora.
    for campo, etiqueta in (("hostname", "equipo"), ("cpu_name", "CPU"),
                            ("ram_total", "RAM total")):
        a, d = sa.get(campo), sd.get(campo)
        if a and d and a != d:
            motivos.append(_motivo("hardware", GRAVE, f"{etiqueta}: «{a}» → «{d}»"))

    # --- El modo: dos escalas, no dos medidas.
    if bool(ma.get("quick")) != bool(md.get("quick")):
        rapida = "la primera" if ma.get("quick") else "la segunda"
        motivos.append(_motivo(
            "quick", GRAVE,
            f"{rapida} se midió con --quick: menos datos, menos repeticiones y "
            f"otras escalas, así que la diferencia no es del equipo"))

    # --- La escala de referencia con la que se dieron las notas.
    ra = (antes.get("reference_meta") or {}).get("date")
    rd = (despues.get("reference_meta") or {}).get("date")
    if ra and rd:
        if ra != rd:
            motivos.append(_motivo(
                "reference", GRAVE,
                f"la escala de referencia cambió entre las dos ({ra} → {rd}): "
                f"las notas no son conmensurables, aunque las cifras crudas sí"))
    elif (ma.get("version") or "?") != (md.get("version") or "?"):
        # Las ejecuciones anteriores a la v2.2 no guardaban `reference_meta`, así
        # que con una de ellas delante no se puede saber si la escala cambió. La
        # versión es lo único que queda para decirlo, y decirlo es mejor que
        # afirmar que son comparables sin haberlo mirado.
        motivos.append(_motivo(
            "version", LEVE,
            f"versiones distintas (v{ma.get('version') or '?'} → "
            f"v{md.get('version') or '?'}) y una de las dos no anota con qué "
            f"escala puntuó"))

    # --- El intérprete: 35% de sesgo en las pruebas de CPU monohilo.
    pa, pd = _ajuste_python(sa.get("python_version")), _ajuste_python(sd.get("python_version"))
    if pa is not None and pd is not None and pa != pd:
        motivos.append(_motivo(
            "python", GRAVE,
            f"Python {sa.get('python_version')} → {sd.get('python_version')}: las "
            f"pruebas de CPU monohilo se corrigen con factores distintos "
            f"({pa} y {pd}), y esa diferencia no la pone el equipo"))

    # --- Cobertura: un --no-gpu contra una ejecución completa.
    ca = set((antes.get("scores") or {}).get("components") or {})
    cd = set((despues.get("scores") or {}).get("components") or {})
    if ca != cd:
        faltan = sorted(ca - cd) + sorted(cd - ca)
        motivos.append(_motivo(
            "coverage", LEVE,
            f"no se midió lo mismo en las dos: {', '.join(faltan)} solo está en "
            f"una. La nota global sale de conjuntos distintos"))
    return motivos


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
    motivos = comparabilidad(antes, despues)
    return {
        "meta": {
            "before": {"generated_at": (antes.get("meta") or {}).get("generated_at"),
                       "version": (antes.get("meta") or {}).get("version")},
            "after": {"generated_at": (despues.get("meta") or {}).get("generated_at"),
                      "version": (despues.get("meta") or {}).get("version")},
            # No basta con restar: hay que decidir si la resta significa algo. Es
            # el mismo criterio que el módulo ya aplica al margen de cada prueba.
            "comparable": not any(m["severity"] == GRAVE for m in motivos),
            "comparability": motivos,
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
