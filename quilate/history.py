"""Histórico local de ejecuciones y detección de deriva.

`--compare` contrasta dos ficheros, que es lo que hace falta para responder a
«¿ha servido de algo lo que acabo de aplicar?». Pero la pregunta que un equipo
acaba haciéndose con el tiempo es otra: **¿voy a peor?** El disco que se
degrada, el arranque que crece mes a mes, el portátil que empieza a bajar de
frecuencia en cuanto llega el verano. Eso no se ve en una foto, se ve en la
serie.

La pieza difícil ya estaba hecha: sin margen no se puede distinguir una
tendencia de una nube de puntos, y el margen ya se calcula por prueba. Aquí solo
se guarda una línea por ejecución y se compara el bloque más antiguo con el más
reciente usando ese mismo criterio.

Todo se queda en el equipo, en un fichero de texto que el usuario puede leer,
copiar o borrar. No se envía nada a ninguna parte.
"""

from __future__ import annotations

import json
import os
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from .const import APP_VERSION, IS_WINDOWS
from .platform_utils import is_admin

# Ejecuciones mínimas para que hablar de tendencia signifique algo. Con dos
# puntos siempre se puede trazar una recta, y no dice nada. Son seis y no cuatro
# porque los bloques que se comparan necesitan al menos tres medidas cada uno:
# con dos, una sola ejecución rara es la mitad del bloque y arrastra la mediana.
MINIMO_PARA_TENDENCIA = 6
BLOQUE_MINIMO = 3

# Cuántas ejecuciones se conservan. Un fichero de texto con esto ocupa unos
# pocos kilobytes y cubre años de uso normal.
MAX_ENTRADAS = 200

# Claves cuya deriva interesa seguir, con su etiqueta.
SERIES = {
    "overall": "Puntuación global",
    "cpu_single": "CPU monohilo",
    "cpu_multi": "CPU multihilo",
    "memory": "Memoria",
    "disk": "Almacenamiento",
    "gpu": "GPU",
    "boot_seconds": "Duración del arranque",
    "cpu_temp": "Temperatura de CPU",
}

# Donde subir es empeorar.
MENOS_ES_MEJOR = {"boot_seconds", "cpu_temp"}

# Cambio relativo mínimo, en %, para llamarlo deriva. Por debajo de esto son las
# diferencias normales entre ejecuciones de un mismo equipo.
DERIVA_MINIMA_PCT = 8.0


def _base_valida(base: Path) -> bool:
    """Si se puede escribir el histórico colgando de esa carpeta.

    La base sale de una variable de entorno, y el proceso que eleva por UAC
    hereda el entorno del que no estaba elevado. Quien pueda cambiar
    `LOCALAPPDATA` —una tarea programada del propio usuario, un `.bat` previo, un
    `setx`— conseguiría que Quilate creara árboles de directorios y añadiera
    líneas a un fichero **como Administrador** donde le apeteciera. No hay
    traversal clásico, porque al nombre no se le concatena nada del usuario: solo
    se sustituye la base. Pero crear carpetas en zonas protegidas ya es de más.

    Con privilegios se exige además que caiga dentro del perfil: es lo único que
    garantiza que el histórico no acabe en un sitio donde el usuario no podría
    haberlo puesto por sí mismo.
    """
    if not base.is_absolute() or not base.is_dir():
        return False
    if is_admin():
        try:
            base.resolve().relative_to(Path.home().resolve())
        except (ValueError, OSError):
            return False
    return True


def history_path() -> Path:
    """Fichero del histórico, en el sitio que cada sistema reserva para datos
    de aplicación. Si ese sitio no es de fiar, el perfil del usuario."""
    if IS_WINDOWS:
        base, respaldo = Path(os.environ.get("LOCALAPPDATA") or ""), Path.home()
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or "")
        respaldo = Path.home() / ".local" / "share"
    return (base if _base_valida(base) else respaldo) / "Quilate" / "historico.jsonl"


def _resumen(payload: dict) -> dict[str, Any]:
    """Lo mínimo de cada ejecución: solo cifras y ninguna ruta ni nombre.

    El histórico se guarda para siempre y se acumula, así que no se mete nada
    que no sea un número o una fecha. Nada de rutas, listas de procesos ni
    nombres de programa: para eso ya está el JSON completo de cada ejecución.
    """
    marcas = (payload.get("scores") or {}).get("components") or {}
    metricas = payload.get("metrics") or {}
    entrada: dict[str, Any] = {
        "at": (payload.get("meta") or {}).get("generated_at")
              or datetime.now().isoformat(timespec="seconds"),
        "version": (payload.get("meta") or {}).get("version") or APP_VERSION,
        "overall": (payload.get("scores") or {}).get("overall"),
        "findings": len(payload.get("findings") or []),
        "quick": bool((payload.get("meta") or {}).get("quick")),
    }
    for clave in ("cpu_single", "cpu_multi", "memory", "disk", "gpu"):
        if marcas.get(clave) is not None:
            entrada[clave] = round(float(marcas[clave]), 1)
    arranque = (payload.get("boot") or {}).get("seconds")
    if arranque:
        entrada["boot_seconds"] = round(float(arranque), 1)
    temperatura = (metricas.get("cpu_temp_load") or {}).get("value")
    if temperatura:
        entrada["cpu_temp"] = float(temperatura)
    # El margen máximo de la sesión: una entrada medida con el equipo ocupado no
    # puede pesar lo mismo que una limpia al buscar tendencias.
    dispersion = payload.get("dispersion") or {}
    if dispersion:
        entrada["max_spread_pct"] = round(
            max(float(d.get("spread_pct") or 0) for d in dispersion.values()), 1)
    ambiente = payload.get("ambient_load") or {}
    if ambiente:
        entrada["busy_pct"] = round(
            max(float(v.get("cpu_pct") or 0) for v in ambiente.values()), 1)
    return entrada


def append(payload: dict, path: Path | None = None) -> Path | None:
    """Añade una ejecución al histórico. Devuelve el fichero, o None si falló.

    Que no se pueda escribir el histórico no puede tumbar un análisis que ya
    está hecho, así que los errores se tragan y se informa devolviendo None.
    """
    destino = path or history_path()
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_resumen(payload), ensure_ascii=False) + "\n")
        _recortar(destino)
        return destino
    except (OSError, ValueError):
        # ValueError además de OSError: una ruta con un carácter inválido no
        # lanza OSError sino ValueError, y aquí el análisis ya está hecho y
        # mostrado. Perderlo por no poder anotar una línea sería absurdo.
        return None


def _recortar(destino: Path) -> None:
    try:
        lineas = destino.read_text(encoding="utf-8").splitlines()
        if len(lineas) > MAX_ENTRADAS:
            destino.write_text("\n".join(lineas[-MAX_ENTRADAS:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def load(path: Path | None = None) -> list[dict]:
    """Entradas del histórico, de la más antigua a la más reciente.

    Una línea corrupta —un corte de luz a media escritura— no puede invalidar
    todo lo anterior: se salta y ya está.
    """
    origen = path or history_path()
    try:
        crudo = origen.read_text(encoding="utf-8")
    except OSError:
        return []
    entradas = []
    for linea in crudo.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            dato = json.loads(linea)
        except json.JSONDecodeError:
            continue
        # El tipo, no solo la presencia: la ordenación de abajo compara los `at`
        # entre sí, y un `at` numérico —de una línea editada a mano, que es un
        # uso previsto de este fichero— dejaba `--history` inservible para
        # siempre con un TypeError, justo lo contrario de lo que promete el
        # docstring de esta función.
        if isinstance(dato, dict) and isinstance(dato.get("at"), str):
            entradas.append(dato)
    entradas.sort(key=lambda e: e["at"])
    return entradas


def serie(entradas: list[dict], clave: str) -> list[tuple[str, float]]:
    return [(e["at"], float(e[clave])) for e in entradas
            if isinstance(e.get(clave), (int, float))]


def deriva(entradas: list[dict], clave: str) -> dict | None:
    """Compara el bloque más antiguo con el más reciente.

    Se usan bloques y no el primero contra el último a propósito: una sola
    ejecución rara —hecha con el equipo ocupado, o justo tras un reinicio— no
    puede decidir por sí sola que el equipo va a peor.
    """
    puntos = serie(entradas, clave)
    if len(puntos) < MINIMO_PARA_TENDENCIA:
        return None
    corte = max(BLOQUE_MINIMO, len(puntos) // 3)
    antiguos = [v for _, v in puntos[:corte]]
    recientes = [v for _, v in puntos[-corte:]]
    base = statistics.median(antiguos)
    ahora = statistics.median(recientes)
    if not base:
        return None
    cambio = (ahora - base) / base * 100
    if clave in MENOS_ES_MEJOR:
        cambio = -cambio
    if abs(cambio) < DERIVA_MINIMA_PCT:
        sentido = "estable"
    else:
        sentido = "mejora" if cambio > 0 else "degradación"
    return {
        "key": clave,
        "label": SERIES.get(clave, clave),
        "samples": len(puntos),
        "first_at": puntos[0][0],
        "last_at": puntos[-1][0],
        "baseline": round(base, 1),
        "current": round(ahora, 1),
        # El signo ya está corregido: positivo es «mejor», sea cual sea la métrica.
        "change_pct": round(cambio, 1),
        "direction": sentido,
        "lower_is_better": clave in MENOS_ES_MEJOR,
    }


def report(entradas: list[dict] | None = None, path: Path | None = None) -> dict:
    """Estado del histórico y qué series se están moviendo."""
    entradas = entradas if entradas is not None else load(path)
    derivas = [d for d in (deriva(entradas, k) for k in SERIES) if d]
    return {
        "path": str(path or history_path()),
        "runs": len(entradas),
        "first_at": entradas[0]["at"] if entradas else None,
        "last_at": entradas[-1]["at"] if entradas else None,
        "enough": len(entradas) >= MINIMO_PARA_TENDENCIA,
        "needed": MINIMO_PARA_TENDENCIA,
        "series": derivas,
        "degrading": [d for d in derivas if d["direction"] == "degradación"],
        "entries": entradas,
    }
