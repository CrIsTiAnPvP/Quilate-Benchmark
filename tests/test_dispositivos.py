"""El signo de exclamación amarillo del Administrador de dispositivos.

Windows lleva la cuenta de qué hardware ha dado por imposible, y la guarda en
`ConfigManagerErrorCode`. Es un problema real —el Bluetooth que no aparece, el
lector de tarjetas que no lee— que casi nadie relaciona con un driver, porque
para verlo hay que abrir una ventana que nadie abre.

El cuidado está en dos sitios. Uno, que no todo código distinto de cero es una
avería: quien deshabilita a mano la webcam de un portátil no quiere que se le
acuse de tenerla estropeada. Y dos, la forma de preguntar. Lo natural sería
`Get-PnpDevice -Status Error,Degraded`, pero ese cmdlet **lanza excepción cuando
no hay ninguno**:

    ERROR: No Win32_PnPEntity objects found with property 'Status' equal to 'Error'.

Con él, el equipo sano y la consulta rota llegan a la comprobación exactamente
iguales, y el equipo sano acabaría contado como «Sin comprobar». El filtro WQL
sobre `Win32_PnPEntity` devuelve lista vacía, que es la respuesta correcta.
"""

from __future__ import annotations

import unittest

from quilate import audit
from quilate.audit import Auditor, SinDato, _PNP_DELIBERADO, _PNP_PROBLEMA
from quilate.platform_utils import PSResult
from quilate.sysinfo import SystemInfo
from tests.support import patched


def dispositivo(nombre="Lector de tarjetas", codigo=28, clase="System") -> dict:
    return {"Name": nombre, "PNPClass": clase, "ConfigManagerErrorCode": codigo}


def auditar(filas):
    """`filas` puede ser un `PSResult` ya hecho o una lista de dispositivos."""
    a = Auditor(SystemInfo(), None)
    respuesta = filas if isinstance(filas, PSResult) else PSResult(filas)
    with patched(audit, wmi=respuesta):
        return a, a.check_device_problems()


class DispositivosRotos(unittest.TestCase):
    def test_ninguno_con_problema(self):
        a, resumen = auditar([])
        self.assertEqual(a.findings, [])
        self.assertIn("ninguno", resumen)

    def test_uno_roto_sale_por_su_nombre(self):
        a, resumen = auditar([dispositivo("Realtek Card Reader", 28)])
        self.assertEqual([f.id for f in a.findings], ["dispositivo_con_error"])
        self.assertIn("Realtek Card Reader", a.findings[0].title)
        self.assertIn("no tiene drivers instalados", a.findings[0].detail)
        self.assertIn("1", resumen)

    def test_varios_se_cuentan_en_el_titulo(self):
        a, _ = auditar([dispositivo("Uno", 28), dispositivo("Dos", 43),
                        dispositivo("Tres", 10)])
        self.assertIn("3 dispositivos", a.findings[0].title)

    def test_cada_codigo_conocido_se_traduce(self):
        for codigo, texto in _PNP_PROBLEMA.items():
            with self.subTest(codigo=codigo):
                a, _ = auditar([dispositivo(codigo=codigo)])
                self.assertIn(texto, a.findings[0].detail)

    def test_un_codigo_desconocido_sigue_siendo_un_problema(self):
        # No saber qué significa el 99 no es motivo para callarse que Windows ha
        # marcado el dispositivo. Decir el número permite buscarlo.
        a, _ = auditar([dispositivo("Chisme raro", 99)])
        self.assertEqual([f.id for f in a.findings], ["dispositivo_con_error"])
        self.assertIn("99", a.findings[0].detail)

    def test_la_lista_larga_se_recorta(self):
        a, _ = auditar([dispositivo(f"Cacharro {n}", 28) for n in range(9)])
        detalle = a.findings[0].detail
        self.assertIn("Cacharro 3", detalle)
        self.assertNotIn("Cacharro 4", detalle)
        self.assertIn("Y 5 más", detalle)

    def test_no_promete_velocidad(self):
        # Un dispositivo que no arranca no es lentitud, y prometer un porcentaje
        # por arreglarlo sería inventárselo.
        a, _ = auditar([dispositivo()])
        f = a.findings[0]
        self.assertEqual(f.gain, 0.0)
        self.assertIn("no es una optimización", f.gain_note)
        self.assertEqual(f.category, "dispositivos")


class LoQueAlguienApagoAProposito(unittest.TestCase):
    """Deshabilitar la webcam no es tenerla estropeada."""

    def test_los_deliberados_no_son_hallazgos(self):
        for codigo in sorted(_PNP_DELIBERADO):
            with self.subTest(codigo=codigo):
                a, resumen = auditar([dispositivo("Cámara integrada", codigo)])
                self.assertEqual(a.findings, [], f"ha acusado al código {codigo}")
                self.assertIn("a propósito", resumen)

    def test_pero_se_cuentan_en_el_resumen(self):
        a, resumen = auditar([dispositivo("Cámara", 22), dispositivo("Wi-Fi", 22)])
        self.assertEqual(a.findings, [])
        self.assertIn("2", resumen)

    def test_mezclados_con_uno_roto_de_verdad(self):
        a, resumen = auditar([dispositivo("Cámara", 22), dispositivo("Bluetooth", 43)])
        self.assertEqual([f.id for f in a.findings], ["dispositivo_con_error"])
        self.assertIn("Bluetooth", a.findings[0].title)
        self.assertNotIn("Cámara", a.findings[0].detail)
        self.assertIn("1 con problema", resumen)

    def test_el_codigo_cero_no_llega_ni_a_contarse(self):
        # El filtro lo excluye en la consulta, pero si algún día deja de
        # hacerlo, un dispositivo sano no puede colarse como apagado.
        a, resumen = auditar([dispositivo("Teclado", 0)])
        self.assertEqual(a.findings, [])
        self.assertNotIn("a propósito", resumen)


class RespuestasQueNoSonCodigos(unittest.TestCase):
    def test_la_consulta_fallida_no_da_por_sano_el_equipo(self):
        with self.assertRaises(SinDato):
            auditar(PSResult((), ok=False, error="acceso denegado"))

    def test_el_motivo_del_fallo_se_arrastra(self):
        try:
            auditar(PSResult((), ok=False, error="acceso denegado"))
        except SinDato as exc:
            self.assertIn("acceso denegado", str(exc))

    def test_un_codigo_que_no_es_un_entero_se_ignora(self):
        # `isinstance(True, int)` es cierto en Python: un booleano aquí sería
        # una respuesta con otra forma, no el código 1.
        for valor in (None, True, False, "28", 1.5, [28]):
            with self.subTest(valor=valor):
                a, _ = auditar([dispositivo(codigo=valor)])
                self.assertEqual(a.findings, [], f"ha opinado sobre {valor!r}")

    def test_un_dispositivo_sin_nombre_no_revienta(self):
        a, _ = auditar([{"Name": None, "PNPClass": "USB", "ConfigManagerErrorCode": 28}])
        self.assertIn("USB", a.findings[0].detail)

    def test_sin_nombre_ni_clase_tampoco(self):
        a, _ = auditar([{"ConfigManagerErrorCode": 28}])
        self.assertEqual([f.id for f in a.findings], ["dispositivo_con_error"])


class LaConsultaQueSeLePide(unittest.TestCase):
    """La trampa comprobada ejecutándola, blindada aquí."""

    def _consulta(self) -> str:
        capturada = {}

        def espia(select, *a, **k):
            capturada["select"] = select
            return PSResult(())

        a = Auditor(SystemInfo(), None)
        with patched(audit, wmi=espia):
            a.check_device_problems()
        return capturada["select"]

    def test_no_se_usa_el_cmdlet_que_falla_cuando_todo_va_bien(self):
        self.assertNotIn("Get-PnpDevice", self._consulta())

    def test_se_filtra_en_la_consulta_y_no_en_python(self):
        # Traerse los 236 dispositivos del equipo para descartar 236 costaría
        # cinco veces más y no aportaría nada.
        consulta = self._consulta()
        self.assertIn("Win32_PnPEntity", consulta)
        self.assertIn("ConfigManagerErrorCode <> 0", consulta)


class LasDosTablas(unittest.TestCase):
    def test_ningun_codigo_esta_en_las_dos(self):
        self.assertEqual(set(_PNP_PROBLEMA) & set(_PNP_DELIBERADO), set())

    def test_el_cero_no_esta_en_ninguna(self):
        # El cero es «este dispositivo funciona»: interpretarlo como cualquier
        # otra cosa acusaría a todo el equipo.
        self.assertNotIn(0, _PNP_PROBLEMA)
        self.assertNotIn(0, _PNP_DELIBERADO)

    def test_las_explicaciones_no_son_jerga(self):
        # El informe lo lee quien no sabe qué es un enumerador PnP.
        for codigo, texto in _PNP_PROBLEMA.items():
            with self.subTest(codigo=codigo):
                self.assertNotIn("PnP", texto)
                self.assertNotIn("CM_PROB", texto)
                # Cada texto se encadena con «: » detrás del nombre y con «. »
                # delante del siguiente, así que no puede traer su propio punto.
                self.assertFalse(texto.endswith("."))


if __name__ == "__main__":
    unittest.main()
