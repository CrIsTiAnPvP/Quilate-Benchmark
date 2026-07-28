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

import argparse
import io
import subprocess
import unittest
from contextlib import redirect_stdout

from quilate import cli, elevacion
from quilate.const import IS_WINDOWS
from quilate.elevacion import (NO_PEDIDOS, SIN_PERMISOS, _CONSULTAS_ELEVADAS, _guion,
                               _nombre_de_tuberia, consulta_elevada)
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


class ElLoteQueSeEjecutaConPermisos(unittest.TestCase):
    """Lo que Quilate hace cuando le das permisos, en una sola lista legible."""

    def test_ninguna_consulta_cambia_nada(self):
        # Es la promesa que hace el README: todo lo que se ejecuta elevado es de
        # lectura. Aquí deja de ser una promesa.
        prohibidos = ("Set-", "Remove-", "New-Item", "Stop-", "Start-", "Disable-",
                      "Enable-", "Clear-", "Restart-", "Invoke-", "Add-", "Format-",
                      "Out-File", "reg ", "cmd ", "&{")
        for clave, consulta in _CONSULTAS_ELEVADAS.items():
            for prohibido in prohibidos:
                with self.subTest(consulta=clave, prohibido=prohibido):
                    self.assertNotIn(prohibido, consulta)

    # Lo que cada consulta tiene permitido hacer con los permisos concedidos.
    # No es una lista de comodidad: añadir una consulta al lote obliga a pasar
    # por aquí, que es justo lo que se quiere. Nada se ejecuta con privilegios
    # sin que alguien lo haya escrito dos veces.
    LECTURAS = {
        "reliability": "Get-PhysicalDisk",
        "smart": "Get-CimInstance",
        "bitlocker": "Get-BitLockerVolume",
        "secureboot": "Confirm-SecureBootUEFI",
        "tpm": "Get-Tpm",
        "smb1": "Get-WindowsOptionalFeature",
        "arranque": "Get-WinEvent",
        "fsdirty": "dirty query",
    }

    def test_cada_consulta_hace_lo_que_dice_y_nada_mas(self):
        self.assertEqual(set(self.LECTURAS), set(_CONSULTAS_ELEVADAS),
                         "hay una consulta elevada que nadie ha revisado")
        for clave, lectura in self.LECTURAS.items():
            with self.subTest(consulta=clave):
                self.assertIn(lectura, _CONSULTAS_ELEVADAS[clave])

    def test_fsutil_solo_pregunta(self):
        # Es el único que no es PowerShell, y el mismo binario con `dirty set`
        # marcaría el volumen a mano y programaría un chkdsk en el siguiente
        # arranque. Aquí solo se le pregunta.
        for clave, consulta in _CONSULTAS_ELEVADAS.items():
            if "fsutil" not in consulta:
                continue
            with self.subTest(consulta=clave):
                self.assertIn("dirty query", consulta)
                self.assertNotIn("dirty set", consulta)

    def test_el_entorno_no_elige_que_se_ejecuta(self):
        """Lo que corre elevado no puede salir de una variable de entorno.

        El proceso hijo hereda el entorno del padre, que no está elevado. Si la
        ruta de un binario saliera de ahí, quien pudiera cambiar `SystemRoot`
        elegiría qué se ejecuta como administrador. Por eso las rutas salen de
        `[Environment]::SystemDirectory`, que lo pregunta al sistema.

        La única variable que se lee es `firmware_type`, y solo decide si la
        pregunta del arranque seguro procede o no: manipularla daría un informe
        equivocado, no privilegios.
        """
        for clave, consulta in _CONSULTAS_ELEVADAS.items():
            for variable in ("$env:SystemRoot", "$env:Path", "$env:TEMP",
                             "$env:LOCALAPPDATA", "$env:SystemDrive",
                             "$env:USERPROFILE", "$env:ComSpec"):
                with self.subTest(consulta=clave, variable=variable):
                    self.assertNotIn(variable, consulta)


class RecogerUnaSolaVez(unittest.TestCase):
    def setUp(self):
        elevacion.olvidar()
        self.pedir = elevacion._pedir
        self.lanzar = elevacion._lanzar_elevado
        self.admin = elevacion.is_admin
        elevacion.is_admin = lambda: False

    def tearDown(self):
        elevacion.olvidar()
        elevacion._pedir = self.pedir
        elevacion._lanzar_elevado = self.lanzar
        elevacion.is_admin = self.admin

    def test_sin_permiso_para_preguntar_no_se_pregunta(self):
        # Importar Quilate como biblioteca, o correr sus tests, no puede
        # sacarle a nadie un diálogo de Windows por sorpresa.
        llamadas = []
        elevacion._lanzar_elevado = lambda *a: llamadas.append(a) or True
        elevacion.permitir_uac(False)
        lote = elevacion.recoger()
        self.assertEqual(llamadas, [])
        self.assertEqual(set(lote), set(_CONSULTAS_ELEVADAS))
        self.assertTrue(all(not v.ok for v in lote.values()))
        self.assertTrue(all(v.error == NO_PEDIDOS for v in lote.values()))

    def test_no_pedidos_y_denegados_no_son_lo_mismo(self):
        # El informe tiene que poder distinguir «no te lo pedí» de «dijiste que
        # no»: son dos frases distintas para quien lo lee.
        self.assertNotEqual(NO_PEDIDOS, SIN_PERMISOS)

    def test_solo_se_pide_una_vez(self):
        # Cada llamada sería otro aviso de UAC, y encadenar diálogos es la forma
        # más rápida de enseñarle a alguien a darle a «Sí» sin leer.
        llamadas = []
        elevacion._lanzar_elevado = lambda *a: llamadas.append(a) or False
        elevacion.permitir_uac(True)
        primero = elevacion.recoger()
        segundo = elevacion.recoger()
        self.assertEqual(len(llamadas), 1)
        self.assertIs(primero, segundo)

    @unittest.skipUnless(IS_WINDOWS, "el atajo de estar ya elevado es de Windows")
    def test_estando_ya_elevado_no_se_monta_ningun_canal(self):
        # Montar un proceso aparte y un aviso para leer lo que este mismo puede
        # leer no tendría ningún sentido.
        llamadas = []
        elevacion.is_admin = lambda: True
        elevacion._lanzar_elevado = lambda *a: llamadas.append(a) or True
        elevacion.permitir_uac(True)
        lote = elevacion.recoger()
        self.assertEqual(llamadas, [])
        self.assertEqual(set(lote), set(_CONSULTAS_ELEVADAS))


class CuandoSePideYCuandoNo(unittest.TestCase):
    """El aviso de UAC ya no relanza nada: es la pregunta en sí.

    Antes había que preguntar en la consola antes de sacarlo, porque aceptar
    mandaba el análisis a una ventana nueva y esta se quedaba mirando. Ahora el
    análisis ocurre aquí y lo único que se va es un proceso de dos segundos, así
    que el propio diálogo de Windows es la pregunta y no hace falta otra.
    """

    def setUp(self):
        elevacion.olvidar()
        self.pedir, self.admin = elevacion._pedir, elevacion.is_admin
        self.interactivo, self.lanzar = cli._interactive, elevacion._lanzar_elevado
        elevacion.is_admin = cli.is_admin = lambda: False
        elevacion.permitir_uac(False)
        self.pedido = []
        elevacion._lanzar_elevado = lambda *a: self.pedido.append(a) or False

    def tearDown(self):
        elevacion.olvidar()
        elevacion._pedir, elevacion.is_admin = self.pedir, self.admin
        cli.is_admin, cli._interactive = self.admin, self.interactivo
        elevacion._lanzar_elevado = self.lanzar

    def _correr(self, interactivo=True, no_elevate=False, elevate=False) -> str:
        cli._interactive = lambda: interactivo
        salida = io.StringIO()
        with redirect_stdout(salida):
            cli._pedir_permisos(argparse.Namespace(no_elevate=no_elevate,
                                                   elevate=elevate))
        return salida.getvalue()

    def test_con_alguien_delante_se_pide(self):
        texto = self._correr(interactivo=True)
        self.assertEqual(len(self.pedido), 1)
        self.assertIn("solo leyendo", texto)

    def test_se_dice_para_que_antes_de_pedirlo(self):
        # Pedir permisos sin decir para qué es lo que enseña a aceptar cualquier
        # aviso sin leerlo.
        texto = self._correr(interactivo=True)
        self.assertIn("cifrado", texto)
        self.assertIn("SMB1", texto)
        self.assertIn("sin comprobar", texto)

    def test_sin_nadie_delante_no_se_saca_el_dialogo(self):
        # Un UAC en una tarea programada se queda parado hasta que alguien lo
        # cierre, y mientras no avanza nada.
        texto = self._correr(interactivo=False)
        self.assertEqual(self.pedido, [])
        self.assertIn("nadie delante", texto)

    def test_pero_con_elevate_se_pide_igualmente(self):
        self._correr(interactivo=False, elevate=True)
        self.assertEqual(len(self.pedido), 1)

    def test_no_elevate_no_pregunta_nada(self):
        texto = self._correr(interactivo=True, no_elevate=True)
        self.assertEqual(self.pedido, [])
        self.assertIn("--no-elevate", texto)

    def test_estando_ya_elevado_no_se_pregunta(self):
        cli.is_admin = elevacion.is_admin = lambda: True
        self.assertEqual(self._correr(interactivo=True), "")
        self.assertEqual(self.pedido, [])

    def test_decir_que_no_se_cuenta_y_se_sigue(self):
        self.assertIn("se continúa sin ellos", self._correr(interactivo=True))

    def test_ya_no_hay_pregunta_previa_en_la_consola(self):
        # `_ask_elevate` existía porque aceptar el UAC abría otra ventana. Ese
        # motivo ya no existe, y dejar dos preguntas seguidas sobraba.
        self.assertFalse(hasattr(cli, "_ask_elevate"))
        self.assertFalse(hasattr(cli, "_try_elevate"))


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

    # Aquí había un test que consultaba el blob SMART sin elevar y exigía que
    # fallara con «Acceso denegado», que es lo que hace en un equipo recién
    # arrancado. Se ha quitado porque no era un test: era una afirmación sobre
    # el estado de la máquina. Comprobado ejecutándolo, después de que un
    # proceso elevado lea esa clase una vez, el mismo `Get-CimInstance` empieza
    # a contestar sin permisos, y el test pasaba o fallaba según lo que se
    # hubiera ejecutado antes en ese Windows. Que una consulta denegada se
    # cuente como denegada ya lo prueban `CuandoNoHayPermisos` y
    # `test_una_consulta_rota_no_se_lleva_a_las_demas`, que no dependen de nada
    # de fuera.

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
