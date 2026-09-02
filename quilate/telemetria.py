"""El resumen de la ejecución que sí sale del equipo, desde la 2.8.0.

Hasta la 2.7.0 Quilate no enviaba nada, y lo prometía por escrito en cuatro
sitios. Desde la 2.8.0 eso ha dejado de ser cierto y este módulo es el que lo
hace, así que aquí conviene ser preciso y no generoso con uno mismo.

**Qué se envía**: una lista cerrada, la de `construir()` y solo esa. Se
construye campo a campo, nunca filtrando el informe completo. La diferencia
importa: filtrar es una lista negra, y una lista negra falla en silencio el día
que alguien añade un campo nuevo al informe. Enumerar es una lista blanca, y lo
que se le olvide a alguien es *no enviar* un dato, que es el fallo correcto.
Por eso `construir()` no ve nunca el objeto `SystemInfo` entero —que lleva el
nombre del equipo, las rutas de los volúmenes y los números de serie de los
discos— sino que va a buscar las siete cifras concretas que necesita.

**Tres reglas, en orden de importancia:**

1. **Esto nunca puede estropear una ejecución.** El informe ya está impreso
   cuando esto ocurre. Todo error se traga, sin traza y sin aviso: quien vino a
   medir su equipo ya tiene lo que buscaba, y un endpoint caído no es asunto
   suyo. El envío va en un hilo aparte, con tres segundos de timeout y sin
   reintentos.
2. **No se pregunta en cada ejecución cuando falla.** El fallo se anota con su
   fecha y no se reintenta en 24 h. Sin esto, un equipo sin conexión paga los
   tres segundos del timeout en cada arranque, para siempre. Es exactamente la
   lección que `update_check.py` ya tenía aprendida, y se copia de ahí a
   propósito en vez de inventar otro mecanismo.
3. **No hay cola en disco.** Un envío que falla se pierde. Guardar los payloads
   fallidos para mandarlos luego suena responsable y es lo contrario: convierte
   un fichero local en un historial acumulado de todo lo que se ha querido
   enviar, que es justo el artefacto que no se quiere tener que explicar.

**`--no-net` no corta esto.** Es el cambio de significado de la 2.8.0 y no se
disimula: la bandera corta las sondas de latencia y la comprobación de versión,
no el envío. La ayuda de la propia bandera lo dice, y `PRIVACY.md` lo dice.

**El aviso va antes que el primer envío.** No en la misma ejecución: la primera
vez se avisa y no se manda nada, y se manda a partir de la segunda. Es una
ejecución de diferencia y es la que separa «avisaron antes» de «nos enteramos
después».
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .const import (APP_NAME, APP_VERSION, TELEMETRIA_ESQUEMA, TELEMETRIA_URL)
# El estado va donde el histórico, y por eso se pide la ruta a `history` en vez
# de recomponerla: ahí ya está resuelto en qué carpeta escribe cada sistema y
# —lo que importa— la comprobación de que esa carpeta es de fiar cuando la
# variable de entorno que la nombra la puede cambiar cualquiera. Se reutiliza
# también `_resumen`, que ya calcula las cifras de una ejecución para el
# histórico local: son el mismo criterio de qué merece la pena guardar, y
# duplicarlo garantizaría que las dos copias se separaran a la primera de cambio.
from .history import _resumen, history_path

# Tres segundos, el mismo valor y por la misma razón que en `update_check`: por
# debajo, una conexión móvil lenta falla siempre; por encima, se nota como una
# pausa al final de un informe que ya está impreso.
TIEMPO_LIMITE = 3.0

# Cuánto se espera tras un fallo antes de volver a intentarlo.
VIGENCIA_FALLO = timedelta(hours=24)

# Cada cuánto se tira el identificador y se genera otro. Noventa días es de
# sobra para deduplicar —un usuario ajustando su equipo lo hace en días, no en
# meses— y a la vez impide construir el historial de largo plazo de un equipo
# concreto, que es justo lo que no se quiere poder hacer.
ROTACION = timedelta(days=90)

CABECERAS = {
    "User-Agent": APP_NAME.replace(" ", "-"),
    "Content-Type": "application/json",
}

# Un UUID en la forma canónica y nada más. Lo que hay en el fichero lo puede
# haber editado el usuario —está invitado a hacerlo por `PRIVACY.md`— así que se
# valida antes de mandarlo: sin esto, cualquier cosa que alguien escribiera en
# ese fichero acabaría saliendo del equipo como texto libre, que es
# precisamente lo que la lista negra prohíbe.
_UUID_VALIDO = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                          r"[0-9a-f]{4}-[0-9a-f]{12}$")

# Los identificadores de hallazgo son de un enum cerrado, y `audit/modelo.py` ya
# lo garantiza en el `add` del auditor. Se vuelve a comprobar aquí porque el
# payload puede venir de un JSON de otra versión o editado a mano, y porque el
# día que ese `_ID_VALIDO` se relaje, esto tiene que seguir sin dejar salir
# texto libre. Es la misma expresión a propósito.
_ID_VALIDO = re.compile(r"^[a-z0-9_]+$")

# Cuántos identificadores de hallazgo caben. Una ejecución normal da menos de
# treinta; el tope existe para que un JSON manipulado no convierta el envío en
# algo que el endpoint tenga que rechazar por tamaño.
MAX_HALLAZGOS = 100


# --- Identificador de instalación -------------------------------------------

def estado_path() -> Path:
    """Fichero donde viven el identificador y las dos fechas que hacen falta.

    Al lado del histórico, en la carpeta que cada sistema reserva para datos de
    aplicación. Borrarlo genera un identificador nuevo y hace que el aviso
    vuelva a salir, que es un comportamiento razonable y además el que
    `PRIVACY.md` promete: «puedes verlo, borrarlo o cambiarlo cuando quieras».
    """
    return history_path().with_name("instalacion.json")


def _leer_estado(destino: Path) -> dict:
    """Lo guardado, o `{}` si no hay o no se entiende.

    Un fichero corrupto se trata como si no existiera. Lo peor que puede pasar
    es rotar el identificador antes de tiempo y volver a enseñar el aviso, y
    ninguna de las dos cosas rompe nada.
    """
    try:
        datos = json.loads(destino.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _guardar_estado(destino: Path, estado: dict) -> None:
    """Escribe el estado. Si no se puede, no pasa nada.

    Que no haya dónde escribir no puede costar un análisis que ya está hecho.
    El precio de no poder guardar es rotar el identificador en cada ejecución y
    repetir el aviso, y las dos cosas erran del lado prudente.
    """
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass


def _caducado(creado: Any, ahora: datetime) -> bool:
    """Si toca generar otro identificador.

    Sin fecha legible, sí: no se puede saber cuándo se creó, y dar por bueno un
    identificador de antigüedad desconocida es justo lo que la rotación existe
    para evitar. Una fecha en el futuro también caduca, por lo mismo que en
    `update_check`: un reloj que se adelantó una vez congelaría la rotación
    durante años.
    """
    if not isinstance(creado, str):
        return True
    try:
        cuando = datetime.fromisoformat(creado)
    except ValueError:
        return True
    return not (ahora - ROTACION <= cuando <= ahora)


def install_id(destino: Path | None = None, ahora: datetime | None = None) -> str:
    """El identificador de esta instalación, generándolo o rotándolo si toca.

    Un UUIDv4, que es aleatorio y no deriva de nada del equipo: copiar el `.exe`
    a otro ordenador no lo arrastra, y dos máquinas idénticas tienen
    identificadores distintos. Eso no es un detalle de implementación sino la
    razón de que se pueda enseñar en `PRIVACY.md` sin que sea mentira.

    Devuelve siempre una cadena, incluso si el disco no deja escribir: en ese
    caso sale uno nuevo cada vez, que estropea la deduplicación pero no rompe la
    ejecución, y equivocarse hacia «menos rastreable» es la dirección correcta.
    """
    ahora = ahora or datetime.now()
    fichero = destino or estado_path()
    estado = _leer_estado(fichero)

    actual = estado.get("install_id")
    if isinstance(actual, str) and _UUID_VALIDO.match(actual) \
            and not _caducado(estado.get("created_at"), ahora):
        return actual

    nuevo = str(uuid.uuid4())
    estado["install_id"] = nuevo
    estado["created_at"] = ahora.isoformat(timespec="seconds")
    _guardar_estado(fichero, estado)
    return nuevo


# --- Aviso de primera ejecución ---------------------------------------------

def ya_avisado(destino: Path | None = None) -> bool:
    """Si a este equipo ya se le ha enseñado el aviso alguna vez."""
    return isinstance(_leer_estado(destino or estado_path()).get("notified_at"), str)


def marcar_avisado(destino: Path | None = None, ahora: datetime | None = None) -> None:
    """Anota que el aviso ya se ha enseñado, para no repetirlo cada ejecución."""
    fichero = destino or estado_path()
    estado = _leer_estado(fichero)
    estado["notified_at"] = (ahora or datetime.now()).isoformat(timespec="seconds")
    _guardar_estado(fichero, estado)


# --- Construcción del payload -----------------------------------------------

def _storage_kind(sistema: dict) -> str | None:
    """El tipo de disco de sistema, reducido a las tres categorías que importan.

    `nvme`, `sata_ssd` o `hdd`. Se reduce a propósito en vez de mandar la cadena
    que da Windows: `FriendlyName` lleva el modelo exacto del disco y de ahí a un
    número de serie hay un paso, mientras que para explicar por qué un equipo
    puntúa lo que puntúa la diferencia entre NVMe, SSD SATA y mecánico agota el
    tema. Si no se puede decidir, no se manda el campo: un `"Desconocido"` en el
    servidor no vale más que la ausencia y sí ensucia los recuentos.
    """
    media = str(sistema.get("system_drive_media") or "").upper()
    # «Mixto (SSD, HDD)» es lo que devuelve `_guess_system_media` cuando no ha
    # podido resolver el disco del sistema y ha visto de los dos tipos. Se
    # descarta **antes** de mirar nada más: lleva las dos palabras dentro, así
    # que buscar «HDD» a secas lo clasificaba como mecánico, que es una respuesta
    # inventada con apariencia de dato.
    if "MIXTO" in media:
        return None
    if "HDD" in media:
        return "hdd"
    if "SSD" not in media and "SCM" not in media:
        # «Desconocido», y cualquier cosa que no reconozcamos: sin saber qué
        # disco es el del sistema, cualquier respuesta sería inventada.
        return None
    numero = sistema.get("system_disk_number")
    for disco in sistema.get("physical_disks") or []:
        if isinstance(disco, dict) and disco.get("number") == numero:
            return "nvme" if "NVME" in str(disco.get("bus") or "").upper() else "sata_ssd"
    return "sata_ssd"


def _hallazgos(payload: dict) -> list[str]:
    """Los identificadores de los hallazgos. Nunca los títulos.

    Los títulos llevan valores interpolados —nombres de disco, letras de unidad,
    cuentas de usuario— así que son texto libre y no salen del equipo. Los
    identificadores son de un conjunto cerrado y dicen lo único que aquí
    interesa: qué comprobaciones disparan en el mundo real y cuáles no lo hacen
    nunca. Lo que no encaje en el enum se descarta en silencio, que es lo que
    convierte esta lista en una lista blanca de verdad.
    """
    vistos: list[str] = []
    for hallazgo in payload.get("findings") or []:
        if not isinstance(hallazgo, dict):
            continue
        ident = hallazgo.get("id")
        if isinstance(ident, str) and _ID_VALIDO.match(ident) and ident not in vistos:
            vistos.append(ident)
    return sorted(vistos)[:MAX_HALLAZGOS]


def construir(payload: dict, id_instalacion: str) -> dict[str, Any]:
    """El resumen que se envía, campo a campo y nada más.

    `payload` es el mismo diccionario que `export.build_payload` produce y que el
    histórico ya resume. Se parte de `history._resumen()`, que ya calcula las
    puntuaciones, el arranque, la temperatura y los dos indicadores de calidad
    de la medida sin arrastrar ni una ruta ni un nombre, y se le añade lo que el
    histórico local no necesita: el hardware que permite comparar equipos entre
    sí.

    Lo que sale de `_resumen()` y **no** se reenvía es `at`, la marca de tiempo
    exacta de la ejecución. Al histórico le hace falta para ordenar la serie;
    aquí sería un dato con más resolución de la que ninguna pregunta necesita, y
    el servidor ya sabe cuándo ha recibido algo.

    Los campos sin valor no se mandan. Un `null` y un campo ausente valen lo
    mismo para el servidor, y omitirlos deja el envío más pequeño y más fácil de
    leer entero, que es la única forma de auditar que no se cuela nada.
    """
    resumen = _resumen(payload)
    sistema = payload.get("system") or {}

    cuerpo: dict[str, Any] = {
        "schema": TELEMETRIA_ESQUEMA,
        "app_version": resumen.get("version") or APP_VERSION,
        "install_id": id_instalacion,
        "quick": bool(resumen.get("quick")),
        "finding_ids": _hallazgos(payload),
    }

    # --- Equipo. Modelos comerciales, nunca ejemplares concretos. ---
    version_so = " ".join(str(sistema.get(c) or "").strip()
                          for c in ("os_name", "os_build")).strip()
    if version_so:
        cuerpo["os"] = version_so
    if sistema.get("cpu_name"):
        cuerpo["cpu_model"] = str(sistema["cpu_name"])
    # La primera GPU activa, o la primera a secas. El informe lista todas las que
    # ve —incluidas las virtuales de escritorio remoto— y mandar la lista entera
    # sería un rasgo bastante distintivo de un equipo sin responder a ninguna
    # pregunta mejor de lo que la responde una sola tarjeta.
    tarjetas = [g for g in (sistema.get("gpus") or []) if isinstance(g, dict) and g.get("name")]
    principal = next((g for g in tarjetas if g.get("active")), tarjetas[0] if tarjetas else None)
    if principal:
        cuerpo["gpu_model"] = str(principal["name"])

    ram_total = sistema.get("ram_total")
    if isinstance(ram_total, (int, float)) and ram_total > 0:
        # Redondeado a gigas enteros: la cifra exacta en bytes varía entre
        # equipos con la misma RAM instalada y no explica nada que no explique
        # «32 GB».
        cuerpo["ram_gb"] = round(float(ram_total) / 1024**3)
    # La velocidad **real**, no la nominal. Es la misma distinción que el resto
    # del programa defiende: una RAM de 3600 corriendo a 2133 rinde como 2133.
    if sistema.get("ram_speed_mhz"):
        cuerpo["ram_mts"] = int(sistema["ram_speed_mhz"])
    if sistema.get("ram_channels"):
        cuerpo["ram_channels"] = int(sistema["ram_channels"])

    almacenamiento = _storage_kind(sistema)
    if almacenamiento:
        cuerpo["storage_kind"] = almacenamiento

    # --- Medidas. Ya calculadas por `_resumen`, aquí solo se recolocan. ---
    marcas = {c: resumen[c] for c in ("cpu_single", "cpu_multi", "memory", "disk", "gpu")
              if resumen.get(c) is not None}
    if marcas:
        cuerpo["scores"] = marcas
    for origen, destino in (("overall", "overall"),
                            ("boot_seconds", "boot_seconds"),
                            ("cpu_temp", "cpu_temp_peak"),
                            ("max_spread_pct", "max_spread_pct"),
                            ("busy_pct", "busy_pct")):
        if resumen.get(origen) is not None:
            cuerpo[destino] = resumen[origen]
    return cuerpo


# --- Envío -------------------------------------------------------------------

def _fallo_reciente(estado: dict, ahora: datetime) -> bool:
    """Si el último intento falló hace menos de 24 h.

    Lo que se anota es el fallo y no el éxito: los envíos que funcionan no
    cuestan nada que haya que racionar —son tres segundos como mucho, en un hilo
    aparte, después de que el informe esté impreso— y lo que hay que evitar es
    que un equipo permanentemente sin conexión pague el timeout entero cada vez.
    """
    marca = estado.get("failed_at")
    if not isinstance(marca, str):
        return False
    try:
        cuando = datetime.fromisoformat(marca)
    except ValueError:
        return False
    return ahora - VIGENCIA_FALLO <= cuando <= ahora


def enviar(cuerpo: dict, url: str = TELEMETRIA_URL,
           timeout: float = TIEMPO_LIMITE) -> bool:
    """Un POST con el resumen. `True` si el servidor lo aceptó.

    Sin autenticación, sin cookies y sin leer la respuesta más allá de su
    código: no hay nada que el servidor pueda contestar que a Quilate le importe,
    y no pedirlo evita la tentación de añadir mañana un canal de vuelta.

    No reintenta. Un reintento aquí multiplicaría por dos el coste del caso
    peor —un equipo sin red— sin ganar casi nada: si la primera petición no sale
    en tres segundos, la segunda tampoco.
    """
    datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
    peticion = urllib.request.Request(url, data=datos, headers=CABECERAS, method="POST")
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return 200 <= getattr(respuesta, "status", 0) < 300
    except (urllib.error.URLError, OSError, ValueError):
        # URLError cubre DNS y conexión; OSError, el timeout del socket, que no
        # siempre llega envuelto; ValueError, una URL que no lo es —que es
        # exactamente lo que pasa mientras la constante sea un marcador de
        # posición—. Ninguna de las tres puede llegar a la consola.
        return False


def _enviar_y_anotar(cuerpo: dict, fichero: Path, url: str, timeout: float,
                     ahora: datetime) -> None:
    """El trabajo del hilo: mandar y, si falla, anotar la fecha.

    El `except Exception` de abajo es deliberadamente el más ancho que hay, y no
    es dejadez. Esto corre en un hilo suelto después de que el informe esté
    impreso: una excepción que se escapara de aquí saldría por `stderr` como una
    traza de Python pegada al final de un informe terminado, sin que el usuario
    haya hecho nada mal y sin nada que pueda hacer al respecto. La regla es que
    esto no puede estropear una ejecución, y una traza ya la estropea.
    """
    bien = False
    try:
        bien = enviar(cuerpo, url, timeout)
    except Exception:
        # `enviar` ya criba lo que sabe cribar, pero si se le escapa algo el
        # resultado tiene que ser el mismo que un fallo de red y no un camino
        # aparte: sin esto, una excepción inesperada saltaba por encima del
        # `failed_at` de abajo y el equipo volvía a pagar el timeout entero en
        # cada arranque, que es justo lo que el backoff existe para evitar.
        bien = False
    try:
        estado = _leer_estado(fichero)
        if bien:
            estado.pop("failed_at", None)
        else:
            estado["failed_at"] = ahora.isoformat(timespec="seconds")
        _guardar_estado(fichero, estado)
    except Exception:
        pass


def esperar(hilo: threading.Thread | None,
            timeout: float = TIEMPO_LIMITE + 0.5) -> None:
    """Le da al envío el tiempo justo de terminar antes de que muera el proceso.

    Hace falta porque el hilo es demonio, y un hilo demonio **no se espera**: el
    intérprete lo mata en cuanto el programa acaba. Sin esto, el envío solo
    llegaba a completarse cuando algo posterior mantenía el proceso vivo por
    casualidad —el menú final, que se para a leer una tecla—, y se perdía en
    todas las demás: salida redirigida, tarea programada, o simplemente pasar
    `--json` o `--html`, que es como lo ejecuta cualquiera desde la consola. Con
    el envío moría también el `failed_at`, así que el backoff de 24 h tampoco
    llegaba a anotarse y el equipo lo reintentaba en cada arranque.

    Sigue siendo demonio a propósito, y por eso esto es un `join` con tope y no
    un hilo normal: si alguien cierra la ventana a media espera, el proceso tiene
    que poder morir en ese momento. Lo que se espera es el timeout del socket y
    medio segundo de margen; pasado eso se sigue adelante y el envío se pierde,
    que es lo que ya prometía el módulo.

    Quien paga esta espera de verdad es el equipo sin conexión, y la paga una vez
    al día: a la primera el fallo se anota y el backoff se encarga del resto.
    """
    if hilo is None:
        return
    try:
        hilo.join(timeout)
    except Exception:
        # Ni siquiera esperar puede estropear una ejecución que ya está impresa.
        pass


def programar(payload: dict, destino: Path | None = None, url: str = TELEMETRIA_URL,
              ahora: datetime | None = None, timeout: float = TIEMPO_LIMITE
              ) -> threading.Thread | None:
    """Lanza el envío en segundo plano. Devuelve el hilo, o `None` si no toca.

    Se llama cuando el informe ya está impreso. Devuelve `None` —sin abrir
    ninguna conexión— en dos casos: si a este equipo todavía no se le ha
    enseñado el aviso, y si el último intento falló hace menos de 24 h.

    El hilo va en modo demonio a propósito. Si el usuario cierra la ventana
    mientras los tres segundos del timeout corren, el proceso tiene que poder
    morir en ese momento: un hilo no demonio mantendría a Quilate vivo esperando
    a un servidor que no contesta, después de que su trabajo estuviera hecho.
    Vale más perder un envío que no dejar cerrar el programa.

    Devolver el hilo no lo necesita nadie en producción; lo necesitan los tests,
    que sin él tendrían que dormir para comprobar lo que hace.
    """
    ahora = ahora or datetime.now()
    fichero = destino or estado_path()
    estado = _leer_estado(fichero)

    if not isinstance(estado.get("notified_at"), str):
        # Primera ejecución: se avisa y no se envía. La siguiente ya manda.
        return None
    if _fallo_reciente(estado, ahora):
        return None

    try:
        cuerpo = construir(payload, install_id(fichero, ahora))
    except Exception:
        # Construir el payload no debería fallar nunca, pero lee un diccionario
        # que puede venir de cualquier parte. Si falla, no se manda nada: es
        # preferible perder una medida a mandar algo a medio construir.
        return None

    hilo = threading.Thread(target=_enviar_y_anotar,
                            args=(cuerpo, fichero, url, timeout, ahora),
                            name="quilate-telemetria", daemon=True)
    hilo.start()
    return hilo
