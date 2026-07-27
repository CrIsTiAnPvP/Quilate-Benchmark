"""Estado del enlace de red y, si se pide, latencia real.

Un «me va lento» acaba siendo el wifi con una frecuencia asombrosa mucho más a
menudo de lo que parece, y Quilate no tenía nada que decir al respecto. Aquí se
aplica el mismo criterio que al resto: interesa **lo que el enlace está
haciendo**, no lo que la tarjeta podría hacer. Una tarjeta Wi-Fi 6 conectada en
802.11ac, o un adaptador gigabit negociando a 100 Mbps por un cable malo, son
exactamente ese caso.

Sobre privacidad: la inspección del enlace es local y no habla con nadie, así
que va siempre. Las sondas de latencia sí abren conexiones a servidores de
terceros, así que son opcionales y hay que pedirlas expresamente. Y de la wifi
NO se recoge el SSID, ni el BSSID, ni la dirección MAC: identifican la red y el
punto de acceso, no dicen nada sobre el rendimiento, y estos informes se
comparten.
"""

from __future__ import annotations

import re
import socket
import statistics
import time

from .const import IS_WINDOWS
from .platform_utils import ps_json, run_cmd_bytes

# Velocidad teórica del enlace por generación de wifi, en Mbps, con la
# configuración habitual de un portátil (2 flujos, 80 MHz). Sirve para ver si el
# enlace está muy por debajo de lo que la tarjeta sabe hacer.
WIFI_TECHO = {"802.11n": 300, "802.11ac": 866, "802.11ax": 1200, "802.11be": 2400}

# Lo que se puede deducir del nombre del adaptador. Los fabricantes lo anuncian
# ahí ("WiFi 6", "AX201", "BE200") porque es su argumento de venta.
WIFI_CAPACIDAD = (
    (r"\bwi-?fi\s*7\b|\bbe\d{3}\b|802\.11be", "802.11be"),
    (r"\bwi-?fi\s*6e?\b|\bax\d{3}\b|8852|8851|802\.11ax", "802.11ax"),
    # «Wireless-AC 8265» separa la generación del modelo, así que buscar
    # «ac8265» pegado no encontraba nada en media gama de tarjetas Intel.
    (r"\bwi-?fi\s*5\b|\bac\d{3,4}\b|wireless[\s-]?ac\b|802\.11ac", "802.11ac"),
)


def _decodificar(crudo: bytes | None) -> str:
    """Texto de un comando de consola sin saber de antemano su codificación.

    `netsh wlan` responde en UTF-8 y `fsutil` en la página OEM. Se prueban las
    dos y gana la que produzca menos caracteres de sustitución: adivinar mal
    aquí es lo que convertía «no está sucio» en «no est  sucio».
    """
    if not crudo:
        return ""
    mejor, peor_fallos = "", None
    for codec in ("utf-8", "cp850", "cp1252"):
        try:
            texto = crudo.decode(codec, errors="replace")
        except LookupError:
            continue
        fallos = texto.count("�")
        if peor_fallos is None or fallos < peor_fallos:
            mejor, peor_fallos = texto, fallos
        if not fallos:
            break
    return mejor


def _numero(texto: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*$", texto.strip())
    return float(m.group(1).replace(",", ".")) if m else None


def wifi_link() -> dict:
    """Enlace wifi actual, leído sin depender del idioma de Windows.

    `netsh` viene traducido, así que en vez de buscar etiquetas se buscan los
    valores: «802.11ac» se escribe igual en todos los idiomas, y las velocidades
    son las únicas líneas con «Mbps».
    """
    if not IS_WINDOWS:
        return {}
    texto = _decodificar(run_cmd_bytes(["netsh", "wlan", "show", "interfaces"], timeout=15))
    if not texto or "802.11" not in texto:
        return {}

    datos: dict = {}
    radios = re.findall(r"802\.11[a-z]{1,2}\b", texto, re.I)
    if radios:
        datos["radio"] = radios[-1].lower()
    tasas = [_numero(l) for l in texto.splitlines() if "mbps" in l.lower()]
    tasas = [t for t in tasas if t]
    if tasas:
        datos["rate_mbps"] = max(tasas)
    banda = re.search(r"(2[.,]4|5|6)\s*GHz", texto, re.I)
    if banda:
        datos["band_ghz"] = banda.group(1).replace(",", ".")
    # `netsh` alinea los valores con muchos espacios, así que entre la etiqueta
    # y el número hay bastante más que unos pocos caracteres. Se limita a la
    # misma línea para no acabar cogiendo el número del campo siguiente.
    canal = re.search(r"(?:[ck]anal|channel)[^\d\n]{0,40}(\d{1,3})", texto, re.I)
    if canal:
        datos["channel"] = int(canal.group(1))
    # Estos dos se buscan dentro de SU línea, no en todo el texto: buscando un
    # «-NN» suelto, el GUID de la interfaz («…-0000-0000-…») entraba primero y
    # la señal salía a 0 dBm. «Rssi» es un acrónimo y no se traduce; para la
    # señal se admiten las dos formas que sí cambian.
    for linea in texto.splitlines():
        if "rssi" in linea.lower():
            rssi = re.search(r"(-\d{1,3})\b", linea)
            if rssi:
                datos["rssi_dbm"] = int(rssi.group(1))
        elif re.search(r"se[ñn]al|signal", linea, re.I):
            señal = re.search(r"(\d{1,3})\s*%", linea)
            if señal:
                datos["signal_pct"] = int(señal.group(1))
    return datos


def adapters() -> list[dict]:
    """Adaptadores físicos y a qué velocidad han negociado.

    No se recoge la MAC: identifica el equipo y no dice nada del rendimiento.
    """
    if not IS_WINDOWS:
        return []
    filas = ps_json("Get-NetAdapter -Physical | Select-Object Name,InterfaceDescription,"
                    "Status,LinkSpeed,MediaType", timeout=20)
    salida = []
    for f in filas:
        velocidad = str(f.get("LinkSpeed") or "")
        mbps = None
        m = re.match(r"([\d.,]+)\s*(\w?)bps", velocidad, re.I)
        if m:
            valor = float(m.group(1).replace(",", "."))
            mbps = {"g": valor * 1000, "m": valor, "k": valor / 1000}.get(
                m.group(2).lower(), valor / 1e6)
        salida.append({
            "name": str(f.get("Name") or ""),
            "description": str(f.get("InterfaceDescription") or ""),
            "status": str(f.get("Status") or ""),
            "link_mbps": mbps,
            "media": str(f.get("MediaType") or ""),
            "wireless": "802.11" in str(f.get("MediaType") or ""),
        })
    return salida


def wifi_capability(descripcion: str) -> str | None:
    """Generación que anuncia el propio nombre del adaptador."""
    texto = (descripcion or "").lower()
    for patron, generacion in WIFI_CAPACIDAD:
        if re.search(patron, texto):
            return generacion
    return None


# ==============================================================================
# Sondas activas: solo bajo petición expresa
# ==============================================================================
# Estas dos funciones abren conexiones a servidores que no son del usuario. No
# se ejecutan salvo que se pidan con --net, y los destinos son resolutores DNS
# públicos y conocidos, no un servicio propio: Quilate no envía nada a ninguna
# parte, solo cronometra el saludo TCP.
DESTINOS = (("1.1.1.1", 53, "Cloudflare"),
            ("8.8.8.8", 53, "Google"),
            ("9.9.9.9", 53, "Quad9"))

DOMINIOS = ("example.com", "wikipedia.org", "github.com")


def tcp_latency(host: str, puerto: int, intentos: int = 4,
                timeout: float = 2.0) -> list[float]:
    """Milisegundos del saludo TCP. Lista vacía si no hubo respuesta."""
    tiempos = []
    for _ in range(intentos):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        t0 = time.perf_counter()
        try:
            s.connect((host, puerto))
            tiempos.append((time.perf_counter() - t0) * 1000)
        except OSError:
            pass
        finally:
            s.close()
    return tiempos


def latency_probe() -> dict:
    """Latencia hasta varios destinos públicos, más la pérdida observada."""
    resultados = []
    for host, puerto, nombre in DESTINOS:
        tiempos = tcp_latency(host, puerto)
        resultados.append({
            "host": host, "name": nombre,
            "median_ms": round(statistics.median(tiempos), 1) if tiempos else None,
            "jitter_ms": (round(max(tiempos) - min(tiempos), 1)
                          if len(tiempos) > 1 else None),
            "loss_pct": round((4 - len(tiempos)) / 4 * 100),
        })
    validos = [r["median_ms"] for r in resultados if r["median_ms"] is not None]
    return {
        "targets": resultados,
        "best_ms": min(validos) if validos else None,
        "reachable": bool(validos),
    }


def dns_probe() -> dict:
    """Cuánto tarda el resolutor configurado. Una resolución lenta se percibe
    como «internet va lento» aunque el ancho de banda esté perfecto."""
    tiempos = []
    fallos = 0
    for dominio in DOMINIOS:
        t0 = time.perf_counter()
        try:
            socket.getaddrinfo(dominio, 80, socket.AF_INET, socket.SOCK_STREAM)
            tiempos.append((time.perf_counter() - t0) * 1000)
        except OSError:
            fallos += 1
    return {
        "median_ms": round(statistics.median(tiempos), 1) if tiempos else None,
        "failures": fallos,
        "queried": len(DOMINIOS),
    }


def collect(active: bool = False) -> dict:
    """Todo lo que se puede saber de la red. `active` abre conexiones fuera."""
    datos: dict = {"adapters": adapters(), "wifi": wifi_link(), "active": active}
    conectados = [a for a in datos["adapters"] if a["status"].lower() in ("up", "conectado")]
    datos["connected"] = conectados
    if active:
        datos["latency"] = latency_probe()
        datos["dns"] = dns_probe()
    return datos
