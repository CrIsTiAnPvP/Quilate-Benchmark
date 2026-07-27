"""E/S sin caché y detección de medidas contaminadas.

Sin esto, el test de disco medía la RAM: el fichero recién escrito seguía en la
caché de páginas. El componente de disco pesa un 34% de la nota global y se
pegaba al techo en cualquier equipo con memoria de sobra.
"""

import os
import tempfile
import unittest

from quilate.benchmark import CACHE_LATENCY_US, SCORE_CAP, WEIGHTS, cache_served
from quilate.rawio import ALIGN, DiskIO


class DecisionDeCache(unittest.TestCase):
    """Cifras reales medidas sobre el mismo Kingston SNV2S1000G."""

    def test_latencias_de_caches_se_descartan(self):
        # Medido con E/S normal: 4,9 y 5,0 µs en dos ejecuciones distintas.
        for latencia in (4.9, 5.0, 0.8, 19.9):
            with self.subTest(us=latencia):
                self.assertTrue(cache_served(latencia, direct=False))

    def test_latencias_de_disco_se_aceptan(self):
        # Medido con E/S sin buffer sobre el mismo disco: 81,6 µs.
        for latencia in (81.6, 20.0, 200.0, 5000.0):
            with self.subTest(us=latencia):
                self.assertFalse(cache_served(latencia, direct=False))

    def test_con_io_directa_no_se_sospecha(self):
        # Un NVMe rapidísimo puede bajar de 20 µs legítimamente; si la lectura ya
        # esquiva la caché, no hay nada que descartar.
        self.assertFalse(cache_served(4.9, direct=True))

    def test_umbral_documentado(self):
        self.assertEqual(CACHE_LATENCY_US, 20.0)


class PesoDelDisco(unittest.TestCase):
    def test_el_disco_es_el_componente_de_mas_peso(self):
        # Por eso contaminarlo estropeaba la nota global más que ningún otro.
        self.assertEqual(max(WEIGHTS, key=WEIGHTS.get), "disk")

    def test_la_medida_contaminada_llegaba_al_techo(self):
        # Cifras reales de antes del arreglo: 43 pts de escritura, 187 de lectura
        # y 934 de IOPS con los pesos internos del componente.
        contaminado = 43 * 0.25 + 187 * 0.3 + 934 * 0.45
        self.assertGreater(contaminado, SCORE_CAP)
        # Y con la medida real del mismo disco, ya no.
        real = 73 * 0.25 + 151 * 0.3 + 56 * 0.45
        self.assertLess(real, SCORE_CAP)


class EntradaSalidaReal(unittest.TestCase):
    """DiskIO contra un fichero de verdad: alineación, escritura y lectura."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".quilate-test")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(self.path) and os.unlink(self.path))

    def test_escribe_y_lee_el_mismo_contenido(self):
        bloque = ALIGN * 16
        escritor = DiskIO(self.path, write=True, block=bloque)
        escritor.fill_random()
        escrito = escritor.write(bloque)
        escritor.sync()
        escritor.close()
        self.assertEqual(escrito, bloque)
        self.assertEqual(os.path.getsize(self.path), bloque)

        lector = DiskIO(self.path, write=False, block=bloque)
        try:
            self.assertEqual(lector.read(bloque), bloque)
        finally:
            lector.close()

    def test_lectura_posicionada(self):
        bloque = ALIGN * 4
        escritor = DiskIO(self.path, write=True, block=bloque)
        escritor.fill_random()
        for _ in range(4):
            escritor.write(bloque)
        escritor.sync()
        escritor.close()

        lector = DiskIO(self.path, write=False, block=ALIGN)
        try:
            for offset in (0, ALIGN, ALIGN * 8, bloque * 3):
                with self.subTest(offset=offset):
                    lector.seek(offset)
                    self.assertEqual(lector.read(ALIGN), ALIGN)
        finally:
            lector.close()

    def test_el_buffer_queda_alineado(self):
        # Requisito de FILE_FLAG_NO_BUFFERING: sin esto, WriteFile falla.
        import ctypes
        io = DiskIO(self.path, write=True, block=ALIGN)
        try:
            direccion = ctypes.cast(io._ptr, ctypes.c_void_p).value
            self.assertEqual(direccion % ALIGN, 0)
        finally:
            io.close()

    def test_datos_incompresibles(self):
        # Los SSD con compresión transparente inflan la escritura si se les
        # mandan ceros.
        io = DiskIO(self.path, write=True, block=ALIGN)
        try:
            io.fill_random()
            self.assertNotEqual(io._data, b"\0" * ALIGN)
            self.assertGreater(len(set(io._data)), 200)
        finally:
            io.close()


if __name__ == "__main__":
    unittest.main()
