"""Si la copia que se está ejecutando es la última publicada.

Quilate se distribuye como un `.exe` suelto que se copia a un pendrive y se
ejecuta meses después: no hay gestor de paquetes que avise, ni instalador que
se actualice solo. Sin esto, la única forma de enterarse de que hay una versión
nueva es acordarse de mirar la página de releases, y nadie se acuerda.

**Por qué GitHub Releases y no PyPI.** Quilate no se publica en PyPI a
propósito: `pyproject.toml` declara que no lleva `[project]` ni
`[build-system]` porque no se instala como paquete. Lo que sí existe es una
release por etiqueta `v*`, y `.github/workflows/release.yml` falla la
publicación si la etiqueta no coincide con `APP_VERSION`. Es decir, **la
etiqueta ya está atada al código**, y eso convierte a `releases/latest` en la
respuesta exacta a la pregunta «¿hay algo más nuevo que lo que tengo?». Un
índice de paquetes en el que el proyecto no está solo podría contestar que no.

Tres reglas que gobiernan todo lo de aquí, en orden de importancia:

1. **Esto nunca puede estropear una ejecución.** El análisis ya está hecho
   cuando se pregunta, y una release que no contesta no puede costar un
   informe. Todo error se traga y se devuelve `None`; el timeout es de tres
   segundos y no se reintenta.
2. **No se pregunta en cada ejecución.** La respuesta se guarda con su fecha al
   lado del histórico y vale un día. El fallo también se guarda: sin eso, un
   equipo sin conexión pagaría los tres segundos del timeout en cada arranque,
   para siempre.
3. **`--no-net` corta esta comprobación.** Ver `comprobar`, que no puede llegar
   a la red sin que quien llama lo autorice.

   Hasta la 2.7.0 aquí ponía que la bandera «promete que no se abre ninguna
   conexión, y esa promesa no admite excepciones para el fabricante». **Desde
   la 2.8.0 eso ya no es cierto** y no se deja escrito como si lo fuera: la
   telemetría se envía también con `--no-net`. La bandera sigue mandando sobre
   lo que hay en este módulo y sobre las sondas de latencia, pero ha dejado de
   significar «no sale nada». Quien lea esto buscando la garantía antigua tiene
   que encontrar aquí que ya no existe, no tener que deducirlo. Ver `PRIVACY.md`
   y la ayuda de la propia bandera en `cli.py`.

Lo que se envía a GitHub es una petición GET sin cabecera de autenticación, sin
parámetros y sin cuerpo: no viaja nada del equipo salvo lo que cualquier
petición HTTP lleva de serie. Y no se envía la versión instalada, que es lo
único que aquí habría tentación de mandar: la comparación se hace en local.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .const import APP_NAME, APP_VERSION
# El fichero de caché va donde el histórico, y por eso se pide la ruta a
# `history` en vez de recomponerla: ahí ya está resuelto en qué carpeta escribe
# cada sistema y —lo que importa— la comprobación de que esa carpeta es de fiar
# cuando la variable de entorno que la nombra la puede cambiar cualquiera.
# La dependencia va en el sentido de siempre (`const` → `cli`) y no cierra
# ningún ciclo: `history` no sabe que esto existe.
from .history import history_path

# El repositorio del que salen las releases. Va aquí y no en `const` porque es
# lo único de todo el programa que lo necesita: `WEBSITE_URL` es la web del
# autor, que es otra cosa y puede cambiar sin que cambie esto.
REPO = "CrIsTiAnPvP/Quilate-Benchmark"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
DESCARGA_URL = f"https://github.com/{REPO}/releases/latest"

# Tres segundos. No es un valor redondeado a ojo: por debajo de eso una
# conexión móvil lenta daría siempre negativo, y por encima se nota como una
# pausa al final de un informe que ya está impreso.
TIEMPO_LIMITE = 3.0

# Cuánto vale una respuesta antes de volver a preguntar.
VIGENCIA = timedelta(hours=24)

# GitHub rechaza las peticiones sin User-Agent con un 403, así que hay que
# mandar uno. Se identifica como lo que es —no se disfraza de navegador— y no
# lleva la versión instalada: para saber si hay algo más nuevo no hace falta
# decirle a nadie qué se tiene.
CABECERAS = {
    "User-Agent": APP_NAME.replace(" ", "-"),
    "Accept": "application/vnd.github+json",
}

_NUMERO = re.compile(r"\d+")


def cache_path() -> Path:
    """Fichero donde se recuerda la última respuesta.

    Al lado del histórico, en la carpeta que cada sistema reserva para datos de
    aplicación. Borrarlo no rompe nada: la siguiente ejecución vuelve a
    preguntar.
    """
    return history_path().with_name("version_check.json")


def version_tuple(texto: Any) -> tuple:
    """La versión como tupla de enteros comparable, o `()` si no lo parece.

    Acepta la forma en que GitHub nombra las etiquetas (`v2.7.0`) y la que
    declara `const.py` (`2.7.0`). Lo que devuelve para una cadena que no es una
    versión es una tupla vacía, y eso importa: `()` es menor que cualquier otra
    tupla, así que una etiqueta rara nunca se puede leer como «hay algo más
    nuevo».
    """
    numeros = _NUMERO.findall(str(texto or "").strip())
    return tuple(int(n) for n in numeros[:4])


def hay_novedad(publicada: Any, instalada: Any = APP_VERSION) -> bool:
    """Si la publicada es estrictamente más nueva que la instalada.

    Se compara por tuplas y no por texto: `"2.10.0" > "2.9.0"` es falso en
    orden alfabético, y esa comparación acabaría callando justo la versión que
    había que anunciar. Sin versión publicada legible no hay novedad que
    anunciar, que es la respuesta prudente.
    """
    izquierda, derecha = version_tuple(publicada), version_tuple(instalada)
    return bool(izquierda) and izquierda > derecha


def _leer_cache(destino: Path) -> dict:
    """La última respuesta guardada, o `{}` si no hay o no se entiende.

    Un fichero corrupto —un corte de luz a media escritura— se trata como si no
    existiera. No se avisa de ello: es una caché, y lo peor que puede pasar es
    preguntar otra vez.
    """
    try:
        datos = json.loads(destino.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _guardar_cache(destino: Path, entrada: dict) -> None:
    """Anota la respuesta con su fecha. Si no se puede, no pasa nada.

    Se guarda **también cuando la consulta ha fallado**, con `latest` a `None`.
    Sin eso, un equipo sin conexión vuelve a intentarlo en cada ejecución y paga
    el timeout entero cada vez; con eso, lo intenta una vez al día.
    """
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(entrada, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass


def _vigente(entrada: dict, ahora: datetime) -> bool:
    marca = entrada.get("checked_at")
    if not isinstance(marca, str):
        return False
    try:
        cuando = datetime.fromisoformat(marca)
    except ValueError:
        return False
    # El futuro cuenta como caducado: un reloj que se adelantó una vez dejaría
    # la caché válida durante años si no.
    return ahora - VIGENCIA <= cuando <= ahora


def consultar(url: str = API_URL, timeout: float = TIEMPO_LIMITE) -> tuple:
    """Pregunta a GitHub por la última release. Devuelve `(version, error)`.

    Es la única función del módulo que abre una conexión, y no la llama nadie
    salvo `comprobar` con permiso explícito. Se recorta la respuesta a 64 KB
    porque un JSON de release ronda los 3 KB y no hay ninguna razón para leer
    más de lo que cabe en la memoria de un `.exe` de siete megas.
    """
    peticion = urllib.request.Request(url, headers=CABECERAS, method="GET")
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            crudo = respuesta.read(64 * 1024)
        datos = json.loads(crudo.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return None, f"GitHub ha respondido {exc.code}"
    except urllib.error.URLError as exc:
        return None, f"no se ha podido contactar con GitHub: {exc.reason}"
    except (OSError, ValueError) as exc:
        # OSError cubre el timeout del socket, que no siempre llega envuelto en
        # URLError; ValueError, un JSON que no lo es.
        return None, f"no se ha podido leer la respuesta: {type(exc).__name__}"
    if not isinstance(datos, dict):
        return None, "la respuesta de GitHub no tiene la forma esperada"
    etiqueta = datos.get("tag_name") or datos.get("name")
    if not version_tuple(etiqueta):
        return None, "la release publicada no declara una versión legible"
    return str(etiqueta).lstrip("vV"), None


def comprobar(permitido: bool, destino: Path | None = None,
              ahora: datetime | None = None, consulta=consultar) -> dict:
    """Estado de la versión, mirando la caché y —si se permite— la red.

    `permitido` no tiene valor por defecto a propósito. Quien llame tiene que
    decidir explícitamente si esta ejecución puede salir a internet, y así no
    hay forma de abrir una conexión por olvidarse de un parámetro. Con
    `permitido=False` se sigue leyendo la caché: una respuesta que ya está
    guardada en el disco no cuesta ninguna conexión, y callarla sería esconder
    un dato que ya se tiene.

    Devuelve siempre el mismo diccionario, y nunca lanza: `latest` es la última
    versión conocida o `None`, `outdated` dice si hay que avisar, y `source`
    distingue de dónde salió la respuesta —«red», «caché» o `None`— para que el
    informe pueda decir la verdad sobre su propia frescura.
    """
    ahora = ahora or datetime.now()
    fichero = destino or cache_path()
    entrada = _leer_cache(fichero)
    estado = {
        "current": APP_VERSION,
        "latest": None,
        "outdated": False,
        "checked_at": entrada.get("checked_at"),
        "source": None,
        "error": None,
        "url": DESCARGA_URL,
    }

    if _vigente(entrada, ahora):
        estado["latest"] = entrada.get("latest")
        estado["error"] = entrada.get("error")
        estado["source"] = "caché"
    elif not permitido:
        # Ni caché fresca ni permiso: no hay nada que decir, y sobre todo no hay
        # ninguna conexión que abrir.
        estado["error"] = "no se ha consultado: sin permiso para usar la red"
        # La caché caducada sí se conserva como último dato conocido. Es de
        # ayer, y se dice que lo es.
        if entrada.get("latest"):
            estado["latest"] = entrada.get("latest")
            estado["source"] = "caché caducada"
    else:
        version, error = consulta()
        estado["latest"], estado["error"] = version, error
        estado["checked_at"] = ahora.isoformat(timespec="seconds")
        estado["source"] = "red"
        _guardar_cache(fichero, {"checked_at": estado["checked_at"],
                                 "latest": version, "error": error})

    estado["outdated"] = hay_novedad(estado["latest"])
    return estado


def linea_de_aviso(estado: dict) -> str | None:
    """El texto de una sola línea, o `None` si no hay nada que anunciar.

    Una versión nueva no es un hallazgo de la auditoría ni un problema del
    equipo, así que no le corresponde una sección propia ni un color de
    severidad: es una nota al pie. Y si no hay novedad no se escribe nada, ni
    siquiera un «estás al día» — el informe ya es largo.
    """
    if not estado.get("outdated") or not estado.get("latest"):
        return None
    texto = (f"Hay una versión más reciente: {estado['latest']} "
             f"(tienes {estado.get('current') or APP_VERSION}) · {estado['url']}")
    if estado.get("source") == "caché caducada":
        texto += "  [dato guardado; no se ha consultado ahora]"
    return texto
