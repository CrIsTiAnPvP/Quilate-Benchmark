"""El andamiaje de los tests, probado como lo que es: código que puede fallar.

`patched` sustituye nombres en el espacio de un módulo, y una función los
resuelve en los globals del fichero donde está definida. Mientras todo lo
auditable vivía en un solo fichero eso no tenía matiz. En cuanto hay paquetes
con el código repartido, parchear solo el `__init__` deja a la mitad de las
comprobaciones sin parchear — y no fallando, que sería lo cómodo, sino hablando
con Windows de verdad en mitad de la suite.

Lo mismo con `inspect.getsource`: sobre un paquete devuelve solo el `__init__`,
así que un test que barre el código declarado pasaría en vacío. Estas dos
garantías se comprueban aquí contra un paquete de verdad, `quilate.export`, para
que no dependan de que alguien se acuerde.
"""

from __future__ import annotations

import inspect
import unittest

from quilate import export, sensors
from quilate.export import html_export
from tests.support import alcanzados, fuente_completa, patched


class ModulosAlcanzados(unittest.TestCase):
    def test_un_modulo_suelto_se_alcanza_a_si_mismo(self):
        self.assertEqual(alcanzados(sensors), [sensors])

    def test_un_paquete_alcanza_a_sus_submodulos(self):
        destinos = alcanzados(export)
        self.assertIn(export, destinos)
        self.assertIn(html_export, destinos)

    def test_el_paquete_va_primero(self):
        # `patched` toca el paquete siempre y los submódulos solo donde el
        # nombre ya exista: el orden deja clara esa asimetría.
        self.assertIs(alcanzados(export)[0], export)


class ParcheoEnUnPaquete(unittest.TestCase):
    def test_el_submodulo_recibe_el_parche(self):
        original = html_export.SEVERITY_ORDER
        with patched(export, SEVERITY_ORDER={"inventado": 0}):
            self.assertEqual(html_export.SEVERITY_ORDER(), {"inventado": 0})
        self.assertIs(html_export.SEVERITY_ORDER, original)

    def test_no_se_le_inventan_nombres_al_submodulo(self):
        # Solo se sustituye lo que el submódulo ya declaraba. Plantarle un
        # nombre nuevo escondería un import que falta.
        with patched(export, un_nombre_que_no_existe=lambda: 1):
            self.assertFalse(hasattr(html_export, "un_nombre_que_no_existe"))

    def test_lo_que_no_existia_se_retira_al_salir(self):
        with patched(export, un_nombre_que_no_existe=lambda: 1):
            self.assertTrue(hasattr(export, "un_nombre_que_no_existe"))
        self.assertFalse(hasattr(export, "un_nombre_que_no_existe"))

    def test_dos_valores_constantes_no_se_pisan(self):
        # Las lambdas de varios overrides compartían la variable del bucle y
        # todas devolvían el último valor.
        with patched(export, uno="primero", dos="segundo"):
            self.assertEqual(export.uno(), "primero")
            self.assertEqual(export.dos(), "segundo")


class FuenteCompleta(unittest.TestCase):
    def test_de_un_modulo_suelto_es_su_fuente(self):
        self.assertEqual(fuente_completa(sensors), inspect.getsource(sensors))

    def test_de_un_paquete_incluye_los_submodulos(self):
        fuente = fuente_completa(export)
        self.assertGreater(len(fuente), len(inspect.getsource(export)))
        self.assertIn("def export_html", fuente)
        self.assertIn("def export_plan", fuente)


if __name__ == "__main__":
    unittest.main()
