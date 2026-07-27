"""Enlace de red: lo que la tarjeta puede frente a lo que está haciendo.

Una Wi-Fi 6 conectada en 802.11ac y un adaptador gigabit negociando a 100 Mbps
por un cable malo son el mismo patrón que la RAM a velocidad JEDEC: el hardware
da para más y nadie lo mira. La parte delicada es leer `netsh`, que viene
traducido y —a diferencia de casi todo lo demás en Windows— responde en UTF-8.

También se comprueba lo que NO puede salir: el nombre de la red, el punto de
acceso y la MAC identifican al usuario y no dicen nada sobre el rendimiento.
"""

from __future__ import annotations

import json
import unittest

from quilate import audit
from quilate.network import (WIFI_TECHO, _decodificar, adapters, collect,
                             wifi_capability, wifi_link)
from quilate.sysinfo import SystemInfo
from tests.support import FixtureCase, patched

# Salida real de `netsh wlan show interfaces` de un Windows 11 en español, con
# los identificadores sustituidos. Se conserva tal cual llega, en UTF-8.
NETSH_ES = """
Hay 1 interfaz en el sistema:

    Nombre                   : Wi-Fi
    Descripción              : Realtek 8852BE Wireless LAN WiFi 6 PCI-E NIC
    GUID                     : 00000000-0000-0000-0000-000000000000
    Dirección física         : 00:00:00:00:00:00
    Estado                   : conectado
    SSID                     : RED-DE-PRUEBA
    AP BSSID                 : 00:00:00:00:00:00
    Banda                    : 5 GHz
    Canal                    : 100
    Tipo de radio            : 802.11ac
    Autenticación            : WPA2-Personal
    Velocidad de recepción (Mbps)   : 866.7
    Velocidad de transmisión (Mbps) : 866.7
    Señal                           : 100%
    Rssi                            : -50
"""

NETSH_EN = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    State                  : connected
    SSID                   : SOME-NETWORK
    BSSID                  : 00:00:00:00:00:00
    Band                   : 2.4 GHz
    Channel                : 6
    Radio type             : 802.11n
    Receive rate (Mbps)    : 144.4
    Transmit rate (Mbps)   : 144.4
    Signal                 : 62%
    Rssi                   : -74
"""


class Decodificacion(unittest.TestCase):
    """`netsh wlan` responde en UTF-8 y `fsutil` en la página OEM."""

    def test_utf8(self):
        self.assertIn("Descripción", _decodificar("Descripción".encode("utf-8")))

    def test_pagina_oem(self):
        self.assertIn("Descripción", _decodificar("Descripción".encode("cp850")))

    def test_sin_salida(self):
        self.assertEqual(_decodificar(None), "")
        self.assertEqual(_decodificar(b""), "")

    def test_gana_la_que_no_rompe_acentos(self):
        # El fallo que esto evita: elegir mal deja «Descripci?n» y luego no se
        # encuentra ninguna etiqueta.
        self.assertNotIn("�", _decodificar("señal canción".encode("utf-8")))


class LecturaDelEnlace(unittest.TestCase):
    def _leer(self, salida: str) -> dict:
        # Se finge Windows además de la salida: interpretar el texto no depende
        # del sistema, y estos casos tienen que poder probarse también en Linux.
        import quilate.network as red
        original = red.run_cmd_bytes, red.IS_WINDOWS
        red.run_cmd_bytes = lambda *a, **k: salida.encode("utf-8")
        red.IS_WINDOWS = True
        try:
            return wifi_link()
        finally:
            red.run_cmd_bytes, red.IS_WINDOWS = original

    def test_espanol(self):
        d = self._leer(NETSH_ES)
        self.assertEqual(d["radio"], "802.11ac")
        self.assertEqual(d["rate_mbps"], 866.7)
        self.assertEqual(d["band_ghz"], "5")
        self.assertEqual(d["channel"], 100)
        self.assertEqual(d["rssi_dbm"], -50)

    def test_ingles(self):
        # Las mismas cifras se sacan sin depender de una sola traducción.
        d = self._leer(NETSH_EN)
        self.assertEqual(d["radio"], "802.11n")
        self.assertEqual(d["rate_mbps"], 144.4)
        self.assertEqual(d["band_ghz"], "2.4")
        self.assertEqual(d["channel"], 6)
        self.assertEqual(d["rssi_dbm"], -74)

    def test_sin_wifi(self):
        self.assertEqual(self._leer("No hay ninguna interfaz inalámbrica."), {})


class Capacidad(unittest.TestCase):
    def test_se_deduce_del_nombre_del_adaptador(self):
        casos = [
            ("Realtek 8852BE Wireless LAN WiFi 6 PCI-E NIC", "802.11ax"),
            ("Intel(R) Wi-Fi 6 AX201 160MHz", "802.11ax"),
            ("Intel(R) Wi-Fi 7 BE200 320MHz", "802.11be"),
            ("Intel(R) Dual Band Wireless-AC 8265", "802.11ac"),
            ("Realtek PCIe GbE Family Controller", None),
        ]
        for nombre, esperado in casos:
            with self.subTest(nombre=nombre):
                self.assertEqual(wifi_capability(nombre), esperado)

    def test_los_techos_van_en_orden(self):
        orden = ["802.11n", "802.11ac", "802.11ax", "802.11be"]
        valores = [WIFI_TECHO[g] for g in orden]
        self.assertEqual(valores, sorted(valores))


class Hallazgos(FixtureCase):
    def _auditar(self, red: dict):
        a = self.auditor()
        a.network = red
        return a, a.check_network_link()

    def _wifi(self, descripcion, radio, **extra):
        base = {"radio": radio, "rate_mbps": 866.7, "band_ghz": "5", "channel": 40,
                "rssi_dbm": -50, "signal_pct": 100}
        base.update(extra)
        adaptador = {"name": "Wi-Fi", "description": descripcion, "status": "Up",
                     "link_mbps": base["rate_mbps"], "media": "Native 802.11",
                     "wireless": True}
        return {"adapters": [adaptador], "connected": [adaptador], "wifi": base}

    def test_wifi6_conectada_en_ac(self):
        a, resumen = self._auditar(
            self._wifi("Realtek 8852BE Wireless LAN WiFi 6 PCI-E NIC", "802.11ac"))
        hallazgo = next(f for f in a.findings if f.id == "wifi_downgrade")
        self.assertIn("802.11ax", hallazgo.title)
        self.assertIn("802.11ac", hallazgo.title)

    def test_wifi6_conectada_en_ax_no_genera_nada(self):
        a, _ = self._auditar(
            self._wifi("Intel(R) Wi-Fi 6 AX201 160MHz", "802.11ax"))
        self.assertEqual([f.id for f in a.findings], [])

    def test_senal_debil(self):
        a, _ = self._auditar(self._wifi("Intel(R) Wi-Fi 6 AX201", "802.11ax", rssi_dbm=-78))
        self.assertTrue([f for f in a.findings if f.id == "wifi_signal"])

    def test_banda_de_24_con_tarjeta_capaz(self):
        a, _ = self._auditar(self._wifi("Intel(R) Wi-Fi 6 AX201", "802.11ax", band_ghz="2.4"))
        self.assertTrue([f for f in a.findings if f.id == "wifi_banda"])

    def test_gigabit_a_100(self):
        adaptador = {"name": "Ethernet", "description": "Realtek PCIe GbE Family Controller",
                     "status": "Up", "link_mbps": 100.0, "media": "802.3", "wireless": False}
        a, _ = self._auditar({"adapters": [adaptador], "connected": [adaptador], "wifi": {}})
        hallazgo = next(f for f in a.findings if f.id == "ethernet_lento")
        self.assertEqual(hallazgo.severity, "high")
        self.assertIn("cable", hallazgo.detail.lower())

    def test_gigabit_a_1000_no_genera_nada(self):
        adaptador = {"name": "Ethernet", "description": "Realtek PCIe GbE Family Controller",
                     "status": "Up", "link_mbps": 1000.0, "media": "802.3", "wireless": False}
        a, _ = self._auditar({"adapters": [adaptador], "connected": [adaptador], "wifi": {}})
        self.assertEqual(a.findings, [])

    def test_sin_adaptadores_no_se_opina(self):
        a = self.auditor()
        a.network = {"adapters": [], "connected": [], "wifi": {}}
        with self.assertRaises(audit.SinDato):
            a.check_network_link()


class SondasActivas(FixtureCase):
    def test_sin_pedirlas_no_aplica(self):
        a = self.auditor()
        a.network = {"active": False}
        with self.assertRaises(audit.NoAplica):
            a.check_network_latency()

    def test_pedidas_y_sin_respuesta_no_es_un_aprobado(self):
        a = self.auditor()
        a.network = {"active": True, "latency": {"reachable": False, "targets": []}}
        with self.assertRaises(audit.SinDato):
            a.check_network_latency()

    def test_dns_lento(self):
        a = self.auditor()
        a.network = {"active": True,
                     "latency": {"reachable": True, "best_ms": 12.0,
                                 "targets": [{"name": "X", "median_ms": 12.0,
                                              "loss_pct": 0, "jitter_ms": 1.0}]},
                     "dns": {"median_ms": 260.0, "failures": 0, "queried": 3}}
        a.check_network_latency()
        self.assertTrue([f for f in a.findings if f.id == "dns_lento"])

    def test_perdida_de_paquetes(self):
        a = self.auditor()
        a.network = {"active": True,
                     "latency": {"reachable": True, "best_ms": 20.0,
                                 "targets": [{"name": "X", "median_ms": 20.0,
                                              "loss_pct": 50, "jitter_ms": None}]},
                     "dns": {}}
        a.check_network_latency()
        self.assertTrue([f for f in a.findings if f.id == "red_perdida"])


class Privacidad(unittest.TestCase):
    """Estos informes se comparten. Hay datos que no pueden salir de aquí."""

    def test_el_enlace_no_recoge_identificadores(self):
        import quilate.network as red
        original = red.run_cmd_bytes, red.IS_WINDOWS
        red.run_cmd_bytes = lambda *a, **k: NETSH_ES.encode("utf-8")
        red.IS_WINDOWS = True
        try:
            datos = wifi_link()
        finally:
            red.run_cmd_bytes, red.IS_WINDOWS = original
        volcado = json.dumps(datos).lower()
        for prohibido in ("ssid", "bssid", "red-de-prueba", "00:00:00"):
            self.assertNotIn(prohibido, volcado, f"se ha colado «{prohibido}»")

    def test_los_adaptadores_no_recogen_la_mac(self):
        import quilate.network as red
        original = red.ps_json, red.IS_WINDOWS
        red.IS_WINDOWS = True
        red.ps_json = lambda *a, **k: [
            {"Name": "Wi-Fi", "InterfaceDescription": "Tarjeta", "Status": "Up",
             "LinkSpeed": "866.7 Mbps", "MediaType": "Native 802.11",
             "MacAddress": "AA-BB-CC-DD-EE-FF"}]
        try:
            filas = adapters()
        finally:
            red.ps_json, red.IS_WINDOWS = original
        self.assertNotIn("aa-bb-cc", json.dumps(filas).lower())

    def test_con_no_net_no_se_contacta_con_nadie(self):
        # Las sondas van activadas por defecto, pero --no-net tiene que cortar
        # de verdad la salida a internet, no solo ocultar el resultado.
        import quilate.network as red
        original = red.latency_probe, red.dns_probe

        def prohibido(*a, **k):
            raise AssertionError("se ha sondeado la red con --no-net")

        red.latency_probe = red.dns_probe = prohibido
        try:
            datos = collect(active=False)
        finally:
            red.latency_probe, red.dns_probe = original
        self.assertNotIn("latency", datos)
        self.assertFalse(datos["active"])

    def test_la_inspeccion_del_enlace_nunca_sale_fuera(self):
        # Leer el adaptador y el enlace es local: eso no depende de ningún flag.
        import quilate.network as red
        original = red.tcp_latency
        red.tcp_latency = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("la inspección local ha abierto una conexión"))
        try:
            red.wifi_link()
        finally:
            red.tcp_latency = original


if __name__ == "__main__":
    unittest.main()
