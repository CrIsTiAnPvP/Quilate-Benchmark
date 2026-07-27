"""Composición de mejoras y orden de prioridad."""

import unittest

from quilate.audit import Finding
from quilate.projection import combine_gains, priority_rank, project_improvement


def hallazgo(**kw):
    base = dict(id="x", title="t", severity="medium", category="fluidez",
                component="system", detail="d", gain=0.1, gain_note="n",
                effort="bajo", risk="nulo", steps=[])
    base.update(kw)
    return Finding(**base)


class Composicion(unittest.TestCase):
    def test_sin_mejoras(self):
        self.assertEqual(combine_gains([]), 0.0)

    def test_una_sola(self):
        self.assertAlmostEqual(combine_gains([0.2]), 0.2)

    def test_rendimientos_decrecientes(self):
        # Dos mejoras del 50% no dan un 100%: 1 - 0,5·0,5 = 0,75.
        self.assertAlmostEqual(combine_gains([0.5, 0.5]), 0.75)

    def test_nunca_pasa_del_100(self):
        # Con 20 mejoras al tope, 1 - 0,1^20 se redondea a 1,0 exacto en coma
        # flotante. No es un caso real —la mayor ganancia del catálogo es 0,45—
        # pero conviene fijar que el resultado no se desborda.
        self.assertLessEqual(combine_gains([0.9] * 20), 1.0)

    def test_con_ganancias_realistas_queda_lejos_del_100(self):
        # Las del catálogo: RAM insuficiente, HDD de sistema, arranque cargado.
        self.assertLess(combine_gains([0.45, 0.35, 0.30, 0.22]), 0.85)

    def test_ignora_valores_absurdos(self):
        # Se recortan a [0, 0.9] para que un `gain` mal puesto no dispare todo.
        self.assertAlmostEqual(combine_gains([-5.0]), 0.0)
        self.assertAlmostEqual(combine_gains([50.0]), 0.9)


class Prioridad(unittest.TestCase):
    def test_a_igual_esfuerzo_manda_la_ganancia(self):
        alto = hallazgo(gain=0.3, effort="bajo")
        bajo = hallazgo(gain=0.1, effort="bajo")
        self.assertLess(priority_rank(alto), priority_rank(bajo))

    def test_a_igual_ganancia_manda_el_esfuerzo(self):
        facil = hallazgo(gain=0.2, effort="bajo")
        dificil = hallazgo(gain=0.2, effort="alto")
        self.assertLess(priority_rank(facil), priority_rank(dificil))

    def test_una_mejora_grande_y_costosa_puede_ir_antes(self):
        grande = hallazgo(gain=0.45, effort="alto")
        pequena = hallazgo(gain=0.05, effort="bajo")
        self.assertLess(priority_rank(grande), priority_rank(pequena))


class Proyeccion(unittest.TestCase):
    def test_agrupa_por_componente_y_categoria(self):
        r = project_improvement(None, [
            hallazgo(component="disk", category="almacenamiento", gain=0.2),
            hallazgo(component="disk", category="almacenamiento", gain=0.1),
            hallazgo(component="system", category="arranque", gain=0.3),
        ])
        self.assertAlmostEqual(r["component_gain"]["disk"], 1 - 0.8 * 0.9)
        self.assertAlmostEqual(r["system_gain"], 0.3)

    def test_los_hallazgos_sin_ganancia_no_cuentan(self):
        r = project_improvement(None, [hallazgo(component="disk", gain=0.0)])
        self.assertEqual(r["component_gain"], {})

    def test_sin_benchmark_no_proyecta_puntuacion(self):
        r = project_improvement(None, [hallazgo(gain=0.2)])
        self.assertNotIn("projected_overall", r)


if __name__ == "__main__":
    unittest.main()
