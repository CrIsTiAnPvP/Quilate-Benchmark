"""Composición de mejoras y orden de prioridad."""

import pytest

from quilate.audit import Finding
from quilate.projection import combine_gains, priority_rank, project_improvement


def hallazgo(**kw):
    base = dict(id="x", title="t", severity="medium", category="fluidez",
                component="system", detail="d", gain=0.1, gain_note="n",
                effort="bajo", risk="nulo", steps=[])
    base.update(kw)
    return Finding(**base)


# --- Composición de ganancias ------------------------------------------------

def test_sin_mejoras():
    assert combine_gains([]) == 0.0


def test_una_sola():
    assert combine_gains([0.2]) == pytest.approx(0.2)


def test_rendimientos_decrecientes():
    # Dos mejoras del 50% no dan un 100%: 1 - 0,5·0,5 = 0,75.
    assert combine_gains([0.5, 0.5]) == pytest.approx(0.75)


def test_nunca_pasa_del_100():
    # Con 20 mejoras al tope, 1 - 0,1^20 se redondea a 1,0 exacto en coma
    # flotante. No es un caso real —la mayor ganancia del catálogo es 0,45—
    # pero conviene fijar que el resultado no se desborda.
    assert combine_gains([0.9] * 20) <= 1.0


def test_con_ganancias_realistas_queda_lejos_del_100():
    # Las del catálogo: RAM insuficiente, HDD de sistema, arranque cargado.
    assert combine_gains([0.45, 0.35, 0.30, 0.22]) < 0.85


@pytest.mark.parametrize("entrada, recortado", [
    (-5.0, 0.0),    # por debajo del suelo
    (50.0, 0.9),    # por encima del techo
])
def test_ignora_valores_absurdos(entrada, recortado):
    # Se recortan a [0, 0.9] para que un `gain` mal puesto no dispare todo.
    assert combine_gains([entrada]) == pytest.approx(recortado)


# --- Prioridad ---------------------------------------------------------------

def test_a_igual_esfuerzo_manda_la_ganancia():
    alto = hallazgo(gain=0.3, effort="bajo")
    bajo = hallazgo(gain=0.1, effort="bajo")
    assert priority_rank(alto) < priority_rank(bajo)


def test_a_igual_ganancia_manda_el_esfuerzo():
    facil = hallazgo(gain=0.2, effort="bajo")
    dificil = hallazgo(gain=0.2, effort="alto")
    assert priority_rank(facil) < priority_rank(dificil)


def test_una_mejora_grande_y_costosa_puede_ir_antes():
    grande = hallazgo(gain=0.45, effort="alto")
    pequena = hallazgo(gain=0.05, effort="bajo")
    assert priority_rank(grande) < priority_rank(pequena)


# --- Proyección --------------------------------------------------------------

def test_agrupa_por_componente_y_categoria():
    r = project_improvement(None, [
        hallazgo(component="disk", category="almacenamiento", gain=0.2),
        hallazgo(component="disk", category="almacenamiento", gain=0.1),
        hallazgo(component="system", category="arranque", gain=0.3),
    ])
    assert r["component_gain"]["disk"] == pytest.approx(1 - 0.8 * 0.9)
    assert r["system_gain"] == pytest.approx(0.3)


def test_los_hallazgos_sin_ganancia_no_cuentan():
    r = project_improvement(None, [hallazgo(component="disk", gain=0.0)])
    assert r["component_gain"] == {}


def test_sin_benchmark_no_proyecta_puntuacion():
    r = project_improvement(None, [hallazgo(gain=0.2)])
    assert "projected_overall" not in r


# --- La tabla de la proyección solo enseña lo que cambia ----------------------

def _tabla(actuales, ganancias):
    from quilate.export.html_export.bloques import _projection_tables
    return _projection_tables({
        "current_components": actuales,
        "component_gain": ganancias,
        "projected_components": {k: v * (1 + ganancias.get(k, 0.0))
                                 for k, v in actuales.items()},
    })


def test_la_proyeccion_no_repite_los_componentes_que_no_mejoran():
    # Listarlos todos obligaba a leer cinco filas de antes y después para
    # descubrir que cuatro eran idénticas a los dos lados. Las cifras de «ahora»
    # ya están en Benchmark y en la tira del resumen.
    html = _tabla({"memory": 115.0, "gpu": 109.0, "cpu_single": 118.0},
                  {"memory": 0.12})
    assert html.count("<tr>") == 2          # encabezado + la única que mejora
    assert "Memoria" in html
    assert "no tienen mejoras pendientes" in html
    assert "GPU" in html                    # nombrados en la nota, no perdidos
    assert "CPU monohilo" in html


def test_si_nada_mejora_no_se_pinta_la_tabla():
    html = _tabla({"memory": 115.0, "gpu": 109.0}, {})
    assert "<table>" not in html
    assert "Ningún componente tiene mejoras" in html
    assert "Memoria" in html and "GPU" in html
