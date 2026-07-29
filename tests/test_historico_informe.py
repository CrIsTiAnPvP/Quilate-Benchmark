"""Lo que `--history` enseña por pantalla, que tampoco miraba nadie.

`history.py` estaba probado: decide cuándo una serie de medidas es una
tendencia y cuándo es una nube de puntos. Pero el módulo que convierte eso en
lo que lee el usuario no tenía una sola prueba, y es el tercero de los tres
modos de la herramienta.

Lo que se fija aquí es lo que no puede fallar aunque cambie la maquetación: que
con pocas ejecuciones se diga que aún no hay tendencia en vez de dibujar una,
que una degradación se nombre y se explique, y que el aviso sobre el signo esté
—porque en arranque y temperatura el número baja cuando la cosa mejora, y sin
esa frase la tabla se lee justo al revés.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from quilate.console import C
from quilate.history import MINIMO_PARA_TENDENCIA, report
from quilate.history_report import _chispograma, print_history


def entrada(n: int, **campos) -> dict:
    """Una línea del histórico, con fecha que crece de verdad."""
    base = {"at": f"2026-01-{1 + n // 24:02d}T{n % 24:02d}:00:00",
            "version": "2.6.0", "overall": 100.0, "findings": 0, "quick": False}
    base.update(campos)
    return base


def salida(entradas: list[dict]) -> str:
    C.disable()
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        print_history(report(entradas, path=None))
    return buffer.getvalue()


class ElHistoricoVacio(unittest.TestCase):
    def test_se_dice_que_no_hay_nada_y_como_llenarlo(self):
        # Sin esto, un histórico vacío sale como una tabla sin filas y parece
        # que la herramienta no ha funcionado.
        texto = salida([])
        self.assertIn("Todavía no hay ninguna ejecución guardada", texto)
        self.assertIn(str(MINIMO_PARA_TENDENCIA), texto)

    def test_no_se_dibuja_ninguna_serie(self):
        self.assertNotIn("FORMA", salida([]))


class ConPocasEjecuciones(unittest.TestCase):
    """Con dos puntos siempre se puede trazar una recta, y no significa nada."""

    def test_se_dice_cuantas_faltan(self):
        texto = salida([entrada(n) for n in range(MINIMO_PARA_TENDENCIA - 2)])
        self.assertIn("Aún no hay suficientes", texto)
        self.assertIn("faltan 2", texto)

    def test_se_explica_por_que(self):
        texto = salida([entrada(n) for n in range(2)])
        self.assertIn("Con dos puntos siempre se puede trazar una recta", texto)

    def test_se_cuentan_las_ejecuciones_y_las_fechas(self):
        texto = salida([entrada(n) for n in range(3)])
        self.assertIn("3 ejecuciones", texto)
        self.assertIn("2026-01-01", texto)


class CuandoLaCosaVaAPeor(unittest.TestCase):
    def _degradando(self) -> list[dict]:
        # Bloque antiguo sano y bloque reciente claramente peor: el criterio de
        # `deriva` compara el principio con el final, no puntos sueltos.
        return ([entrada(n, overall=100.0) for n in range(6)]
                + [entrada(6 + n, overall=70.0) for n in range(6)])

    def test_se_nombra_la_serie_que_se_degrada(self):
        texto = salida(self._degradando())
        self.assertIn("Va a peor", texto)
        self.assertIn("Puntuación global", texto)

    def test_se_dice_que_no_se_arregla_con_ajustes(self):
        # Es el mensaje que evita que alguien busque un ajuste mágico para algo
        # que suele ser el disco gastándose o la pasta térmica.
        texto = salida(self._degradando())
        self.assertIn("no se arregla con ajustes", texto)
        self.assertIn("--compare", texto)

    def test_sin_degradacion_se_dice_que_no_la_hay(self):
        texto = salida([entrada(n) for n in range(MINIMO_PARA_TENDENCIA + 2)])
        self.assertIn("Ninguna serie se está degradando", texto)
        self.assertNotIn("Va a peor", texto)

    def test_el_arranque_lleva_su_unidad(self):
        entradas = ([entrada(n, boot_seconds=20.0) for n in range(6)]
                    + [entrada(6 + n, boot_seconds=45.0) for n in range(6)])
        texto = salida(entradas)
        self.assertIn("Va a peor", texto)
        self.assertIn(" s (", texto)


class ElSignoNoSeLeeAlReves(unittest.TestCase):
    """En arranque y temperatura el número baja cuando la cosa mejora."""

    def test_se_avisa_de_que_el_signo_ya_esta_corregido(self):
        entradas = ([entrada(n, boot_seconds=45.0) for n in range(6)]
                    + [entrada(6 + n, boot_seconds=20.0) for n in range(6)])
        texto = salida(entradas)
        self.assertIn("«+» siempre es mejor", texto)

    def test_arrancar_mas_rapido_sale_como_mejora(self):
        entradas = ([entrada(n, boot_seconds=45.0) for n in range(6)]
                    + [entrada(6 + n, boot_seconds=20.0) for n in range(6)])
        texto = salida(entradas)
        self.assertNotIn("Va a peor", texto)
        self.assertIn("+", texto)


class ElChispograma(unittest.TestCase):
    """La forma de la serie en una línea: si sube, si baja o si no va a ningún
    sitio. No es decoración: es lo único que distingue una tendencia de un
    diente de sierra con los mismos extremos."""

    def test_con_menos_de_dos_valores_no_se_dibuja_nada(self):
        self.assertEqual(_chispograma([]), "")
        self.assertEqual(_chispograma([100.0]), "")

    def test_una_serie_plana_sale_plana(self):
        self.assertEqual(_chispograma([50.0] * 4), "▄▄▄▄")

    def test_el_minimo_y_el_maximo_son_los_extremos(self):
        dibujo = _chispograma([0.0, 50.0, 100.0])
        self.assertEqual(dibujo[0], "▁")
        self.assertEqual(dibujo[-1], "█")

    def test_no_se_pasa_del_ancho_pedido(self):
        self.assertEqual(len(_chispograma([float(n) for n in range(100)], ancho=28)), 28)

    def test_se_queda_con_lo_mas_reciente(self):
        # Recortar por el principio y no por el final: lo que interesa de una
        # serie larga es cómo acaba, no cómo empezó.
        dibujo = _chispograma([0.0] * 30 + [1.0, 2.0], ancho=3)
        self.assertEqual(dibujo, "▁▄█")


if __name__ == "__main__":
    unittest.main()
