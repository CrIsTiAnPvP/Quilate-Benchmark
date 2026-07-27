"""Medida de ancho de banda de memoria.

Dos bytearray creados uno detrás de otro quedan separados por el tamaño más los
16 bytes de cabecera del objeto: 16 bytes justos módulo 4096. La caché mapea las
direcciones por sus bits 6-11, así que con esa separación cada línea del destino
desaloja a la del origen y la copia se pasa la vida fallando. Se veía en que un
nivel de caché aparecía más lento que la RAM.
"""

import ctypes
import unittest

from quilate.workloads import _ANTIALIAS, _unaliased_pair, memcpy_bandwidth

# Periodo con el que dos direcciones caen en el mismo conjunto de caché.
PERIODO = 4096


def direccion(view: memoryview) -> int:
    return ctypes.addressof(ctypes.c_char.from_buffer(view))


class Antialiasing(unittest.TestCase):
    def test_el_desplazamiento_no_es_multiplo_del_periodo(self):
        # Si lo fuera no serviría de nada: volveríamos al mismo conjunto.
        self.assertNotEqual(_ANTIALIAS % PERIODO, 0)

    def test_origen_y_destino_no_comparten_conjunto(self):
        for size in (16 * 1024, 192 * 1024, 4 * 1024**2):
            with self.subTest(size=size):
                src, dst = _unaliased_pair(size)
                separacion = abs(direccion(dst) - direccion(src))
                # Lo que hacía daño era una separación de casi cero módulo 4096.
                resto = separacion % PERIODO
                self.assertGreater(min(resto, PERIODO - resto), 32,
                                   "origen y destino caen en el mismo conjunto de caché")

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
