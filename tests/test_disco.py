"""E/S sin caché y detección de medidas contaminadas.

Sin esto, el test de disco medía la RAM: el fichero recién escrito seguía en la
caché de páginas. El componente de disco pesa un 34% de la nota global y se
pegaba al techo en cualquier equipo con memoria de sobra.
"""

import inspect
import io
import itertools
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from quilate import benchmark
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


class DivisionesPorElCronometro(unittest.TestCase):
    """Ninguna cifra se divide por un tiempo sin comprobar que no es cero.

    `speedup` estaba protegido y `tps`, la línea de al lado, no. Haría falta que
    `perf_counter` devolviera dos veces el mismo valor —prácticamente
    imposible—, pero la asimetría entre dos líneas contiguas es un olvido, no una
    decisión, y montar un `Pool` de verdad para probarlo costaría más de lo que
    vale. Se comprueba sobre el código, que es donde está el criterio.
    """

    def test_ninguna_division_por_wall_va_desnuda(self):
        fuente = inspect.getsource(benchmark.Benchmark.run_cpu_multi)
        desnudas = [linea.strip() for linea in fuente.splitlines()
                    if "/ wall" in linea and "if wall" not in linea]
        self.assertEqual(desnudas, [], "una división por el cronómetro sin guarda")


class ElFicheroDePruebaNoSeQueda(unittest.TestCase):
    """Un fallo a mitad de la prueba no puede dejar 512 MB tirados en %TEMP%.

    En Windows un fichero con un handle abierto no se puede borrar, así que si
    `WriteFile`/`ReadFile` fallaba —disco lleno, una unidad USB desconectada, un
    `--disk-path` en un recurso de red que se cae— el `unlink` moría con
    PermissionError, se lo tragaba el `except OSError`, y quedaba un
    `.quilate_<pid>.tmp` huérfano hasta el final del proceso. Es exactamente la
    basura que la propia herramienta denuncia en «Archivos grandes».
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _correr_fallando(self, tras: int) -> list:
        """Ejecuta la prueba de disco haciendo que la E/S falle a mitad.

        Devuelve los `DiskIO` que se llegaron a crear, para poder comprobar
        después que ninguno se quedó abierto.
        """
        creados = []
        escrituras = itertools.count()

        class DiskIOQueFalla(DiskIO):
            def write(self, n):
                if next(escrituras) >= tras:
                    raise OSError(28, "No queda espacio en el dispositivo")
                return super().write(n)

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                creados.append(self)

        original = benchmark.DiskIO
        benchmark.DiskIO = DiskIOQueFalla
        try:
            b = benchmark.Benchmark(disk_size_mb=8, target_dir=self.dir.name)
            with redirect_stdout(io.StringIO()):
                b.run_disk()
        finally:
            benchmark.DiskIO = original
        return creados

    def test_no_queda_ningun_handle_abierto(self):
        creados = self._correr_fallando(tras=2)
        self.assertTrue(creados, "no se ha llegado a abrir nada: el test no prueba nada")
        for i, disco in enumerate(creados):
            with self.subTest(handle=i):
                self.assertIsNone(disco._handle, "handle de Windows sin cerrar")
                self.assertIsNone(disco._fd, "descriptor POSIX sin cerrar")

    def test_el_temporal_se_borra_igual(self):
        # La comprobación que ve el usuario: en Windows esto es imposible si
        # queda un handle abierto, así que vale por las dos cosas.
        self._correr_fallando(tras=2)
        self.assertEqual(list(Path(self.dir.name).glob(".quilate_*.tmp")), [],
                         "ha quedado el fichero de prueba sin borrar")

    def test_sin_fallos_tampoco_queda_nada(self):
        b = benchmark.Benchmark(disk_size_mb=8, target_dir=self.dir.name)
        with redirect_stdout(io.StringIO()):
            b.run_disk()
        self.assertEqual(list(Path(self.dir.name).glob(".quilate_*.tmp")), [])


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
