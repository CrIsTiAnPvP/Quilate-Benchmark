"""Los binarios del sistema se piden por ruta absoluta.

Windows resuelve un nombre de ejecutable sin ruta mirando el directorio actual
antes que System32, y este programa se ofrece a elevarse por UAC: basta con
dejar un `powercfg.exe` en la carpeta desde la que se hace doble clic para que
se ejecute como Administrador. Aquí se comprueba lo único que cierra ese hueco:
que la ruta que se le pasa a `subprocess` no la decida ni el `PATH` ni el
directorio de trabajo.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from quilate.const import IS_WINDOWS
from quilate.platform_utils import (_SYSTEM32, _sys_exe, _system32, run_cmd,
                                    run_cmd_bytes)
from quilate import sensors


class _EnDirectorioTrampa(unittest.TestCase):
    """Base: un directorio de trabajo con un ejecutable plantado dentro."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.trampa = Path(self.dir.name)
        self.cwd_original = os.getcwd()
        self.entorno_original = dict(os.environ)
        os.chdir(self.trampa)

    def tearDown(self):
        os.chdir(self.cwd_original)
        os.environ.clear()
        os.environ.update(self.entorno_original)
        self.dir.cleanup()

    def plantar(self, nombre: str) -> Path:
        """Deja en el directorio actual un ejecutable real con ese nombre.

        Se copia `cmd.exe` porque es autocontenido —solo depende de DLL de
        System32— y porque acepta un argumento que deja rastro en disco, así que
        se puede distinguir «se ha ejecutado» de «no se ha ejecutado» sin
        depender de lo que devuelva el binario legítimo.
        """
        destino = self.trampa / nombre
        shutil.copy2(os.path.join(_SYSTEM32, "cmd.exe"), destino)
        return destino


@unittest.skipUnless(IS_WINDOWS, "el orden de búsqueda de CreateProcess es de Windows")
class RutaAbsoluta(_EnDirectorioTrampa):
    def test_powershell_no_depende_del_path_ni_del_cwd(self):
        ruta = _sys_exe("powershell.exe")
        self.assertTrue(os.path.isabs(ruta), "una ruta relativa la resuelve el CWD")
        # PowerShell 5.1 no cuelga directamente de System32.
        self.assertEqual(
            ruta.lower(),
            os.path.join(_SYSTEM32, "WindowsPowerShell", "v1.0", "powershell.exe").lower())

        # Ni un PATH hostil ni un directorio actual con una trampa dentro
        # cambian la respuesta: no se consulta ninguno de los dos.
        self.plantar("powershell.exe")
        os.environ["PATH"] = str(self.trampa)
        self.assertEqual(_sys_exe("powershell.exe"), ruta)

    def test_system32_no_sale_del_entorno(self):
        """`%SystemRoot%` lo puede cambiar cualquiera que arranque el proceso.

        Mientras Quilate solo se lanzaba a sí mismo, eso era el problema de 1.3.
        Desde que hay un `powershell.exe` que se lanza **con permisos de
        administrador**, quien controle esa variable elegiría qué binario se
        ejecuta elevado, y eso es una escalada de privilegios.
        """
        os.environ["SystemRoot"] = str(self.trampa)
        self.assertEqual(_system32(), _SYSTEM32)
        self.assertNotIn(str(self.trampa).lower(), _sys_exe("powershell.exe").lower())

    def test_los_demas_binarios_salen_de_system32(self):
        for nombre in ("powercfg.exe", "fsutil.exe", "netsh.exe"):
            ruta = _sys_exe(nombre)
            self.assertEqual(os.path.dirname(ruta).lower(), _SYSTEM32.lower(), nombre)
            self.assertTrue(os.path.exists(ruta), f"{nombre} no está donde se espera")

    def test_el_ejecutable_plantado_no_llega_a_correr(self):
        """La prueba de fuego: el mismo comando, con nombre desnudo y con ruta.

        Con el nombre desnudo se ejecuta la trampa —es el fallo que se está
        corrigiendo, y comprobarlo evita que el test pase por casualidad—; con
        `_sys_exe` se ejecuta el `powercfg` de verdad, que rechaza esos
        argumentos y no deja rastro.
        """
        # Los shells tipo POSIX (Git Bash, MSYS2) exportan esta variable para que
        # Windows deje de buscar en el directorio actual. Un usuario que hace
        # doble clic desde el Explorador no la tiene, que es justo el escenario
        # del hallazgo: sin quitarla, el test pasaría sin comprobar nada.
        os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)
        self.plantar("powercfg.exe")
        marcador = self.trampa / "marcador.txt"

        run_cmd(["powercfg", "/c", "echo.>marcador.txt"], timeout=15)
        self.assertTrue(marcador.exists(),
                        "sin este rastro el test no demuestra nada: la trampa "
                        "no se ha llegado a ejecutar ni siquiera con el nombre desnudo")
        marcador.unlink()

        run_cmd([_sys_exe("powercfg.exe"), "/c", "echo.>marcador.txt"], timeout=15)
        self.assertFalse(marcador.exists(),
                         "se ha ejecutado el binario del directorio actual")


class MotivoDeLaSalidaVacia(unittest.TestCase):
    """«No hay dato» y «no he podido preguntarlo» no son lo mismo.

    Es la misma disciplina que `PSResult.ok` aplicada a los programas de
    consola: un `fsutil.exe` sustituido que devuelve basura era indistinguible
    de un `fsutil` ausente, y `check_power_plan` no podía decir por qué no había
    leído el plan de energía.
    """

    def test_lo_que_va_bien_no_lleva_motivo(self):
        res = run_cmd([sys.executable, "-c", "print('hola')"], timeout=30)
        self.assertTrue(res.ok)
        self.assertIsNone(res.error)
        self.assertEqual(res, "hola")

    def test_un_binario_que_no_existe(self):
        res = run_cmd(["quilate_esto_no_existe_12345"], timeout=10)
        self.assertFalse(res.ok)
        self.assertIn("no está en este sistema", res.error)
        self.assertIn("quilate_esto_no_existe_12345", res.error)

    def test_el_sistema_no_deja_ejecutarlo(self):
        # Un directorio existe y no se puede ejecutar: es el camino más portátil
        # para provocar el mismo error que una política o un antivirus.
        with tempfile.TemporaryDirectory() as d:
            res = run_cmd([d], timeout=10)
        self.assertFalse(res.ok)
        self.assertIn("no permite ejecutar", res.error)

    def test_codigo_de_salida_distinto_de_cero(self):
        res = run_cmd([sys.executable, "-c",
                       "import sys; sys.stderr.write('el disco no responde'); sys.exit(3)"],
                      timeout=30)
        self.assertFalse(res.ok)
        self.assertIn("código 3", res.error)
        self.assertIn("el disco no responde", res.error, "se ha perdido el stderr")

    def test_el_stderr_va_recortado(self):
        res = run_cmd([sys.executable, "-c",
                       "import sys; sys.stderr.write('x' * 5000); sys.exit(1)"], timeout=30)
        self.assertFalse(res.ok)
        self.assertLess(len(res.error), 200, "un motivo así no cabe en el informe")

    def test_el_que_no_responde_a_tiempo(self):
        res = run_cmd([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
        self.assertFalse(res.ok)
        self.assertIn("no ha respondido en 1 s", res.error)

    def test_un_fallo_no_devuelve_media_salida(self):
        # Medio resultado es peor que ninguno: quien busque una palabra en la
        # respuesta la encontraría en un texto que el programa no llegó a acabar.
        res = run_cmd([sys.executable, "-c",
                       "print('no esta sucio'); raise SystemExit(1)"], timeout=30)
        self.assertEqual(res, "")

    def test_sigue_siendo_una_cadena_normal(self):
        # Quien solo quiera el texto no tiene por qué enterarse de nada de esto.
        res = run_cmd([sys.executable, "-c", "print('  Máximo rendimiento  ')"], timeout=30)
        self.assertIn("rendimiento", res.lower())
        self.assertEqual(res.strip(), str(res))

    def test_sin_decodificar_el_criterio_es_el_mismo(self):
        self.assertTrue(run_cmd_bytes([sys.executable, "-c", "print('ok')"], timeout=30).ok)
        fallido = run_cmd_bytes(["quilate_esto_no_existe_12345"], timeout=10)
        self.assertFalse(fallido.ok)
        self.assertIn("no está en este sistema", fallido.error)
        self.assertEqual(fallido, b"")


class NvidiaSmi(_EnDirectorioTrampa):
    def setUp(self):
        super().setUp()
        self.cache_original = sensors._nvsmi_path
        sensors._nvsmi_path = False   # False = sin buscar todavía

    def tearDown(self):
        sensors._nvsmi_path = self.cache_original
        super().tearDown()

    @unittest.skipUnless(IS_WINDOWS, "en Linux el PATH no incluye el directorio actual")
    def test_no_se_coge_el_del_directorio_actual(self):
        # `shutil.which` era peor que CreateProcess: CPython inserta os.curdir
        # en la posición 0 de la búsqueda en Windows, así que la trampa ganaba
        # incluso al nvidia-smi.exe instalado por el driver.
        self.plantar("nvidia-smi.exe")
        os.environ["PATH"] = str(self.trampa)
        encontrado = sensors._find_nvidia_smi()
        if encontrado is not None:
            self.assertFalse(
                Path(encontrado).resolve().is_relative_to(self.trampa.resolve()),
                "se ha resuelto al ejecutable del directorio actual")

    @unittest.skipUnless(IS_WINDOWS, "%SystemRoot% solo decide rutas en Windows")
    def test_no_se_coge_el_que_senala_systemroot(self):
        # Cerrar la búsqueda por PATH y dejar abierta la variable de entorno no
        # cierra nada: es el mismo hueco por otra puerta, y `%SystemRoot%` lo
        # cambia cualquiera que arranque este proceso. System32 se le pregunta a
        # Windows con GetSystemDirectoryW, que no mira el entorno.
        falso32 = self.trampa / "System32"
        falso32.mkdir()
        shutil.copy2(os.path.join(_SYSTEM32, "cmd.exe"), falso32 / "nvidia-smi.exe")
        os.environ["SystemRoot"] = str(self.trampa)
        encontrado = sensors._find_nvidia_smi() or ""
        # Estricto a propósito: la trampa existe siempre, así que el equipo tenga
        # o no una NVIDIA de verdad, resolver ahí solo puede ser el fallo.
        self.assertNotIn(str(self.trampa).lower(), encontrado.lower())

    def test_lo_que_devuelve_es_una_ruta_absoluta_o_nada(self):
        encontrado = sensors._find_nvidia_smi()
        if encontrado is not None:
            self.assertTrue(os.path.isabs(encontrado))


if __name__ == "__main__":
    unittest.main()
