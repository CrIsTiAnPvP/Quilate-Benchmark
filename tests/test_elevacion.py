"""El canal por el que vuelve lo que solo se puede leer con permisos.

El resultado de un proceso elevado no puede volver por la salida estándar.
`ShellExecuteEx` con el verbo «runas» —el único camino que Windows soporta para
elevar— devuelve el handle del proceso pero no admite `STARTUPINFO`, así que no
hay tubería que heredar. Lo habitual es que el hijo escriba en un fichero
temporal; aquí no, porque sería una escritura elevada a una ruta que controla el
usuario, que es la forma clásica de una escalada de privilegios por enlace
simbólico. El padre monta una tubería con nombre y el hijo se conecta a ella.

Los tests que hablan con PowerShell de verdad lanzan el mismo guion **sin**
elevar, sustituyendo solo el lanzador. Así se prueba entero lo que se puede
probar sin un diálogo de UAC delante: el guion, la tubería, el troceado y la
distinción entre una consulta que falla y una que no devuelve nada.
"""

from __future__ import annotations

import subprocess
import unittest

from quilate import elevacion
from quilate.const import IS_WINDOWS
from quilate.elevacion import SIN_PERMISOS, _guion, _nombre_de_tuberia, consulta_elevada
from quilate.platform_utils import _sys_exe

CONSULTAS = {"os": "Get-CimInstance Win32_OperatingSystem | Select-Object Caption"}


class ElNombreDeLaTuberia(unittest.TestCase):
    def test_no_se_repite(self):
        self.assertNotEqual(_nombre_de_tuberia(), _nombre_de_tuberia())

    def test_es_largo_de_verdad(self):
        # Se crea con FILE_FLAG_FIRST_PIPE_INSTANCE, así que un nombre ya
        # ocupado hace fallar la creación en vez de compartir la tubería con
        # quien llegó antes. Adivinarlo es la única vía, y son 128 bits.
        self.assertGreaterEqual(len(_nombre_de_tuberia()), 32)


class ElGuionQueSeEjecutaConPermisos(unittest.TestCase):
    def test_cada_consulta_va_en_su_propio_try(self):
        guion = _guion({"uno": "Get-A", "dos": "Get-B"}, "tuberia")
        self.assertIn("$r['uno'] = Leer { Get-A }", guion)
        self.assertIn("$r['dos'] = Leer { Get-B }", guion)

    def test_un_fallo_no_puede_pasar_por_silencio(self):
        # Sin esto, una clase WMI que no existe escribe el error y devuelve
        # vacío: el try/catch no se entera y el bloque saldría con «se ejecutó y
        # no había nada», que es lo contrario de lo que ha pasado.
        self.assertIn("$ErrorActionPreference = 'Stop'", _guion(CONSULTAS, "t"))

    def test_el_resultado_sale_por_la_tuberia_y_no_por_un_fichero(self):
        guion = _guion(CONSULTAS, "la-tuberia")
        self.assertIn("NamedPipeClientStream", guion)
        self.assertIn("la-tuberia", guion)
        for sospechoso in ("Set-Content", "Out-File", "TEMP", "$env:"):
            with self.subTest(sospechoso=sospechoso):
                self.assertNotIn(sospechoso, guion)

    def test_no_escribe_nada_en_disco(self):
        # Lo que corre con privilegios no toca el sistema de ficheros: ni
        # siquiera para dejar el resultado.
        self.assertNotIn("Remove-Item", _guion(CONSULTAS, "t"))
        self.assertNotIn("New-Item", _guion(CONSULTAS, "t"))


class LoQueSeEjecutaEsFijo(unittest.TestCase):
    """Lo que corre con permisos no puede depender de lo que nadie escriba."""

    def test_las_consultas_del_modulo_son_constantes(self):
        import inspect
        fuente = inspect.getsource(elevacion)
        # No hay ninguna forma de que un argumento de la línea de órdenes acabe
        # dentro del guion: no se lee `sys.argv` ni el entorno en todo el módulo.
        self.assertNotIn("sys.argv", fuente)
        self.assertNotIn("os.environ", fuente)

    def test_el_powershell_es_el_de_system32(self):
        # Misma razón que en 1.1: `CreateProcess` mira el directorio actual
        # antes que System32, y esto se lanza con privilegios.
        self.assertTrue(_sys_exe("powershell.exe").lower().endswith(
            "\\windowspowershell\\v1.0\\powershell.exe") or not IS_WINDOWS)


class CuandoNoHayPermisos(unittest.TestCase):
    def test_decir_que_no_al_uac_no_es_un_fallo_del_programa(self):
        # Es un camino normal: el informe tiene que poder decir «no se comprobó
        # porque no diste permisos» en vez de dar por bueno lo que nadie miró.
        original = elevacion._lanzar_elevado
        elevacion._lanzar_elevado = lambda *a: False
        try:
            res = consulta_elevada({"uno": "Get-A", "dos": "Get-B"})
        finally:
            elevacion._lanzar_elevado = original
        self.assertEqual(set(res), {"uno", "dos"})
        for clave, valor in res.items():
            with self.subTest(consulta=clave):
                self.assertFalse(valor.ok)
                self.assertEqual(valor.error, SIN_PERMISOS)

    def test_el_motivo_se_puede_enseñar_tal_cual(self):
        self.assertNotIn("Error", SIN_PERMISOS)
        self.assertNotIn("ShellExecute", SIN_PERMISOS)

    def test_sin_consultas_no_se_pide_nada(self):
        # Pedir un UAC para no preguntar nada sería gratuito y molesto.
        llamadas = []
        original = elevacion._lanzar_elevado
        elevacion._lanzar_elevado = lambda *a: llamadas.append(a) or True
        try:
            self.assertEqual(consulta_elevada({}), {})
        finally:
            elevacion._lanzar_elevado = original
        self.assertEqual(llamadas, [])


@unittest.skipUnless(IS_WINDOWS, "la tubería con nombre es de Windows")
class ElCanalDeVerdad(unittest.TestCase):
    """El camino entero salvo el `runas`, que necesita a alguien delante."""

    def setUp(self):
        self.hijos = []

    def tearDown(self):
        for hijo in self.hijos:
            try:
                hijo.wait(timeout=20)
            except subprocess.TimeoutExpired:
                hijo.kill()

    def _sin_uac(self, exe: str, parametros: str) -> bool:
        """El mismo lanzamiento, sin el diálogo de UAC delante.

        Sustituye solo el `runas`, que es la única pieza que no se puede probar
        sin alguien que acepte un aviso. El guion, la tubería, el JSON y el
        troceado son idénticos al camino de verdad.
        """
        self.hijos.append(subprocess.Popen(
            [exe] + parametros.split(" "), creationflags=0x08000000))  # CREATE_NO_WINDOW
        return True

    def _consultar(self, consultas, timeout=40):
        original = elevacion._lanzar_elevado
        elevacion._lanzar_elevado = self._sin_uac
        try:
            return consulta_elevada(consultas, timeout=timeout)
        finally:
            elevacion._lanzar_elevado = original

    def test_ida_y_vuelta(self):
        res = self._consultar(CONSULTAS)
        self.assertTrue(res["os"].ok, res["os"].error)
        self.assertIn("Caption", list(res["os"])[0])

    def test_una_consulta_rota_no_se_lleva_a_las_demas(self):
        res = self._consultar({
            "buena": "Get-CimInstance Win32_OperatingSystem | Select-Object Caption",
            "rota": "Get-CimInstance NoExisteEstaClase"})
        self.assertTrue(res["buena"].ok)
        self.assertFalse(res["rota"].ok)
        self.assertTrue(res["rota"].error)

    def test_ejecutada_y_sin_resultados_no_es_un_fallo(self):
        res = self._consultar({
            "vacia": "Get-CimInstance Win32_OperatingSystem | Where-Object { $false }"})
        self.assertTrue(res["vacia"].ok)
        self.assertEqual(len(res["vacia"]), 0)

    def test_una_denegada_lo_dice(self):
        # Sin elevar, esta es exactamente la que va a fallar en un equipo real.
        res = self._consultar({
            "smart": "Get-CimInstance -Namespace root\\wmi "
                     "-ClassName MSStorageDriver_FailurePredictData"})
        self.assertFalse(res["smart"].ok)

    def test_un_hijo_que_no_contesta_no_deja_esperando(self):
        # Con una tubería bloqueante, un UAC rechazado o un PowerShell que muere
        # antes de conectarse dejarían a Quilate esperando para siempre.
        original = elevacion._lanzar_elevado
        elevacion._lanzar_elevado = lambda *a: True      # dice que sí y no lanza nada
        try:
            res = consulta_elevada(CONSULTAS, timeout=2)
        finally:
            elevacion._lanzar_elevado = original
        self.assertFalse(res["os"].ok)
        self.assertIn("no ha contestado", res["os"].error)


if __name__ == "__main__":
    unittest.main()
