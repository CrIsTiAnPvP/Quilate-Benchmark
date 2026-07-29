"""Las piezas sueltas con las que se construye el informe.

Constantes de presentacion —iconos, etiquetas, glosario— y las primitivas
que las usan: escapado, iconos, barras, medidores. Nada de aqui sabe que es
un hallazgo ni que es un disco: reciben texto y cifras y devuelven HTML.

`Seccion` esta aqui y no en el `__init__` a proposito. Es una estructura de
datos sin dependencias, y `bloques` la nombra en una anotacion: dejarla
arriba obligaba a que el nivel de abajo importara del de arriba, es decir un
ciclo que hoy solo se sostiene porque las anotaciones estan aplazadas.

Aqui vive tambien el unico estado compartido del paquete, la memoria del
glosario, y por eso se toca con `reiniciar_glosario()` y no manipulando el
`set` desde fuera: ver el comentario de `_TERMINOS_VISTOS`.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from html import escape
from typing import Any


SEVERITY_LABELS = {"critical": "críticos", "high": "altos", "medium": "medios",
                   "low": "bajos", "info": "info"}

# Iconos: trazos de 24x24 que heredan el color del texto. Se declaran una vez en
# un sprite y se referencian con <use>: así no se repite el path en cada uso.
ICONS = {
    "i-zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "i-box": '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 '
             '1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>'
             '<polyline points="3.27 6.96 12 12.01 20.73 6.96"/>'
             '<line x1="12" y1="22.08" x2="12" y2="12"/>',
    "i-chart": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/>'
               '<line x1="6" y1="20" x2="6" y2="14"/>',
    "i-cpu": '<rect x="4" y="4" width="16" height="16" rx="2"/>'
             '<rect x="9" y="9" width="6" height="6"/>'
             '<line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/>'
             '<line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/>'
             '<line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/>'
             '<line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
    "i-ram": '<rect x="2" y="4" width="20" height="9" rx="2"/>'
             '<line x1="2" y1="17" x2="22" y2="17"/><line x1="6" y1="17" x2="6" y2="20"/>'
             '<line x1="12" y1="17" x2="12" y2="20"/><line x1="18" y1="17" x2="18" y2="20"/>',
    "i-disk": '<line x1="22" y1="12" x2="2" y2="12"/>'
              '<path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 '
              '16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'
              '<line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/>',
    "i-gpu": '<rect x="2" y="3" width="20" height="14" rx="2"/>'
             '<line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
    "i-sys": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>'
             '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>'
             '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>'
             '<line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/>'
             '<line x1="17" y1="16" x2="23" y2="16"/>',
    "i-trend": '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/>'
               '<polyline points="17 6 23 6 23 12"/>',
    "i-list": '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/>'
              '<line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/>'
              '<line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    "i-alert": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 '
               '2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/>'
               '<line x1="12" y1="17" x2="12.01" y2="17"/>',
    "i-award": '<circle cx="12" cy="8" r="7"/>'
               '<polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.87"/>',
    "i-chev": '<polyline points="9 18 15 12 9 6"/>',
    "i-up": '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/>',
    "i-clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "i-wrench": '<path d="M14.7 6.3a4 4 0 0 0 5 5l-9.4 9.4a2.1 2.1 0 0 1-3-3z"/>'
                '<path d="M14.7 6.3 18 3l3 3-3.3 3.3"/>',
    "i-folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 '
                '2 2z"/>',
    "i-gauge": '<path d="M12 21a9 9 0 1 1 9-9"/><line x1="12" y1="12" x2="17" y2="8"/>'
               '<circle cx="12" cy="12" r="1.6"/>',
    "i-download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                  '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "i-shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    # Ondas de wifi: la red se representa por el enlace, que es lo que se mide.
    "i-net": '<path d="M2 8.5a16 16 0 0 1 20 0"/><path d="M5.5 12a11 11 0 0 1 13 0"/>'
             '<path d="M9 15.5a6 6 0 0 1 6 0"/><circle cx="12" cy="19.5" r="1"/>',
    "i-search": '<circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="21" y2="21"/>',
    "i-x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "i-check": '<polyline points="20 6 9 17 4 12"/>',
    "i-help": '<circle cx="12" cy="12" r="10"/>'
              '<path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.6-3 4"/>'
              '<line x1="12" y1="17.5" x2="12.01" y2="17.5"/>',
    "i-layers": '<polygon points="12 2 22 8 12 14 2 8 12 2"/>'
                '<polyline points="2 16 12 22 22 16"/>',
}

COMPONENT_ICONS = {"cpu": "i-cpu", "memory": "i-ram", "disk": "i-disk",
                   "gpu": "i-gpu", "network": "i-net", "system": "i-sys"}

# La barra de navegación se queda sin sitio con los títulos completos y acaba
# recortando las últimas entradas: ahí van estos nombres cortos.
NAV_LABELS = {"componentes": "Componentes", "proyeccion": "Proyección",
              "plan": "Plan de acción", "hallazgos": "Hallazgos"}

# Para qué sirve cada sección, que es lo que decide en qué orden se leen:
#   accion       lo que hay que decidir y aplicar
#   diagnostico  lo que explica por qué sale esa nota
#   referencia   inventario y escalas, se consultan pero no se actúa sobre ellas
#   marca        la conclusión, en el color de la casa
# Sin esta distinción, doce secciones idénticas compiten todas por igual.
TONOS = {"plan": "accion", "hallazgos": "accion", "veredicto": "marca",
         "seguridad": "accion", "inventario": "referencia"}

# Grupos para los presets de exportación de la bandeja.
GRUPOS = {"plan": "accion", "hallazgos": "accion", "veredicto": "accion",
          "seguridad": "accion", "proyeccion": "accion tecnico"}

# Términos que el informe usa y que no todo el mundo tiene por qué saber. Se
# explican donde aparecen por primera vez, no en un anexo que nadie abre.
GLOSARIO = {
    "margen": ("Cuánto varió una prueba consigo misma al repetirla o al partir el "
               "mismo trabajo en tramos. Un número solo nunca delata que está "
               "contaminado; con margen, sí. Por encima de ±25% la cifra vale como "
               "orden de magnitud, pero no para comparar con otra ejecución."),
    "referencia": ("100 puntos equivalen a un equipo de gama media reciente, con la "
                   "fecha en que se fijó esa equivalencia. Por encima de 100 el "
                   "equipo va mejor que esa referencia; por debajo, peor."),
    "sostenido": ("El trabajo hecho en el último cuarto de una carga larga frente al "
                  "hecho en el primero. Por debajo del 90% el equipo se está "
                  "limitando solo por temperatura o por potencia."),
    "cobertura": ("Cuántas comprobaciones llegaron a un veredicto. Las que no, no "
                  "significan «correcto»: significan que no había dato con el que "
                  "opinar, y se listan con el motivo."),
    "carga-ajena": ("CPU que consumían otros programas con el benchmark parado. Si es "
                    "alta, no se está midiendo el equipo: se está midiendo el equipo "
                    "mientras hace otra cosa."),
    "opencl": ("La biblioteca con la que se pone a calcular la gráfica. La instala el "
               "propio driver, así que no hay nada que instalar aparte, y funciona "
               "igual con NVIDIA, AMD e Intel."),
}

# Términos ya explicados en este documento. Se vacía al empezar cada informe:
# repetir el mismo globo diez veces convierte una ayuda en ruido.
#
# Es el único estado compartido del módulo, y se toca desde fuera con
# `reiniciar_glosario()` y no manipulando este nombre. La diferencia importa
# cuando el módulo se reparte en varios ficheros: `.clear()` muta el objeto y
# funcionaría aunque cada fichero importara el nombre por su cuenta, pero el día
# que alguien lo cambiara por `= set()` estaría reasignando solo su copia local,
# el glosario dejaría de vaciarse y no fallaría nada — el segundo informe de la
# sesión saldría sin una sola explicación y el fichero se generaría igual. Con
# una función no hay nombre que reasignar desde fuera, así que ese fallo deja de
# ser posible en vez de ser improbable.
_TERMINOS_VISTOS: set[str] = set()


def reiniciar_glosario() -> None:
    """Olvida los términos ya explicados. Se llama al empezar cada informe."""
    _TERMINOS_VISTOS.clear()


def _e(value: Any) -> str:
    """Escapa cualquier valor. Los nombres de dispositivo vienen de WMI y pueden
    traer &, < o > que romperían el marcado."""
    return escape(str(value), quote=True)


def _icon(name: str, cls: str = "ic") -> str:
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true"><use href="#{name}"/></svg>'


def _term(texto: str, clave: str) -> str:
    """Marca un término del glosario con su explicación emergente.

    Solo la primera aparición lleva globo: repetir el mismo aviso en cada tabla
    convierte una ayuda en ruido, y a la tercera ya nadie la lee. Las siguientes
    salen como texto normal.

    Va siempre en prosa, nunca dentro de un `.tw`: ese contenedor tiene
    `overflow-x` para las tablas anchas y recortaría el globo.
    """
    if clave in _TERMINOS_VISTOS or clave not in GLOSARIO:
        return _e(texto)
    _TERMINOS_VISTOS.add(clave)
    return (f'<span class="term" tabindex="0" role="note">{_e(texto)}'
            f'{_icon("i-help", "ic sm")}'
            f'<span class="tip">{_e(GLOSARIO[clave])}</span></span>')


def _logo(cls: str = "brandmark", uid: str = "a") -> str:
    """Isotipo de Quilate, mismo trazado que quilate.svg.

    Va inline y completo en cada sitio donde aparece, no como <use> del sprite:
    el sprite lleva display:none y ahí Chromium no resuelve el degradado ni la
    máscara —los iconos de trazo sí salen porque no referencian nada—, así que
    el logotipo quedaba invisible. El sufijo evita que dos copias compartan id.

    El `maskUnits` explícito no es decorativo: sin él la región de la máscara es
    la caja del círculo ampliada un 10%, medida SIN el grosor del trazo, y el
    anillo salía achatado arriba y a la izquierda en vez de redondo.
    """
    oro, muesca = f"ql-oro-{uid}", f"ql-muesca-{uid}"
    return (
        f'<svg class="{cls}" viewBox="0 0 100 100" aria-hidden="true">'
        f'<defs><linearGradient id="{oro}" x1="20" y1="10" x2="84" y2="94" '
        'gradientUnits="userSpaceOnUse">'
        '<stop offset="0" stop-color="#ffeeb0"/><stop offset=".34" stop-color="#f8d156"/>'
        '<stop offset=".70" stop-color="#e9ab1e"/><stop offset="1" stop-color="#b87c0b"/>'
        "</linearGradient>"
        f'<mask id="{muesca}" maskUnits="userSpaceOnUse" x="0" y="0" width="100" '
        'height="100"><rect width="100" height="100" fill="#fff"/>'
        '<rect x="56.5" y="46" width="21" height="52" fill="#000" '
        'transform="rotate(-45 67 72)"/></mask></defs>'
        f'<circle cx="48" cy="46" r="30" fill="none" stroke="url(#{oro})" stroke-width="14" '
        f'mask="url(#{muesca})"/>'
        f'<rect x="60.5" y="50.8" width="13" height="42.4" rx="1" fill="url(#{oro})" '
        'transform="rotate(-45 67 72)"/></svg>'
    )


def _favicon() -> str:
    """El isotipo como icono de pestaña, incrustado en la propia URL.

    Sin esto haría falta el .ico al lado del fichero, y el informe dejaría de ser
    un único documento que se puede enviar por correo. Va en `data:` con el SVG
    escrito a mano: `#` tiene que ir escapado o el navegador lee el resto de la
    URL como un ancla y no pinta nada.
    """
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
        "<defs><linearGradient id='g' x1='20' y1='10' x2='84' y2='94' "
        "gradientUnits='userSpaceOnUse'>"
        "<stop offset='0' stop-color='%23ffeeb0'/><stop offset='.34' stop-color='%23f8d156'/>"
        "<stop offset='.70' stop-color='%23e9ab1e'/><stop offset='1' stop-color='%23b87c0b'/>"
        "</linearGradient>"
        "<mask id='m' maskUnits='userSpaceOnUse' x='0' y='0' width='100' height='100'>"
        "<rect width='100' height='100' fill='%23fff'/>"
        "<rect x='56.5' y='46' width='21' height='52' fill='%23000' "
        "transform='rotate(-45 67 72)'/></mask></defs>"
        "<circle cx='48' cy='46' r='30' fill='none' stroke='url(%23g)' stroke-width='14' "
        "mask='url(%23m)'/>"
        "<rect x='60.5' y='50.8' width='13' height='42.4' rx='1' fill='url(%23g)' "
        "transform='rotate(-45 67 72)'/></svg>"
    )
    return f'<link rel="icon" href="data:image/svg+xml,{svg.replace(chr(34), chr(39))}">'


# El halo del arco tiene que ser del mismo tono que el arco, y `drop-shadow` no
# entiende `var(--ok)` a través de otra variable: se pasa el color ya resuelto.
_GAUGE_GLOW = {"var(--ok)": "rgba(66,196,107,.55)", "var(--warn)": "rgba(247,146,59,.55)",
               "var(--bad)": "rgba(255,95,95,.55)"}


def _gauge(score: float, letter: str, size: int = 168) -> str:
    """Dial de 270° con la referencia justo arriba.

    La escala llega a 200 puntos, así que los 100 de la referencia caen exactos
    en las doce en punto: la aguja por encima de esa marca significa «mejor que
    un equipo de gama media», y eso se lee sin necesidad de comparar cifras.

    El arco se dibuja con degradado y un halo del mismo tono, y entra animándose
    desde cero. La animación va por `stroke-dashoffset` y no por `dasharray`
    porque el valor final depende de la nota: se pasa en `--dash` y el fotograma
    inicial lo lee de ahí, así que el mismo `@keyframes` sirve para cualquier
    puntuación. Con `prefers-reduced-motion` la hoja de estilo la desactiva.
    """
    trazo = 13
    radio = (size - trazo) / 2 - 4
    centro = size / 2
    circun = 2 * math.pi * radio
    arco = circun * 0.75
    fraccion = max(0.0, min(1.0, score / 200.0))
    color = _score_color(score)
    lleno = arco * fraccion
    uid = f"gg{int(score * 10)}"
    return (
        f'<div class="gauge" style="height:{size}px">'
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'role="img" aria-label="Puntuación global {score:.0f} sobre una referencia '
        f'de 100, nota {_e(letter)}">'
        f'<defs><linearGradient id="{uid}" x1="0" y1="1" x2="1" y2="0">'
        f'<stop offset="0" stop-color="{color}" stop-opacity=".55"/>'
        f'<stop offset="1" stop-color="{color}"/></linearGradient></defs>'
        f'<g transform="rotate(135 {centro} {centro})">'
        f'<circle class="grail" cx="{centro}" cy="{centro}" r="{radio:.2f}" '
        f'stroke-width="{trazo}" stroke-dasharray="{arco:.2f} {circun:.2f}"/>'
        f'<circle class="gval" cx="{centro}" cy="{centro}" r="{radio:.2f}" '
        f'stroke="url(#{uid})" stroke-width="{trazo}" '
        f'stroke-dasharray="{lleno:.2f} {circun:.2f}" '
        f'style="--dash:{lleno:.2f};--glow:{_GAUGE_GLOW.get(color, "rgba(255,255,255,.4)")}"/>'
        f"</g>"
        f'<line class="gtick" x1="{centro}" y1="{centro - radio - trazo / 2 - 5:.1f}" '
        f'x2="{centro}" y2="{centro - radio + trazo / 2 + 2:.1f}"/>'
        f'<text class="gtxt" x="{centro}" y="{centro - radio - trazo / 2 - 9:.1f}">100</text>'
        f"</svg>"
        f'<div class="gin"><div class="gnum" style="color:{color}">{score:.0f}</div>'
        f'<div class="gunit">puntos</div>'
        f'<div class="gletter" style="color:{color}">{_e(letter)}</div></div></div>'
    )


def _sprite() -> str:
    symbols = "".join(f'<symbol id="{k}" viewBox="0 0 24 24">{v}</symbol>'
                      for k, v in ICONS.items())
    return f'<svg id="sprite" style="display:none" aria-hidden="true">{symbols}</svg>'


def _slug(text: str) -> str:
    """Nombre de fichero seguro a partir del título de la sección."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "seccion"


def _score_color(pct: float) -> str:
    return "var(--ok)" if pct >= 85 else "var(--warn)" if pct >= 55 else "var(--bad)"


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} EB"


# Dónde cae la referencia (100 puntos) dentro de la barra. La escala llega hasta
# 192 puntos para que quede recorrido visible por encima de la media: con el
# tope en 100, un equipo de 105 y otro de 190 pintaban exactamente la misma
# barra llena y el gráfico dejaba de aportar justo donde empieza lo interesante.
BAR_REF_PCT = 52.0
BAR_MAX_SCORE = 100.0 / (BAR_REF_PCT / 100.0)


def _html_bar(score: float, ref: bool = True) -> str:
    """Barra de puntuación con la referencia marcada.

    `ref=False` para porcentajes normales (una ganancia estimada, una ocupación),
    donde 100 es el máximo y no una nota media.
    """
    if not ref:
        ancho = max(0.0, min(100.0, score))
        return (f'<div class="track"><div class="clip"><div class="fill" '
                f'style="width:{ancho:.0f}%;background:{_score_color(score)}">'
                f"</div></div></div>")
    ancho = max(0.0, min(100.0, score / BAR_MAX_SCORE * 100))
    return (f'<div class="track ref" title="la marca es la referencia: 100 puntos">'
            f'<div class="clip"><div class="fill" style="width:{ancho:.1f}%;'
            f'background:{_score_color(score)}"></div></div></div>')


@dataclass
class Seccion:
    """Una sección del informe y todo lo que hace falta para colocarla.

    Va en una clase y no en una tupla porque ya son siete campos y la llamada
    posicional se había vuelto ilegible.
    """

    sid: str
    label: str
    icon: str
    inner: str
    count: str = ""
    severity: str = ""          # peor severidad de los hallazgos que trae: pinta
                                # el punto de color de la navegación
    # Cuántos hallazgos hay detrás de esa severidad. NO se renderiza, y es a
    # propósito: las cuatro secciones que lo rellenan cuentan cosas distintas y
    # solapadas —«componentes» recibe TODOS los hallazgos, «red» solo los suyos,
    # «plan» los accionables y «hallazgos» todos otra vez—, así que sumarlas o
    # enseñarlas juntas daría un total inflado. El único recuento total del
    # informe es el del hero, y el panel lateral desglosa ese mismo conjunto por
    # severidad. Queda escrito aquí, y no borrado, para que quien lo encuentre
    # sepa que no pintarlo fue una decisión y no un descuido.
    findings: int = 0

    @property
    def tono(self) -> str:
        return TONOS.get(self.sid, "diagnostico")

    @property
    def grupo(self) -> str:
        return GRUPOS.get(self.sid, "tecnico")
