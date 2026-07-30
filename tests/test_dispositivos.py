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

from datetime import datetime, timedelta

from quilate import audit
from quilate.audit import (Auditor, SinDato, _DRIVER_VIEJO_DIAS, _PNP_DELIBERADO,
                           _PNP_PROBLEMA)
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


def driver(nombre="Realtek Audio", años=1.0, proveedor="Realtek", clase="MEDIA") -> dict:
    fecha = datetime.now() - timedelta(days=round(años * 365.25))
    return {"DeviceName": nombre, "DriverDate": fecha.strftime("%Y-%m-%d"),
            "DriverProviderName": proveedor, "DeviceClass": clase}


def auditar_drivers(filas):
    a = Auditor(SystemInfo(), None)
    respuesta = filas if isinstance(filas, PSResult) else PSResult(filas)
    with patched(audit, wmi=respuesta):
        return a, a.check_old_drivers()


class DriversSinTocar(unittest.TestCase):
    """La regla del informe («anteriores a la instalación del SO») no valía.

    Medido en el equipo de referencia: la cumplían 209 de 239 drivers, porque
    Microsoft fecha los suyos en 21/06/2006 a propósito para que cualquier
    driver del fabricante gane la resolución de PnP. La señal está en los de
    terceros, que allí eran 39.
    """

    def test_los_recientes_no_son_un_hallazgo(self):
        a, resumen = auditar_drivers([driver(años=1), driver("Otro", años=3)])
        self.assertEqual(a.findings, [])
        self.assertIn("2", resumen)

    def test_uno_muy_viejo_sale_con_su_edad(self):
        a, _ = auditar_drivers([driver("Razer BlackWidow", años=9)])
        self.assertEqual([f.id for f in a.findings], ["driver_viejo"])
        self.assertIn("Razer BlackWidow", a.findings[0].title)
        self.assertIn("9 años", a.findings[0].title)

    def test_varios_se_cuentan(self):
        a, _ = auditar_drivers([driver(f"Cacharro {n}", años=6) for n in range(3)])
        self.assertIn("3 dispositivos", a.findings[0].title)

    def test_el_umbral_son_cinco_anios(self):
        limite = _DRIVER_VIEJO_DIAS / 365.25
        a, _ = auditar_drivers([driver(años=limite - 0.1)])
        self.assertEqual(a.findings, [])
        a, _ = auditar_drivers([driver(años=limite + 0.1)])
        self.assertEqual([f.id for f in a.findings], ["driver_viejo"])

    def test_van_del_mas_viejo_al_menos(self):
        a, _ = auditar_drivers([driver("Medio", años=7), driver("Viejo", años=12),
                                driver("Nuevo", años=6)])
        detalle = a.findings[0].detail
        self.assertLess(detalle.index("Viejo"), detalle.index("Medio"))
        self.assertLess(detalle.index("Medio"), detalle.index("Nuevo"))

    def test_la_lista_larga_se_recorta(self):
        a, _ = auditar_drivers([driver(f"Cacharro {n}", años=6 + n) for n in range(7)])
        self.assertIn("y 3 más", a.findings[0].detail)

    def test_una_lista_corta_no_esconde_ninguno(self):
        # Recortar a cuatro de cinco dejaba el título contando cinco, la lista
        # enseñando cuatro y un «y 1 más» que no decía cuál era el quinto: el
        # dispositivo se ha medido y luego no hay forma de saber su nombre.
        a, _ = auditar_drivers([driver(f"Cacharro {n}", años=6 + n) for n in range(5)])
        detalle = a.findings[0].detail
        self.assertNotIn("más", detalle.split(".")[0])
        for n in range(5):
            self.assertIn(f"Cacharro {n}", detalle)

    def test_no_promete_velocidad(self):
        a, _ = auditar_drivers([driver(años=9)])
        f = a.findings[0]
        self.assertEqual(f.gain, 0.0)
        self.assertEqual(f.severity, "low")
        self.assertEqual(f.category, "dispositivos")

    def test_dice_que_no_corre_prisa(self):
        # Un driver de hace años que funciona bien puede quedarse. Presentarlo
        # como urgente empujaría a tocar lo que no está roto.
        a, _ = auditar_drivers([driver(años=9)])
        self.assertIn("no corre prisa", " ".join(a.findings[0].steps))


class LoQueNoCuentaComoDriverViejo(unittest.TestCase):
    def test_la_grafica_la_audita_otra_comprobacion(self):
        # `check_gpu_drivers` ya avisa de la GPU, con su fecha y su ganancia.
        # Repetirla aquí sería el mismo aviso dos veces en el mismo informe.
        a, resumen = auditar_drivers([driver("GeForce RTX 3060", años=9, clase="DISPLAY")])
        self.assertEqual(a.findings, [])
        self.assertIn("no hay drivers", resumen)

    def test_una_fecha_en_el_futuro_no_es_vieja(self):
        # Verificado: los hay. No es error del fabricante sino de la máquina
        # que firmó el paquete.
        a, _ = auditar_drivers([driver(años=-0.3)])
        self.assertEqual(a.findings, [])

    def test_el_mismo_dispositivo_solo_cuenta_una_vez(self):
        # Un teclado aparece una vez por función que expone: HID, teclado,
        # ratón, control de consumo. Es un solo cacharro.
        a, resumen = auditar_drivers([
            driver("Teclado", años=9, clase="HIDCLASS"),
            driver("Teclado", años=6, clase="KEYBOARD"),
            driver("Teclado", años=6, clase="MOUSE")])
        self.assertIn("Teclado (9 años)", a.findings[0].detail)
        self.assertEqual(a.findings[0].title.count("Teclado"), 1)
        self.assertIn("de 1 de terceros", resumen)

    def test_se_queda_con_la_fecha_mas_antigua(self):
        # La más vieja es la que dice desde cuándo no se toca ese dispositivo.
        a, _ = auditar_drivers([driver("Teclado", años=1), driver("Teclado", años=9)])
        self.assertIn("9 años", a.findings[0].title)

    def test_un_driver_sin_nombre_no_se_puede_leer(self):
        # Sin nombre no hay nada que enseñarle a nadie, así que se descarta;
        # pero descartarlo es no haber podido leerlo, no haberlo excluido con
        # motivo, y el veredicto tiene que notar la diferencia.
        with self.assertRaises(SinDato):
            auditar_drivers([{"DeviceName": "", "DriverDate": "2010-01-01",
                              "DeviceClass": "NET"}])


class CuandoNoSePuedePreguntar(unittest.TestCase):
    def test_la_consulta_fallida_no_da_por_buenos_los_drivers(self):
        with self.assertRaises(SinDato):
            auditar_drivers(PSResult((), ok=False, error="acceso denegado"))

    def test_sin_drivers_de_terceros_no_falta_ningun_dato(self):
        # Un Windows recién instalado en hardware corriente funciona entero con
        # drivers de caja. Es la respuesta, no una ausencia de respuesta.
        a, resumen = auditar_drivers([])
        self.assertEqual(a.findings, [])
        self.assertIn("no hay drivers de terceros", resumen)

    def test_fechas_ilegibles_no_se_dan_por_recientes(self):
        with self.assertRaises(SinDato):
            auditar_drivers([{"DeviceName": "Cacharro", "DriverDate": "vete a saber",
                              "DeviceClass": "NET"}])

    def test_una_fecha_ilegible_entre_varias_buenas_no_arrastra(self):
        a, resumen = auditar_drivers([
            {"DeviceName": "Roto", "DriverDate": None, "DeviceClass": "NET"},
            driver("Bueno", años=9)])
        self.assertEqual([f.id for f in a.findings], ["driver_viejo"])
        self.assertIn("de 1 de terceros", resumen)


class LaConsultaDeDrivers(unittest.TestCase):
    def _consulta(self) -> str:
        capturada = {}

        def espia(select, *a, **k):
            capturada["select"] = select
            return PSResult(())

        with patched(audit, wmi=espia):
            Auditor(SystemInfo(), None).check_old_drivers()
        return capturada["select"]

    def test_microsoft_se_descarta_en_la_consulta(self):
        # Traerse los 239 para quedarse con 39 cuesta lo mismo que traerse 39,
        # pero el filtro deja escrito en la propia consulta cuál es la regla.
        self.assertIn("NOT DriverProviderName LIKE 'Microsoft%'", self._consulta())

    def test_el_filtro_es_por_prefijo_y_no_por_igualdad(self):
        # «Microsoft», «Microsoft Windows» y «Microsoft Corporation» conviven en
        # el mismo equipo: comparar por igualdad dejaría pasar dos de tres.
        consulta = self._consulta()
        self.assertNotIn("DriverProviderName <> 'Microsoft'", consulta)
        self.assertIn("Win32_PnPSignedDriver", consulta)


if __name__ == "__main__":
    unittest.main()
