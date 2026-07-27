"""Medida de ancho de banda de memoria.

Dos bytearray creados uno detrás de otro quedan separados por el tamaño más los
16 bytes de cabecera del objeto: 16 bytes justos módulo 4096. La caché mapea las
direcciones por sus bits 6-11, así que con esa separación cada línea del destino
desaloja a la del origen y la copia se pasa la vida fallando. Se veía en que un
nivel de caché aparecía más lento que la RAM.

Desplazar el destino una cantidad fija tampoco bastaba: el desfase resultante
depende de dónde el asignador coloque cada reserva, y este mismo test llegó a
medir 32 bytes —media línea de caché— con el desplazamiento puesto. Por eso
ahora se leen las direcciones reales y se busca la media vuelta del periodo.
"""

import ctypes
import unittest

from quilate.workloads import (_DESFASE_OBJETIVO, _PERIODO_CONJUNTO,
                               _unaliased_pair, memcpy_bandwidth)

PERIODO = _PERIODO_CONJUNTO
LINEA_CACHE = 64


def direccion(view: memoryview) -> int:
    return ctypes.addressof(ctypes.c_char.from_buffer(view))


def desfase(src: memoryview, dst: memoryview) -> int:
    """Distancia al solapamiento, en bytes: 0 = mismo conjunto de caché."""
    resto = abs(direccion(dst) - direccion(src)) % PERIODO
    return min(resto, PERIODO - resto)


class Antialiasing(unittest.TestCase):
    def test_el_objetivo_esta_lo_mas_lejos_posible_del_solapamiento(self):
        self.assertEqual(_DESFASE_OBJETIVO, PERIODO // 2)

    def test_origen_y_destino_no_comparten_conjunto(self):
        for size in (16 * 1024, 192 * 1024, 4 * 1024**2):
            with self.subTest(size=size):
                src, dst = _unaliased_pair(size)
                self.assertGreater(desfase(src, dst), LINEA_CACHE,
                                   "origen y destino caen en el mismo conjunto de caché")

    def test_el_desfase_no_depende_de_donde_caiga_la_reserva(self):
        # El fallo anterior era intermitente justo por esto: salía bien casi
        # siempre y de vez en cuando el asignador colocaba los dos bloques
        # prácticamente alineados.
        for _ in range(20):
            src, dst = _unaliased_pair(16 * 1024)
            self.assertEqual(desfase(src, dst), _DESFASE_OBJETIVO)

    def test_el_par_tiene_el_tamano_pedido(self):
        src, dst = _unaliased_pair(1024)
        self.assertEqual(len(src), 1024)
        self.assertEqual(len(dst), 1024)

    def test_se_puede_copiar(self):
        src, dst = _unaliased_pair(4096)
        src[:16] = b"0123456789abcdef"
        dst[:] = src
        self.assertEqual(bytes(dst[:16]), b"0123456789abcdef")


class FormaDeLaJerarquia(unittest.TestCase):
    """No se comprueban cifras absolutas —dependen de la máquina— sino que la
    curva tenga sentido físico."""

    def setUp(self):
        self.niveles = {n: memcpy_bandwidth(s, 0.05) for n, s in
                        (("L1", 16 * 1024), ("L2", 192 * 1024),
                         ("L3", 4 * 1024**2), ("RAM", 64 * 1024**2))}

    def test_ningun_nivel_sale_a_cero(self):
        for nivel, gbs in self.niveles.items():
            with self.subTest(nivel=nivel):
                self.assertGreater(gbs, 0.5, f"{nivel} sin medida creíble")

    def test_la_cache_no_puede_ser_mas_lenta_que_la_ram(self):
        # El síntoma exacto del fallo de aliasing.
        self.assertGreater(self.niveles["L1"], self.niveles["RAM"],
                           f"L1 más lenta que la RAM: {self.niveles}")
        self.assertGreater(self.niveles["L2"], self.niveles["RAM"],
                           f"L2 más lenta que la RAM: {self.niveles}")

    def test_los_niveles_pequenos_van_por_delante_de_los_grandes(self):
        self.assertGreaterEqual(self.niveles["L2"], self.niveles["L3"] * 0.9)


if __name__ == "__main__":
    unittest.main()
