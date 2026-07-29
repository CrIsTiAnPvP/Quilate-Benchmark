"""Exportacion a HTML: informe autocontenido, sin recursos externos.

Todo va embebido (CSS, iconos SVG y un puñado de JS) para que el fichero se
pueda enviar por correo o abrir sin conexion y siga funcionando igual.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from ..audit import Auditor, SEVERITY_ORDER, security_findings
from ..benchmark import (BUSY_CPU_PCT, Benchmark, REFERENCE, REFERENCE_DATE,
                         REFERENCE_MACHINE, REFERENCE_ORIGIN, reference_age_months,
                         reference_is_stale)
from ..sensors import temperature_report, temperature_source
from ..components import (COMPONENT_TO_GROUP, ComponentCard, _no_score_text,
                          build_component_cards, finding_group)
from ..console import COMPONENT_LABELS, grade
from ..const import APP_NAME, APP_VERSION, AUTHOR, WEBSITE, WEBSITE_URL
from ..projection import priority_rank
from ..report import build_verdict
from ..storage_scan import (RECLAIMABLE, REVIEWABLE, ScanResult, candidate_bytes)
from ..sysinfo import KIND_LABELS, SystemInfo, gpu_label, primary_gpu

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
_TERMINOS_VISTOS: set[str] = set()

HTML_CSS = """
/* ==========================================================================
   Quilate · hoja de estilo del informe
   --------------------------------------------------------------------------
   Tres zonas de color que no se mezclan nunca, porque significan cosas
   distintas y mezclarlas es lo que convierte un informe en un adorno:

     · DORADO  (--gold)  la marca y la estructura: isotipo, filetes, veredicto.
                         Nunca califica un dato.
     · CIAN    (--acc)   lo que se puede pulsar: enlaces, navegación, foco.
     · SEMÁFORO(--ok/--warn/--bad)  el diagnóstico, y solo el diagnóstico.

   El ámbar del semáforo tira deliberadamente a naranja: con el dorado de la
   marca al lado, un ámbar amarillo se leía como «esto es de Quilate» en vez de
   como «esto va regular».
   ========================================================================== */
:root{
--ink:#0a0c11;--ink2:#0e1117;--surface:#141821;--surface2:#1a1f2b;
--line:#242b38;--line2:#333d4f;
/* --faint se usa donde más duele: .hint (11,5px), .lbl (10,5px en mayúsculas
   con letter-spacing), .crumb y el placeholder del buscador. Texto pequeño y
   secundario, que es justo el que no puede quedarse corto de contraste. El
   #6d798c anterior daba 4,03:1 sobre --surface, por debajo del 4,5:1 que exige
   WCAG AA; este da 5,66:1 y a simple vista es casi el mismo gris. */
--txt:#e9edf4;--dim:#95a1b5;--faint:#8792a6;

--gold:#e8b33e;--gold-lt:#ffe9a8;--gold-dk:#8a6a1e;
--brand:#e8b33e;--brand-dark:#8a6a1e;      /* nombres antiguos, aún referidos */

--acc:#5cc8ff;--acc-soft:rgba(92,200,255,.13);
--ok:#42c46b;--warn:#f7923b;--bad:#ff5f5f;

--r:15px;--r-s:11px;--r-xs:8px;--pill:999px;
--sh:0 1px 2px rgba(0,0,0,.45),0 8px 24px -14px rgba(0,0,0,.8);
--sh-lg:0 12px 40px -12px rgba(0,0,0,.85),0 2px 8px rgba(0,0,0,.4);
--nav:56px}

*{box-sizing:border-box}
html{scroll-behavior:smooth;background:var(--ink)}
body{margin:0;color:var(--txt);background:transparent;
font:15px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}

/* Luz dorada arriba a la izquierda y fría arriba a la derecha: da profundidad a
   la cabecera sin teñir el cuerpo, donde están las tablas. */
html{background-image:
radial-gradient(1200px 520px at 12% -10%,rgba(232,179,62,.13),transparent 60%),
radial-gradient(1000px 480px at 92% -6%,rgba(92,200,255,.09),transparent 58%);
background-repeat:no-repeat;background-attachment:fixed}

/* Grano. Va incrustado como SVG en la propia URL, así que no rompe la regla de
   fichero único, y con opacidad muy baja: lo justo para que las superficies no
   parezcan plástico, no tanto como para estorbar a una tabla de cifras. */
body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
opacity:.05;mix-blend-mode:overlay;
background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='160' height='160' filter='url(%23n)'/%3E%3C/svg%3E")}
.topbar,.layout,footer,#top,.tray{position:relative;z-index:1}

a{color:var(--acc);text-underline-offset:2px}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px;border-radius:4px}
.vh{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
clip:rect(0 0 0 0);white-space:nowrap;border:0}

/* Las cifras se comparan en vertical por columnas: sin cifras de ancho fijo,
   los millares bailan y dos números del mismo orden parecen distintos. */
table,.gnum,.n,.cs-num,.big,.kvs div,.chead .note,.mm-n{font-variant-numeric:tabular-nums}
.num{font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums}

svg.ic{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:2;
stroke-linecap:round;stroke-linejoin:round}
svg.ic.lg{width:20px;height:20px}
svg.ic.sm{width:13px;height:13px}

/* ---------------------------------------------------------------- topbar -- */
.topbar{position:sticky;top:0;z-index:60;background:rgba(10,12,17,.88);
backdrop-filter:blur(16px) saturate(1.4);border-bottom:1px solid var(--line)}
/* Hilo dorado permanente + barra de progreso de lectura encima de él. */
.topbar::before{content:"";position:absolute;inset:0 0 auto;height:2px;
background:linear-gradient(90deg,transparent,var(--gold-dk) 20%,var(--gold) 50%,
var(--gold-dk) 80%,transparent);opacity:.55}
.prog{position:absolute;top:0;left:0;height:2px;width:0;z-index:2;
background:linear-gradient(90deg,var(--gold-dk),var(--gold) 55%,var(--gold-lt));
box-shadow:0 0 10px rgba(232,179,62,.55)}
/* Altura variable: con muchas secciones la navegación se repartía en una fila
   con scroll horizontal y la barra oculta, así que los últimos enlaces quedaban
   fuera de la vista sin ninguna pista de que seguían ahí. Ahora envuelve.
   OJO: la altura mínima va en píxeles fijos a propósito. Con min-height:var(--nav)
   la barra se dimensionaba con la variable que el JS calcula midiéndola, y como
   offsetHeight incluye el borde inferior cada medida salía un píxel más alta que
   la anterior: la barra crecía sin parar. --nav es solo salida, nunca entrada. */
.tb{max-width:1320px;margin:0 auto;min-height:40px;display:flex;align-items:center;
flex-wrap:wrap;gap:9px 14px;padding:9px 20px}
.tb .logo{font-weight:800;letter-spacing:-.02em;white-space:nowrap;font-size:15px;
display:flex;align-items:center;gap:9px;color:var(--txt);text-decoration:none}
.tb .logo b{color:var(--gold)}
.brandmark{width:24px;height:24px;flex:none;display:block}
.brandmark.foot{width:16px;height:16px;vertical-align:-3px;margin-right:7px}
.brandmark.hero{width:68px;height:68px}
.fbrand{display:flex;align-items:center}

/* Dónde se está leyendo. Va en su propia franja bajo la navegación y no pegado
   al logo: ahí estrujaba la marca contra los enlaces y el ancho le bailaba con
   cada sección. Está siempre presente, aunque sea para decir «Resumen», porque
   una fila que aparece y desaparece cambia la altura de la barra —y con ella la
   variable --nav y el desplazamiento de todos los anclajes— a mitad de scroll. */
.crumb{border-top:1px solid var(--line);background:rgba(255,255,255,.016)}
.crumb .cin{max-width:1320px;margin:0 auto;padding:6px 20px;display:flex;
align-items:center;gap:8px;font-size:11.5px;color:var(--faint)}
.crumb svg{color:var(--faint)}
.crumb b{color:var(--txt);font-weight:600;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.crumb .pct{margin-left:auto;font-variant-numeric:tabular-nums;white-space:nowrap}

.tb nav{display:flex;gap:2px;flex:1 1 380px;flex-wrap:wrap}
.tb nav a{display:flex;align-items:center;gap:6px;padding:6px 9px;border-radius:var(--r-xs);
color:var(--dim);text-decoration:none;font-size:12.5px;white-space:nowrap;
transition:color .15s,background .15s;position:relative}
.tb nav a:hover{color:var(--txt);background:var(--surface)}
.tb nav a.active{color:var(--acc);background:var(--acc-soft);
box-shadow:inset 0 -2px 0 var(--acc)}
/* Punto de severidad en la propia navegación: se ve dónde está lo urgente sin
   entrar a mirar. */
.tb nav a .sev{width:6px;height:6px;border-radius:var(--pill);flex:none}
.sev.s-critical,.sev.s-high{background:var(--bad)}
.sev.s-medium{background:var(--warn)}
.sev.s-low,.sev.s-info{background:var(--acc)}

.find{display:flex;align-items:center;gap:7px;background:var(--surface);
border:1px solid var(--line);border-radius:var(--pill);padding:5px 12px;min-width:186px;
position:relative}
.find svg{color:var(--faint)}
.find input{background:none;border:0;color:var(--txt);font:inherit;font-size:12.5px;
width:100%;min-width:0;outline:none}
.find input::placeholder{color:var(--faint)}
.find.hit{border-color:var(--acc)}
.find .cnt{color:var(--faint);font-size:11px;white-space:nowrap}
/* Un campo de filtro vacío no dice qué se puede filtrar. Al enfocarlo aparecen
   ejemplos sacados de este informe —sus categorías, sus componentes—, no una
   lista genérica que podría no encontrar nada. */
.tips{position:absolute;top:calc(100% + 9px);left:0;z-index:70;background:var(--ink2);
border:1px solid var(--line2);border-radius:var(--r-s);box-shadow:var(--sh-lg);
padding:11px 12px;display:none;width:max-content;max-width:min(360px,82vw)}
.find:focus-within .tips{display:block}
.find.hit .tips{display:none}
.tips .th{font-size:10px;text-transform:uppercase;letter-spacing:.13em;color:var(--faint);
margin-bottom:9px;font-weight:700}
.tips .tr{display:flex;flex-wrap:wrap;gap:6px}
.tips button{background:var(--surface);border:1px solid var(--line);color:var(--dim);
border-radius:var(--pill);padding:3px 11px;font:inherit;font-size:11.5px;cursor:pointer}
.tips button:hover{color:var(--acc);border-color:var(--acc)}

.btn{background:var(--surface);color:var(--dim);border:1px solid var(--line);
border-radius:var(--r-xs);padding:6px 11px;font:inherit;font-size:12px;cursor:pointer;
white-space:nowrap;display:flex;align-items:center;gap:6px;
transition:color .15s,border-color .15s,background .15s}
.btn:hover{color:var(--txt);border-color:var(--acc)}
.btn[disabled]{opacity:.42;cursor:default}
.btn[disabled]:hover{color:var(--dim);border-color:var(--line)}
.btn.gold{border-color:rgba(232,179,62,.42);color:var(--gold)}
.btn.gold:hover{background:rgba(232,179,62,.1);border-color:var(--gold)}

/* --------------------------------------------------------------- layout -- */
.layout{max-width:1320px;margin:0 auto;padding:26px 20px 96px;display:grid;
grid-template-columns:298px minmax(0,1fr);gap:26px;align-items:start}
/* La barra se fija SOLO si cabe entera. Fijarla siempre dejaba el ultimo panel
   fuera de la pantalla hasta llegar al final de la pagina, y darle scroll propio
   cortaba las frases a media palabra. Ninguna de las dos es aceptable, asi que
   en pantallas que no dan de si se desplaza con la pagina y se ve completa, que
   es lo que cualquiera espera de una barra lateral. */
.side{display:flex;flex-direction:column;gap:14px}
@media (min-height:940px){
.side{position:sticky;top:calc(var(--nav) + 18px)}
}
.panel{background:linear-gradient(180deg,var(--surface2),var(--surface));
border:1px solid var(--line);border-radius:var(--r);padding:16px;box-shadow:var(--sh);
position:relative;overflow:hidden}
/* El panel de la nota es el que lleva la marca: filete dorado y el isotipo de
   filigrana al fondo. Es la única cifra que resume todo el informe. */
.panel.mark{border-color:rgba(232,179,62,.24)}
.panel.mark::before{content:"";position:absolute;inset:0 0 auto;height:2px;
background:linear-gradient(90deg,transparent,var(--gold),transparent);opacity:.8}
/* La filigrana se queda fuera del flujo. OJO con el orden: `.panel>*` es mas
   especifico que `.wm` y le ganaba el `position`, asi que el isotipo entraba en
   el flujo como un bloque de 150px y empujaba hacia abajo el titulo y el dial.
   El `:not()` es justo lo que evita esa pelea. */
.panel>*:not(.wm),.verdict>*:not(.wm){position:relative;z-index:1}
.wm{position:absolute;right:-26px;bottom:-30px;width:150px;height:150px;
opacity:.05;pointer-events:none;z-index:0}
.lbl{color:var(--faint);font-size:10.5px;text-transform:uppercase;letter-spacing:.15em;
font-weight:700;margin-bottom:11px}
.panel .sub{color:var(--dim);font-size:12.5px;margin-top:4px}
.panel .delta{margin-top:13px;font-size:12.5px;color:var(--dim);
border-top:1px solid var(--line);padding-top:10px}
.mini{list-style:none;margin:0;padding:0;font-size:12.5px}
.mini li{display:flex;gap:9px;align-items:flex-start;padding:5px 0}
.mini li svg{color:var(--faint);margin-top:4px}
.mini li span{min-width:0;overflow-wrap:anywhere}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.hint{margin-top:12px;font-size:11.5px;color:var(--faint);border-top:1px solid var(--line);
padding-top:10px;line-height:1.5}

/* ---------------------------------------------------------------- dial --- */
.gauge{position:relative;width:100%;display:flex;justify-content:center;margin:16px 0 6px}
.gauge svg{display:block;overflow:visible}
.gauge .grail{stroke:#1e2532;fill:none;stroke-linecap:round}
.gauge .gval{fill:none;stroke-linecap:round;
filter:drop-shadow(0 0 7px var(--glow,rgba(66,196,107,.5)));
animation:gfill 1.05s cubic-bezier(.16,.84,.34,1) both}
@keyframes gfill{from{stroke-dashoffset:var(--dash)}to{stroke-dashoffset:0}}
.gauge .gtick{stroke:var(--txt);stroke-width:2;opacity:.85;stroke-linecap:round}
.gauge .gtxt{fill:var(--faint);font-size:9.5px;font-weight:700;letter-spacing:.1em;
text-anchor:middle}
.gauge .gin{position:absolute;inset:0;display:flex;flex-direction:column;
align-items:center;justify-content:center;pointer-events:none}
.gauge .gnum{font-size:42px;font-weight:800;letter-spacing:-.045em;line-height:1}
.gauge .gunit{font-size:11px;color:var(--faint);letter-spacing:.12em;margin-top:2px;
text-transform:uppercase}
.gauge .gletter{font-size:11.5px;font-weight:800;letter-spacing:.12em;margin-top:7px;
padding:1px 10px;border-radius:var(--pill);border:1px solid currentColor}

/* --------------------------------------------------------- cabecera ------ */
header.page{margin-bottom:22px;display:flex;align-items:center;gap:18px}
header.page .htxt{min-width:0}
header.page .kicker{color:var(--gold);font-size:10.5px;font-weight:700;
letter-spacing:.19em;text-transform:uppercase;margin-bottom:5px}
header.page h1{margin:0 0 6px;font-size:30px;letter-spacing:-.028em;line-height:1.12;
font-weight:800}
header.page .meta{color:var(--dim);font-size:12.5px;overflow-wrap:anywhere}
@media (max-width:560px){header.page .brandmark.hero{display:none}}

/* ------------------------------------------------------------- hero ------ */
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(172px,1fr));gap:14px;
margin-bottom:16px;scroll-margin-top:calc(var(--nav) + 14px)}
.hero .box{background:linear-gradient(180deg,var(--surface2),var(--surface));
border:1px solid var(--line);border-radius:var(--r);padding:18px;box-shadow:var(--sh);
position:relative;overflow:hidden}
/* Filo de color arriba de cada dato de cabecera: el mismo semáforo que la
   cifra, legible de un vistazo y sin repetir el número. */
.hero .box::before{content:"";position:absolute;inset:0 0 auto;height:3px;
background:var(--edge,var(--line2))}
.hero .n{font-size:37px;font-weight:800;letter-spacing:-.045em;line-height:1.08}
.hero .l{color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.11em;
margin-top:9px;line-height:1.45}
.hero .box .foot{color:var(--faint);font-size:11.5px;margin-top:9px;
border-top:1px solid var(--line);padding-top:9px;line-height:1.5}

/* ----------------------------------------------- tira por componente ----- */
.strip{background:linear-gradient(180deg,var(--surface2),var(--surface));
border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;margin-bottom:22px;
box-shadow:var(--sh);position:relative;overflow:hidden}
.strip::before{content:"";position:absolute;inset:0 0 auto;height:2px;
background:linear-gradient(90deg,var(--gold-dk),transparent 55%);opacity:.65}
.cs-row{display:grid;grid-template-columns:minmax(170px,248px) minmax(0,1fr) 82px;
align-items:center;gap:14px;padding:7px 0}
/* La etiqueta envuelve en vez de recortarse: «Almacenamiento» con el distintivo
   al lado no cabe en una línea, y cortarlo a «Almacenam...» deja al componente
   más importante del informe sin nombre. */
.cs-name{display:flex;align-items:center;gap:8px;font-size:14px;min-width:0;flex-wrap:wrap}
.cs-name svg{color:var(--faint)}
.cs-name .badge{font-size:9px;padding:1px 7px}
.cs-bar .track{min-width:0}
.cs-num{text-align:right;font-weight:800;font-size:18px;letter-spacing:-.03em}
.cs-let{color:var(--faint);font-size:11px;font-weight:700;margin-left:6px;letter-spacing:.08em}
.strip .hint{border-top:1px solid var(--line);margin-top:10px}
@media (max-width:620px){
.cs-row{grid-template-columns:minmax(0,1fr) 66px;row-gap:2px}
.cs-bar{grid-column:1/-1}
}

/* -------------------------------------------------------- secciones ------ */
/* El tono dice para qué sirve cada sección, que es lo que decide en qué orden
   se leen: lo que hay que decidir, lo que explica por qué, y lo que solo está
   de referencia. Sin esto, doce secciones idénticas compiten todas igual. */
details.sec{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);
margin-bottom:16px;scroll-margin-top:calc(var(--nav) + 14px);box-shadow:var(--sh);
position:relative;overflow:hidden}
details.sec::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;
background:var(--tone,transparent)}
details.sec.t-accion{--tone:linear-gradient(180deg,var(--bad),var(--warn))}
details.sec.t-diagnostico{--tone:linear-gradient(180deg,var(--acc),transparent 70%)}
details.sec.t-referencia{--tone:var(--line2)}
details.sec.t-marca{--tone:linear-gradient(180deg,var(--gold),var(--gold-dk));
border-color:rgba(232,179,62,.26)}
details.sec>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:10px;
padding:14px 18px 14px 20px;font-size:11.5px;text-transform:uppercase;letter-spacing:.15em;
font-weight:700;color:var(--acc);user-select:none}
details.sec.t-accion>summary{color:var(--txt)}
details.sec.t-referencia>summary{color:var(--dim)}
details.sec.t-marca>summary{color:var(--gold)}
details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary:hover{background:rgba(255,255,255,.028)}
/* El margen automatico va en el titulo, no en el contador: «Inventario» y
   «Veredicto» no tienen contador, y con el `margin-left:auto` colgando de el
   sus controles se quedaban pegados al titulo mientras los de las demas
   secciones se iban al borde derecho. */
details.sec>summary .stitle{display:flex;align-items:center;gap:9px;min-width:0;
margin-right:auto}
details.sec>summary .cnt{color:var(--faint);font-size:11px;
letter-spacing:.02em;text-transform:none;font-weight:600;white-space:nowrap}
details.sec>summary .chev{color:var(--faint);transition:transform .18s}
details.sec[open]>summary{border-bottom:1px solid var(--line)}
details.sec[open]>summary .chev{transform:rotate(90deg)}
.body{padding:18px}
.sec.nomatch{display:none}

/* Índice interno de las secciones largas, generado por el JS a partir de los
   subtítulos: «Estado del sistema» son cinco tablas seguidas y llegar a la
   cuarta era puro scroll. */
.subnav{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 16px;padding-bottom:14px;
border-bottom:1px solid var(--line)}
.subnav a{font-size:11.5px;color:var(--dim);text-decoration:none;padding:4px 10px;
border:1px solid var(--line);border-radius:var(--pill);background:var(--surface2);
transition:color .15s,border-color .15s}
.subnav a:hover{color:var(--acc);border-color:var(--acc)}
.backtop{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--faint);
text-decoration:none;margin-top:14px}
.backtop:hover{color:var(--acc)}

/* -------------------------------------------------------- contenido ------ */
.card{background:var(--surface2);border:1px solid var(--line);border-radius:var(--r-s);
padding:16px;margin-bottom:12px}
.card:last-child{margin-bottom:0}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--faint);font-weight:700;font-size:10.5px;text-transform:uppercase;
letter-spacing:.11em;padding:9px 10px;border-bottom:1px solid var(--line2);white-space:nowrap}
td{padding:10px;border-bottom:1px solid var(--line)}
/* La fila 1 es la de encabezados, así que el rayado empieza en el primer dato. */
table tr:nth-of-type(2n){background:rgba(255,255,255,.02)}
table tr:hover td{background:var(--acc-soft)}
tr:last-child td{border-bottom:none}
tr.nomatch{display:none}
.track{height:7px;background:#1e2532;border-radius:4px;min-width:104px;position:relative}
.fill{height:100%;border-radius:4px;
animation:grow .8s cubic-bezier(.16,.84,.34,1) both}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.fill{transform-origin:left center}
/* Marca de la referencia dentro de la barra: sin ella, 100 y 190 puntos se
   pintaban igual de llenos y la barra dejaba de decir nada por encima de la
   media. Ahora el 100 es una línea fija y se ve quién la pasa. */
.track.ref{overflow:visible}
.track.ref .fill{max-width:100%}
.track.ref::after{content:"";position:absolute;top:-3px;bottom:-3px;left:52%;width:2px;
background:var(--dim);opacity:.5;border-radius:2px}
.track>.clip{position:absolute;inset:0;border-radius:4px;overflow:hidden}
.badge{display:inline-block;padding:2px 9px;border-radius:var(--pill);font-size:10.5px;
font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap}
.b-critical{background:rgba(255,59,71,.2);color:var(--bad)}
.b-high{background:rgba(255,95,95,.15);color:var(--bad)}
.b-medium{background:rgba(247,146,59,.16);color:var(--warn)}
.b-low{background:var(--acc-soft);color:var(--acc)}
.b-info{background:rgba(149,161,181,.14);color:var(--dim)}
.gain{color:var(--ok);font-weight:700}
.kvs{display:grid;grid-template-columns:200px minmax(0,1fr);gap:7px 16px;font-size:14px}
.kvs .k{color:var(--dim)}
.kvs div{overflow-wrap:anywhere}
.sub-h{color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.11em;
margin:20px 0 9px;font-weight:700;scroll-margin-top:calc(var(--nav) + 16px)}
.sub-h:first-child{margin-top:0}
.chead{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;
border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:14px}
.chead h3{margin:0;font-size:17px;display:flex;align-items:center;gap:9px;letter-spacing:-.015em}
.chead h3 svg{color:var(--acc)}
.chead .note{font-size:15px;font-weight:700;white-space:nowrap;letter-spacing:-.02em}
.imp{list-style:none;margin:0;padding:0;font-size:14px}
.imp li{padding:8px 0;border-bottom:1px solid var(--line)}
.imp li:last-child{border-bottom:none}
.imp a{text-decoration:none}
.imp a:hover{text-decoration:underline}
.tags{color:var(--faint);font-size:11.5px;margin-top:3px}
.finding h3{margin:0 0 9px;font-size:16px;letter-spacing:-.015em}
.finding p{margin:0 0 12px;color:#cbd4e0}
.finding ul{margin:0;padding-left:20px;color:#cbd4e0;font-size:14px}
.finding li{margin-bottom:5px}
.finding:target{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc)}
.steps-link{margin:0}
.steps-link a{display:inline-flex;align-items:center;gap:7px;font-size:13px;
text-decoration:none;color:var(--acc)}
.steps-link a:hover{text-decoration:underline}
details.howto{margin-top:14px;border:1px solid var(--line);border-radius:var(--r-s);
background:var(--acc-soft)}
details.howto>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:9px;
padding:10px 13px;font-size:13px;font-weight:600;color:var(--acc);user-select:none}
details.howto>summary::-webkit-details-marker{display:none}
details.howto>summary:hover{background:rgba(92,200,255,.07)}
details.howto>summary .cnt{margin-left:auto;color:var(--faint);font-size:11px;font-weight:500}
details.howto>summary .chev{color:var(--faint);transition:transform .18s}
details.howto[open]>summary{border-bottom:1px solid var(--line)}
details.howto[open]>summary .chev{transform:rotate(90deg)}
.howto-body{padding:6px 14px 14px}
.howto-item{padding:12px 0;border-bottom:1px dashed var(--line)}
.howto-item:last-child{border-bottom:none;padding-bottom:0}
.howto-item h4{margin:0 0 5px;font-size:14px}
.howto-item ol{margin:9px 0 0;padding-left:20px;font-size:14px;color:#cbd4e0}
.howto-item li{margin-bottom:6px}
.scan-note{color:var(--dim);font-size:12.5px;margin:0 0 12px;line-height:1.55}
.ok-note{color:var(--ok);margin:0}
.verdict{border-left:3px solid var(--gold);background:rgba(232,179,62,.055);
position:relative;overflow:hidden}
.verdict p{font-size:15.5px;line-height:1.6;margin-top:0}
code{background:#1e2532;padding:2px 6px;border-radius:5px;font-size:13px;
font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.pathcell{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;
word-break:break-all}

/* ------------------------------------------------------------ glosario --- */
/* Un término técnico se explica donde aparece por primera vez, no en un anexo
   que nadie abre. Va en prosa y nunca dentro de .tw, que tiene overflow y
   recortaría el globo. */
.term{position:relative;display:inline-flex;align-items:center;gap:4px;
border-bottom:1px dashed var(--faint);cursor:help;color:inherit}
.term>svg{color:var(--faint);flex:none}
.term .tip{position:absolute;left:0;bottom:calc(100% + 9px);z-index:70;width:max-content;
max-width:min(340px,74vw);background:var(--ink2);border:1px solid var(--line2);
border-radius:var(--r-s);padding:10px 12px;font-size:12.5px;line-height:1.55;
color:var(--txt);box-shadow:var(--sh-lg);opacity:0;visibility:hidden;
transform:translateY(4px);transition:opacity .15s,transform .15s,visibility .15s;
text-transform:none;letter-spacing:0;font-weight:400;text-align:left}
.term .tip::after{content:"";position:absolute;left:16px;top:100%;border:6px solid transparent;
border-top-color:var(--line2)}
.term:hover .tip,.term:focus .tip,.term:focus-visible .tip{opacity:1;visibility:visible;
transform:none}

/* --------------------------------------- selección y exportación --------- */
.pick{display:inline-flex;align-items:center;gap:7px;margin:-6px 0 -6px -6px;padding:6px;
border-radius:var(--r-xs);cursor:pointer;color:var(--faint);font-size:10.5px;
text-transform:uppercase;letter-spacing:.08em;font-weight:700;transition:color .15s,background .15s}
.pick:hover{background:rgba(255,255,255,.045);color:var(--dim)}
.pick input{width:16px;height:16px;accent-color:var(--acc);cursor:pointer;margin:0;flex:none}
.pick .ptxt{display:none}
@media (min-width:1100px){.pick .ptxt{display:inline}}
.exp{background:transparent;border:1px solid var(--line);color:var(--faint);
border-radius:var(--r-xs);padding:3px 9px;font:inherit;font-size:11px;cursor:pointer;
display:inline-flex;align-items:center;gap:5px;margin-left:10px;text-transform:none;
letter-spacing:0;font-weight:600}
.exp:hover{color:var(--acc);border-color:var(--acc)}
details.sec.picked{border-color:var(--acc);box-shadow:0 0 0 1px rgba(92,200,255,.32),var(--sh)}

/* Bandeja flotante: mientras se eligen secciones hay que poder ver cuáles van,
   y quitar una sin ir a buscarla por la página. */
.tray{position:fixed;right:20px;bottom:20px;z-index:65;width:min(320px,calc(100vw - 40px));
background:var(--ink2);border:1px solid var(--line2);border-radius:var(--r);
box-shadow:var(--sh-lg);padding:14px;display:none}
.tray.on{display:block}
.tray h4{margin:0 0 10px;font-size:10.5px;text-transform:uppercase;letter-spacing:.14em;
color:var(--faint);display:flex;align-items:center;gap:8px}
.tray h4 .n{margin-left:auto;color:var(--acc);font-weight:800;font-size:13px;
letter-spacing:0;text-transform:none}
.tray ul{list-style:none;margin:0 0 12px;padding:0;max-height:190px;overflow:auto;
display:flex;flex-direction:column;gap:3px}
.tray li{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--txt);
background:var(--surface);border:1px solid var(--line);border-radius:var(--r-xs);
padding:5px 6px 5px 10px}
.tray li span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.tray li button{background:none;border:0;color:var(--faint);cursor:pointer;padding:3px;
display:flex;border-radius:5px}
.tray li button:hover{color:var(--bad);background:rgba(255,95,95,.12)}
.tray .row{display:flex;gap:8px}
.tray .row .btn{flex:1;justify-content:center}
.presets{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:11px}
/* Los botones de preset llevan icono: sin `inline-flex` el SVG se apoya en la
   linea base del texto y queda pegado y descolgado. */
.presets button{background:var(--surface);border:1px solid var(--line);color:var(--dim);
border-radius:var(--pill);padding:4px 11px;font:inherit;font-size:11px;cursor:pointer;
display:inline-flex;align-items:center;gap:6px;line-height:1.5}
.presets button svg{flex:none}
.presets button:hover{color:var(--acc);border-color:var(--acc)}

/* --------------------------------- documento exportado por secciones ----- */
.exp-wrap{max-width:1040px;margin:0 auto;padding:28px 20px 60px}
.exp-sec{margin-bottom:34px}
.exp-h2{font-size:12px;text-transform:uppercase;letter-spacing:.15em;color:var(--acc);
margin:0 0 14px;font-weight:700;display:flex;align-items:center;gap:9px}
.exp-src{color:var(--dim);font-size:12px;border-left:2px solid var(--gold-dk);padding-left:11px;
margin:0 0 26px}

/* ---------------------------------- categorías del rastreo de archivos --- */
details.cat{border-bottom:1px solid var(--line)}
details.cat:last-of-type{border-bottom:none}
details.cat>summary{list-style:none;cursor:pointer;display:grid;
grid-template-columns:minmax(120px,1.4fr) 90px 90px 130px minmax(80px,1fr) 18px;
align-items:center;gap:10px;padding:10px 4px;font-size:14px}
details.cat>summary::-webkit-details-marker{display:none}
details.cat>summary:hover{background:rgba(255,255,255,.03)}
details.cat[open]>summary .chev{transform:rotate(90deg)}
details.cat .chev{color:var(--faint);transition:transform .18s}
details.cat .cat-count{color:var(--faint);font-size:12px}
details.cat .cat-bar{display:block;height:7px}
details.cat .cat-bar .fill{display:block}
.cat-body{padding:4px 4px 16px}
@media (max-width:720px){
details.cat>summary{grid-template-columns:1fr auto 18px}
details.cat .cat-count,details.cat .cat-bar{display:none}
}

footer{max-width:1320px;margin:0 auto;padding:20px;border-top:1px solid var(--line);
color:var(--faint);font-size:12px;display:flex;justify-content:space-between;
flex-wrap:wrap;gap:8px}
footer a{color:var(--gold);text-decoration:none}
#top{position:fixed;right:22px;bottom:22px;z-index:40;opacity:0;pointer-events:none;
transition:opacity .2s;border-radius:var(--pill);width:42px;height:42px;
justify-content:center;padding:0}
#top.on{opacity:1;pointer-events:auto}
.tray.on ~ #top{right:calc(min(320px,100vw - 40px) + 32px)}

@media (max-width:980px){
.layout{grid-template-columns:minmax(0,1fr);padding-top:18px}
.side{position:static;flex-direction:row;flex-wrap:wrap}
.side .panel{flex:1;min-width:236px}
.tb .logo span{display:none}
.here{display:none!important}
}
@media (prefers-reduced-motion:reduce){
html{scroll-behavior:auto}
*,*::before,*::after{animation-duration:.001ms!important;animation-iteration-count:1!important;
transition-duration:.001ms!important}
}
@media print{
.topbar,.side,#top,.exp,.pick,.tray,.subnav,.backtop,.find,.term>svg{display:none}
body::before{display:none}
html{background:#fff;background-image:none}
body{background:#fff;color:#000}
.layout{display:block;padding:0}
details.sec,.card,.panel,.hero .box,.strip{border-color:#ccc;background:#fff;
box-shadow:none;break-inside:avoid}
details.sec::before,.strip::before{display:none}
details.sec>summary,details.sec.t-referencia>summary{color:#000}
/* El rayado de las tablas se imprime como una mancha gris o no se imprime en
   absoluto, según el navegador. Sobre papel las líneas ya separan bastante. */
table tr:nth-of-type(2n){background:none}
.track{background:#e6e6e6;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.gauge .grail{stroke:#ddd}
/* El gris claro del cuerpo de los hallazgos está pensado para fondo oscuro:
   sobre papel blanco queda casi invisible, y ahí es donde están el diagnóstico
   y los pasos para arreglarlo, que es lo que la gente imprime. */
.finding p,.finding ul,.howto-item ol{color:#000}
/* El globo del glosario no se puede pasar el ratón por encima en papel: se
   imprime detrás del término, entre paréntesis. */
.term{border-bottom:0}
.term .tip{position:static;opacity:1;visibility:visible;transform:none;display:inline;
border:0;background:none;box-shadow:none;padding:0;color:#444;font-size:11px;max-width:none}
.term .tip::before{content:" ("}
.term .tip::after{content:")";position:static;border:0}
/* el dorado sobre papel blanco no se lee: se oscurece solo al imprimir */
footer a,.hero .n[style*="--brand"],header.page .kicker{color:var(--brand-dark)}
}
"""

HTML_JS = """
(function(){
  var secs = Array.prototype.slice.call(document.querySelectorAll('details.sec'));
  var links = Array.prototype.slice.call(document.querySelectorAll('.tb nav a'));
  var toggle = document.getElementById('toggle-all');
  var topbar = document.querySelector('.topbar');
  var prog = document.querySelector('.prog');
  var crumbName = document.getElementById('crumb-name');
  var crumbPct = document.getElementById('crumb-pct');

  // ---- Altura de la barra ---------------------------------------------------
  // La barra ya no tiene altura fija: los enlaces envuelven en varias filas si
  // hacen falta. Todo lo que dependia de su altura la mide en vez de suponerla.
  function navHeight(){ return topbar ? topbar.offsetHeight : 56; }
  var navPrev = 0;
  function syncNav(){
    // Solo se escribe si la altura cambio de verdad. El observador vigila la
    // misma barra que la variable dimensiona indirectamente, asi que sin este
    // corte cualquier diferencia de un pixel se realimenta hasta el infinito.
    var h = navHeight();
    if (Math.abs(h - navPrev) < 1) return;
    navPrev = h;
    document.documentElement.style.setProperty('--nav', h + 'px');
  }
  syncNav();
  window.addEventListener('resize', syncNav);
  if (window.ResizeObserver && topbar) new ResizeObserver(syncNav).observe(topbar);

  // ---- Plegar y desplegar ---------------------------------------------------
  function setAll(open){
    secs.forEach(function(s){ s.open = open; });
    toggle.dataset.open = open ? '1' : '0';
    toggle.querySelector('span').textContent = open ? 'Colapsar todo' : 'Expandir todo';
  }
  toggle.addEventListener('click', function(){ setAll(toggle.dataset.open !== '1'); });

  // Saltar a una seccion plegada no serviria de nada: se abre antes de ir.
  function abrir(id){
    var t = document.getElementById(id);
    if (t && t.tagName === 'DETAILS') t.open = true;
    return t;
  }
  // Al pulsar un enlace, la seccion activa se fija en el destino hasta que el
  // desplazamiento suave termina. Sin esto el resaltado iba saltando por todas
  // las secciones intermedias mientras la pagina viajaba, que es informacion
  // que nadie ha pedido y ademas distrae de a donde se va.
  var fijada = null, vigilante = null;
  function soltarCuandoPare(){
    var ultimo = -1;
    if (vigilante) clearTimeout(vigilante);
    (function mirar(){
      if (window.scrollY === ultimo) { fijada = null; vigilante = null; spy(); return; }
      ultimo = window.scrollY;
      vigilante = setTimeout(mirar, 90);
    })();
  }
  links.forEach(function(a){
    a.addEventListener('click', function(){
      abrir(a.dataset.target);
      fijada = a.dataset.target;
      spy();
      soltarCuandoPare();
    });
  });

  // ---- Indice interno de las secciones largas -------------------------------
  // Se construye leyendo los subtitulos que ya existen, no con una lista escrita
  // a mano: asi ninguna seccion nueva se queda sin indice, y ninguna lo tiene
  // desactualizado. Por debajo de tres subtitulos no compensa: estorba mas de lo
  // que ayuda.
  secs.forEach(function(s){
    var body = s.querySelector('.body');
    if (!body) return;
    var subs = Array.prototype.slice.call(body.querySelectorAll('.sub-h'));
    if (subs.length < 3) return;
    var nav = document.createElement('div');
    nav.className = 'subnav';
    subs.forEach(function(h, i){
      if (!h.id) h.id = s.id + '-s' + i;
      var a = document.createElement('a');
      a.href = '#' + h.id;
      a.textContent = h.textContent.trim();
      nav.appendChild(a);
    });
    body.insertBefore(nav, body.firstChild);
    var back = document.createElement('a');
    back.className = 'backtop';
    back.href = '#' + s.id;
    back.innerHTML = '<svg class="ic sm" viewBox="0 0 24 24" aria-hidden="true">' +
                     '<use href="#i-up"/></svg>Volver al principio de ' +
                     s.dataset.title.toLowerCase();
    body.appendChild(back);
  });

  // ---- Seccion activa, progreso de lectura y volver arriba ------------------
  var targets = links.map(function(a){ return document.getElementById(a.dataset.target); })
                     .filter(Boolean);
  var topBtn = document.getElementById('top');
  function spy(){
    var best = null, bestTop = -1e9;
    targets.forEach(function(t){
      var top = t.getBoundingClientRect().top - (navHeight() + 34);
      if (top <= 0 && top > bestTop) { bestTop = top; best = t; }
    });
    var alto = document.documentElement.scrollHeight - window.innerHeight;
    // La ultima seccion nunca llegaba a marcarse: el veredicto y el pie juntos
    // miden menos que la ventana, asi que por mucho que se baje su borde
    // superior no cruza el umbral. Al tocar fondo, la activa es la ultima.
    if (targets.length && alto > 0 && window.scrollY >= alto - 2) {
      best = targets[targets.length - 1];
    }
    if (fijada) best = document.getElementById(fijada) || best;
    var id = best ? best.id : '';
    links.forEach(function(a){ a.classList.toggle('active', a.dataset.target === id); });
    var avance = alto > 0 ? Math.min(100, Math.max(0, window.scrollY / alto * 100)) : 0;
    if (crumbName) crumbName.textContent = best ? (best.dataset.title || 'Resumen') : 'Resumen';
    if (crumbPct) crumbPct.textContent = avance.toFixed(0) + '% leido';
    if (prog) prog.style.width = avance + '%';
    topBtn.classList.toggle('on', window.scrollY > 500);
  }
  window.addEventListener('scroll', spy, {passive:true});
  window.addEventListener('resize', spy);
  spy();

  // ---- Buscador -------------------------------------------------------------
  // Filtra filas de tabla, hallazgos y elementos de lista sin tocar el DOM mas
  // alla de una clase: hay informes con doscientas filas de ficheros y encontrar
  // uno a ojo no es razonable. Se recuerda que secciones estaban abiertas para
  // devolverlas a su sitio al vaciar la busqueda.
  var buscador = document.getElementById('buscar');
  var contador = document.getElementById('buscar-cnt');
  var caja = document.querySelector('.find');
  var abiertasAntes = null;

  function filtrables(){
    return Array.prototype.slice.call(
      document.querySelectorAll('.body table tr, .body .card.finding, .body .imp li, ' +
                                '.body details.cat'));
  }

  function filtrar(texto){
    var q = texto.trim().toLowerCase();
    if (q && abiertasAntes === null) {
      abiertasAntes = secs.map(function(s){ return s.open; });
    }
    var total = 0;
    if (!q) {
      filtrables().forEach(function(el){ el.classList.remove('nomatch'); });
      secs.forEach(function(s, i){
        s.classList.remove('nomatch');
        if (abiertasAntes) s.open = abiertasAntes[i];
      });
      abiertasAntes = null;
    } else {
      filtrables().forEach(function(el){
        // La fila de encabezados no se filtra nunca: sin ella la tabla que
        // queda es una lista de numeros sin nombre.
        if (el.tagName === 'TR' && el.querySelector('th')) return;
        var hit = el.textContent.toLowerCase().indexOf(q) >= 0;
        el.classList.toggle('nomatch', !hit);
        if (hit) total++;
      });
      secs.forEach(function(s){
        var body = s.querySelector('.body');
        var hit = !!body && body.textContent.toLowerCase().indexOf(q) >= 0;
        s.classList.toggle('nomatch', !hit);
        if (hit) s.open = true;
      });
    }
    if (contador) contador.textContent = q ? (total + (total === 1 ? ' fila' : ' filas')) : '';
    if (caja) caja.classList.toggle('hit', !!q);
  }

  if (buscador) {
    buscador.addEventListener('input', function(){ filtrar(buscador.value); });
    buscador.addEventListener('keydown', function(ev){
      if (ev.key === 'Escape') { buscador.value = ''; filtrar(''); buscador.blur(); }
    });
    // Los ejemplos se aplican con mousedown y no con click: al soltar el raton
    // el campo ya habria perdido el foco, el panel se habria cerrado por CSS y
    // el clic caeria en el vacio.
    Array.prototype.forEach.call(document.querySelectorAll('[data-tip]'), function(b){
      b.addEventListener('mousedown', function(ev){
        ev.preventDefault();
        buscador.value = b.dataset.tip;
        filtrar(buscador.value);
      });
    });
  }

  // ---- Exportacion de secciones ---------------------------------------------
  // El fichero resultante se construye con el mismo <style> y el mismo sprite de
  // iconos que este informe, asi que sale igual de autocontenido: se puede
  // enviar por correo y abrir sin conexion.
  var exportBtn = document.getElementById('export-sel');
  var exportLabel = exportBtn.querySelector('span');
  var tray = document.getElementById('tray');
  var trayList = document.getElementById('tray-list');
  var trayCount = document.getElementById('tray-count');
  var host = (document.body.dataset.host || 'equipo');
  var stamp = (document.body.dataset.stamp || '');

  function casilla(s){ return s.querySelector('input[data-pick]'); }
  function picked(){
    return secs.filter(function(s){ var b = casilla(s); return b && b.checked; });
  }

  function marcar(s, valor){
    var b = casilla(s);
    if (b) b.checked = valor;
  }

  function refresh(){
    var elegidas = picked();
    var n = elegidas.length;
    exportLabel.textContent = 'Exportar (' + n + ')';
    exportBtn.disabled = n === 0;
    secs.forEach(function(s){
      var b = casilla(s);
      s.classList.toggle('picked', !!b && b.checked);
    });
    if (!tray) return;
    tray.classList.toggle('on', n > 0);
    trayCount.textContent = n;
    trayList.innerHTML = '';
    elegidas.forEach(function(s){
      var li = document.createElement('li');
      var nombre = document.createElement('span');
      nombre.textContent = s.dataset.title;
      var quita = document.createElement('button');
      quita.type = 'button';
      quita.setAttribute('aria-label', 'Quitar ' + s.dataset.title + ' de la exportacion');
      quita.innerHTML = '<svg class="ic sm" viewBox="0 0 24 24" aria-hidden="true">' +
                        '<use href="#i-x"/></svg>';
      quita.addEventListener('click', function(){ marcar(s, false); refresh(); });
      li.appendChild(nombre);
      li.appendChild(quita);
      trayList.appendChild(li);
    });
  }

  // Presets: un informe entero es mucho para adjuntar en un correo, y lo que se
  // suele querer mandar son dos cosas concretas. `data-group` lo declara el
  // generador, que es quien sabe para que sirve cada seccion.
  function aplicarPreset(grupo){
    secs.forEach(function(s){
      var grupos = (s.dataset.group || '').split(' ');
      marcar(s, grupo === 'all' ? true : grupo === 'none' ? false
                                       : grupos.indexOf(grupo) >= 0);
    });
    refresh();
  }
  Array.prototype.forEach.call(document.querySelectorAll('[data-preset]'), function(b){
    b.addEventListener('click', function(){ aplicarPreset(b.dataset.preset); });
  });

  function buildDocument(sections){
    var style = document.querySelector('style').outerHTML;
    var sprite = document.getElementById('sprite').outerHTML;
    // El icono viaja dentro de su propia URL, asi que el extracto se lleva la
    // marca en la pestaña igual que el informe completo y sigue sin depender
    // de ningun fichero al lado.
    var icono = document.querySelector('link[rel="icon"]');
    icono = icono ? icono.outerHTML : '';
    var head = document.querySelector('header.page').innerHTML;
    var foot = document.querySelector('footer').outerHTML;
    var titles = sections.map(function(s){ return s.dataset.title; });
    var origen = 'Extracto del informe completo' +
                 (stamp ? ' generado el ' + stamp : '') +
                 '. Secciones incluidas: ' + titles.join(' · ') + '.';
    var cuerpo = sections.map(function(s){
      var ico = '<svg class="ic lg" viewBox="0 0 24 24" aria-hidden="true"><use href="#' +
                s.dataset.icon + '"/></svg>';
      // El indice interno y el «volver» solo tienen sentido dentro del informe
      // completo: en el extracto apuntarian a secciones que no viajan con el.
      var copia = s.querySelector('.body').cloneNode(true);
      Array.prototype.forEach.call(copia.querySelectorAll('.subnav,.backtop'), function(e){
        e.remove();
      });
      return '<section class="exp-sec"><h2 class="exp-h2">' + ico + s.dataset.title +
             '</h2>' + copia.innerHTML + '</section>';
    }).join('');
    return '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<title>' + titles.join(' + ') + ' · ' + host + '</title>' + icono + style +
      '</head><body>' + sprite + '<div class="exp-wrap"><header class="page">' + head +
      '</header><p class="exp-src">' + origen + '</p>' + cuerpo + foot +
      '</div></body></html>';
  }

  function descargar(sections){
    if (!sections.length) return;
    var nombre = 'quilate-' + host + '-' +
      (sections.length === 1 ? sections[0].dataset.slug : 'seleccion') + '.html';
    var blob = new Blob([buildDocument(sections)], {type:'text/html;charset=utf-8'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = nombre;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function(){ URL.revokeObjectURL(url); }, 10000);
  }

  secs.forEach(function(s){
    var etiqueta = s.querySelector('label.pick');
    var box = casilla(s);
    var btn = s.querySelector('button[data-export]');
    // Sin esto, cualquier clic dentro del <summary> pliega la seccion: la
    // etiqueta entera es zona de marcado, no solo el cuadradito.
    if (etiqueta) etiqueta.addEventListener('click', function(ev){ ev.stopPropagation(); });
    if (box) {
      box.addEventListener('click', function(ev){ ev.stopPropagation(); });
      box.addEventListener('change', refresh);
      // Con el teclado, la barra espaciadora sobre el checkbox no puede acabar
      // desplegando la seccion que lo contiene.
      box.addEventListener('keydown', function(ev){ ev.stopPropagation(); });
    }
    if (btn) btn.addEventListener('click', function(ev){
      ev.preventDefault(); ev.stopPropagation(); descargar([s]);
    });
  });
  exportBtn.addEventListener('click', function(){ descargar(picked()); });
  var trayBtn = document.getElementById('tray-export');
  if (trayBtn) trayBtn.addEventListener('click', function(){ descargar(picked()); });
  refresh();
})();
"""


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


def _section(sec: Seccion) -> str:
    cnt = f'<span class="cnt">{_e(sec.count)}</span>' if sec.count else ""
    # La casilla y el botón viven dentro del <summary>: el JS corta ahí la
    # propagación del clic para que marcar o exportar no pliegue la sección.
    # Va envuelta en un <label> con texto propio —oculto en pantallas
    # estrechas, pero siempre presente para un lector de pantalla— porque un
    # checkbox suelto no dice qué marca, y aquí marcarlo no selecciona la
    # sección: la añade a un fichero que se va a descargar.
    pick = (f'<label class="pick" for="pick-{sec.sid}">'
            f'<input type="checkbox" id="pick-{sec.sid}" data-pick="{sec.sid}" '
            f'aria-label="Incluir «{_e(sec.label)}» en la exportación por secciones">'
            f'<span class="ptxt">Exportar</span></label>')
    export = (f'<button class="exp" data-export="{sec.sid}" type="button" '
              f'title="Descargar solo esta sección como fichero HTML suelto">'
              f'{_icon("i-download")}Descargar</button>')
    return (f'<details class="sec t-{sec.tono}" id="{sec.sid}" data-title="{_e(sec.label)}" '
            f'data-slug="{_slug(sec.label)}" data-icon="{sec.icon}" '
            f'data-group="{sec.grupo}" open>'
            f'<summary><span class="stitle">{_icon(sec.icon)}{_e(sec.label)}</span>'
            f"{cnt}{pick}{export}"
            f'{_icon("i-chev", "ic chev")}</summary>'
            f'<div class="body">{sec.inner}</div></details>')


# ------------------------------------------------------------------ bloques --
def _hero(bench: Benchmark | None, auditor: Auditor, projection: dict[str, Any]) -> str:
    boxes = []
    if bench and bench.results:
        overall = bench.overall()
        letter, _ = grade(overall)
        proj = projection.get("projected_overall", overall)
        exp_pct = projection.get("experiential_pct", 0.0)
        boxes.append(f'<div class="box" style="--edge:{_score_color(overall)}">'
                     f'<div class="n" style="color:{_score_color(overall)}">{overall:.0f}</div>'
                     f'<div class="l">Puntuación actual · nota {letter}</div>'
                     f'<div class="foot">100 = equipo de gama media</div></div>')
        boxes.append(f'<div class="box" style="--edge:var(--ok)">'
                     f'<div class="n" style="color:var(--ok)">{proj:.0f}</div>'
                     f'<div class="l">Tras optimizar</div>'
                     f'<div class="foot">+{projection.get("headroom_pct", 0.0):.0f}% sobre la '
                     f"nota de hoy</div></div>")
        boxes.append(f'<div class="box" style="--edge:var(--brand)">'
                     f'<div class="n" style="color:var(--brand)">+{exp_pct:.0f}%</div>'
                     f'<div class="l">Fluidez percibida estimada</div>'
                     f'<div class="foot">lo que se nota al usarlo, no lo que sube la nota'
                     f"</div></div>")
    sin_datos = len(getattr(auditor, "unverified", []))
    graves = sum(1 for f in auditor.findings if f.severity in ("critical", "high"))
    borde = ("var(--bad)" if graves else "var(--warn)" if auditor.findings else "var(--ok)")
    pie = (f"{graves} de severidad alta o crítica" if graves
           else "ninguno grave" if auditor.findings else "nada que corregir")
    if sin_datos:
        pie += f" · {sin_datos} sin comprobar"
    boxes.append(f'<div class="box" style="--edge:{borde}"><div class="n">'
                 f'{len(auditor.findings)}</div>'
                 f'<div class="l">Hallazgos en {auditor.checks_run} pruebas concluyentes</div>'
                 f'<div class="foot">{pie}</div></div>')
    return f'<div class="hero" id="resumen">{"".join(boxes)}</div>'


def _component_strip(bench: Benchmark | None) -> str:
    """Las notas por componente, una al lado de otra y sobre la misma escala.

    Es la respuesta a «¿por dónde falla?», que hasta ahora había que deducir
    comparando filas sueltas de la tabla del benchmark. Con la referencia marcada
    en todas las barras, el componente que se queda corto salta a la vista.
    """
    comp = bench.component_scores() if bench else {}
    if not comp:
        return ""
    peor = min(comp, key=lambda k: comp[k])
    filas = ""
    for clave, nota in sorted(comp.items(), key=lambda x: x[1]):
        letra, _ = grade(nota)
        marca = ('<span class="badge b-medium">cuello de botella</span>'
                 if clave == peor and len(comp) > 1 else "")
        filas += (f'<div class="cs-row"><div class="cs-name">'
                  f'{_icon(COMPONENT_ICONS.get(COMPONENT_TO_GROUP.get(clave, clave), "i-sys"))}'
                  f"<span>{_e(COMPONENT_LABELS.get(clave, clave))}</span>{marca}</div>"
                  f'<div class="cs-bar">{_html_bar(nota)}</div>'
                  f'<div class="cs-num" style="color:{_score_color(nota)}">{nota:.0f}'
                  f'<span class="cs-let">{_e(letra)}</span></div></div>')
    return (f'<div class="strip"><div class="lbl">Nota por componente</div>{filas}'
            f'<div class="hint">La marca de cada barra son los 100 puntos de la '
            f"referencia. Lo que sobresale por la derecha va por encima de un equipo "
            f"de gama media reciente.</div></div>")


def _findings_panel(auditor: Auditor, bench: Benchmark | None) -> str:
    """Recuento por severidad y cuello de botella.

    Aquí solo van las cifras totales. Se probó a desglosarlo por secciones y no
    cuadraba: los mismos cinco hallazgos aparecen en la ficha por componente, en
    el plan de acción y en el detalle, así que los recuentos por sección se
    solapaban y sumaban el triple del total que hay justo encima. Dónde está cada
    uno ya lo dicen los puntos de color de la navegación.
    """
    counts: dict[str, int] = {}
    for f in auditor.findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    chips = "".join(f'<span class="badge b-{s}">{n} {SEVERITY_LABELS.get(s, s)}</span>'
                    for s, n in sorted(counts.items(), key=lambda x: SEVERITY_ORDER[x[0]]))
    hint = ""
    comp = bench.component_scores() if bench else {}
    if comp:
        weakest = min(comp, key=lambda k: comp[k])
        hint = (f'<div class="hint">Cuello de botella: '
                f"<b>{COMPONENT_LABELS.get(weakest, weakest)}</b> "
                f"({comp[weakest]:.0f} pts)</div>")
    return (f'<div class="panel"><div class="lbl">Hallazgos</div>'
            f'<div class="chips">{chips or "ninguno"}</div>{hint}</div>')


def _sidebar(si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
             projection: dict[str, Any], secs: list[Seccion] | None = None) -> str:
    panels = []

    if bench and bench.results:
        overall = bench.overall()
        letter, _ = grade(overall)
        delta = ""
        if projection.get("projected_overall"):
            delta = (f'<div class="delta">Tras optimizar '
                     f'<b style="color:var(--ok)">{projection["projected_overall"]:.0f} pts</b>'
                     f' · fluidez percibida +{projection.get("experiential_pct", 0):.0f}%</div>')
        # El isotipo de filigrana al fondo: es la única cifra que resume todo el
        # informe, y el sitio donde la marca no compite con ningún dato.
        panels.append(f'<div class="panel mark">{_logo("brandmark wm", uid="wm")}'
                      f'<div class="lbl">Puntuación global</div>'
                      f"{_gauge(overall, letter)}"
                      f'<div class="sub" style="text-align:center">La marca de arriba son '
                      f'los 100 puntos de la {_term("referencia", "referencia")}</div>'
                      f"{delta}</div>")
    else:
        panels.append('<div class="panel mark"><div class="lbl">Puntuación global</div>'
                      '<div class="sub">Ejecutado sin benchmark: informe solo de auditoría.'
                      "</div></div>")

    panels.append(_findings_panel(auditor, bench))

    ram = f"{si.ram_total / 1024**3:.1f} GB"
    if si.ram_speed_mhz:
        ram += f" @ {si.ram_speed_mhz} MT/s"
    equipo = [
        ("i-sys", f"{si.hostname}  ·  {'portátil' if si.is_laptop else 'sobremesa'}"),
        ("i-shield", si.os_name),
        ("i-cpu", f"{si.cpu_name} ({si.cpu_cores}C/{si.cpu_threads}T)"),
        ("i-ram", ram),
        ("i-disk", f"{si.system_drive} · {si.system_drive_media}"),
        ("i-gpu", (primary_gpu(si.gpus) or {}).get("name") or "sin datos de GPU"),
        ("i-clock", f"{si.uptime_hours:.1f} h encendido"),
    ]
    items = "".join(f"<li>{_icon(k)}<span>{_e(v)}</span></li>" for k, v in equipo)
    panels.append(f'<div class="panel"><div class="lbl">Equipo</div>'
                  f'<ul class="mini">{items}</ul></div>')

    return f'<aside class="side">{"".join(panels)}</aside>'


def _inventory(si: SystemInfo) -> str:
    """Inventario completo, con el mismo detalle que la consola: TODOS los
    volúmenes y discos físicos, no solo la unidad de sistema."""
    rows = [
        ("Equipo", f"{si.hostname}  ·  {'portátil' if si.is_laptop else 'sobremesa'}"),
        ("Sistema operativo", f"{si.os_name} · {si.os_build}"),
    ]
    if si.os_install_date:
        rows.append(("Instalación", f"{si.os_install_date} "
                                    f"({(si.os_age_days or 0) / 365.25:.1f} años)"))
    if si.bios_date:
        rows.append(("BIOS", si.bios_date))
    rows += [
        ("Tiempo encendido", f"{si.uptime_hours:.1f} h"),
        # Es el reloj base: Win32_Processor no publica la frecuencia de boost.
        ("CPU", f"{si.cpu_name} — {si.cpu_cores} núcleos / {si.cpu_threads} hilos"
                + (f" · base {si.cpu_base_mhz or si.cpu_max_mhz:.0f} MHz"
                   if (si.cpu_base_mhz or si.cpu_max_mhz) else "")),
        ("Memoria", f"{si.ram_total / 1024**3:.1f} GB"
                    + (f" @ {si.ram_speed_mhz} MT/s" if si.ram_speed_mhz else "")
                    + (f" · {si.ram_channels} módulo(s)" if si.ram_channels else "")
                    + (f" · módulos de {si.ram_speed_rated_mhz} MT/s, perfil XMP/EXPO sin activar"
                       if si.ram_speed_rated_mhz and si.ram_speed_mhz
                       and si.ram_speed_rated_mhz > si.ram_speed_mhz else "")),
    ]
    for g in si.gpus:
        rows.append(("GPU", f"{gpu_label(g)} · driver {g.get('driver')} "
                            f"({g.get('driver_date') or 'fecha n/d'})"))
    rows += [
        ("Disco de sistema", f"{si.system_drive} · {si.system_drive_media}"),
        ("Privilegios", "administrador" if si.is_admin
                        else "usuario estándar (algunas comprobaciones limitadas)"),
        ("Python", si.python_version),
    ]
    kvs = "".join(f'<div class="k">{_e(k)}</div><div>{_e(v)}</div>' for k, v in rows)
    out = [f'<div class="card"><div class="kvs">{kvs}</div></div>']

    if si.ram_sticks:
        trs = ""
        for s in si.ram_sticks:
            marca = " ".join(x for x in (s.get("vendor"), s.get("part")) if x) or "—"
            real = s.get("speed") or 0
            nominal = s.get("rated_speed") or 0
            vel = f"{real} MT/s" if real else "—"
            if nominal and real and nominal > real:
                vel += f' <span class="tags">(soporta {nominal})</span>'
            trs += (f"<tr><td>{_e(s.get('slot') or '?')}</td>"
                    f"<td>{_human(s.get('capacity') or 0)}</td><td>{vel}</td>"
                    f"<td>{_e(marca)}</td></tr>")
        out.append('<div class="card"><div class="sub-h">Módulos de memoria</div><div class="tw">'
                   "<table><tr><th>Ranura</th><th>Capacidad</th><th>Velocidad</th>"
                   "<th>Fabricante y modelo</th></tr>" + trs + "</table></div></div>")

    phys = ""
    hay_fiabilidad = any(p.get("power_on_hours") is not None or p.get("temperature") is not None
                         for p in si.physical_disks)
    for p in si.physical_disks:
        health = str(p.get("health") or "n/d")
        color = "var(--ok)" if health.lower() in ("healthy", "sano", "0") else "var(--bad)"
        phys += (f"<tr><td>{_e(p['name'])}</td><td>{_e(p['media'])}</td>"
                 f"<td>{_e(p['bus'])}</td><td>{_human(p['size'])}</td>"
                 f'<td style="color:{color}">{_e(health)}</td>')
        if hay_fiabilidad:
            # El desgaste solo significa algo en un disco de estado sólido.
            es_ssd = "SSD" in str(p.get("media") or "").upper()
            desgaste = p.get("wear")
            horas = p.get("power_on_hours")
            grados = p.get("temperature")
            errores = (p.get("read_errors") or 0) + (p.get("write_errors") or 0)
            phys += (f"<td>{f'{desgaste}%' if es_ssd and desgaste is not None else '—'}</td>"
                     f"<td>{f'{horas:,} h'.replace(',', '.') if horas is not None else '—'}</td>"
                     f"<td>{f'{grados} °C' if grados is not None else '—'}</td>"
                     f'<td style="color:{"var(--bad)" if errores else "inherit"}">'
                     f"{errores if errores else '—'}</td>")
        phys += "</tr>"
    if phys:
        extra = ("<th>Desgaste</th><th>Horas</th><th>Temp.</th><th>Errores</th>"
                 if hay_fiabilidad else "")
        nota = ("" if hay_fiabilidad else
                '<p class="scan-note">Los contadores de desgaste, horas de uso y errores '
                "necesitan privilegios de administrador y no se han podido leer.</p>")
        out.append('<div class="card"><div class="sub-h">Discos físicos</div>' + nota
                   + '<div class="tw"><table>'
                   "<tr><th>Unidad</th><th>Tipo</th><th>Conexión</th><th>Capacidad</th>"
                   "<th>Salud</th>" + extra + "</tr>" + phys + "</table></div></div>")

    vols = ""
    for d in si.disks:
        if d["total"] <= 5 * 1024**3:
            continue
        name = f"<b>{_e(d['mount'])}</b>"
        if d["label"]:
            name += f'<div class="tags">{_e(d["label"])}</div>'
        if d["ignored"]:
            # Un volumen de nube o de red informa de un tamaño que no es del
            # equipo: se muestra para que se vea, pero sin barra ni semáforo,
            # porque «liberar espacio» ahí no significa nada.
            kind = KIND_LABELS.get(d["kind"], d["kind"])
            vols += (f"<tr><td>{name}</td><td>{_e(d['fstype'])}</td>"
                     f"<td>{_human(d['free'])}</td><td>{_human(d['total'])}</td>"
                     f'<td colspan="2"><span class="badge b-info">{_e(kind)}</span> '
                     f'<span style="color:var(--dim)">excluido de la auditoría</span></td></tr>')
            continue
        free_pct = 100 - d["percent"]
        color = "var(--bad)" if free_pct < 10 else "var(--warn)" if free_pct < 20 else "var(--ok)"
        disk = d.get("physical") or {}
        media = f'<div class="tags">{_e(disk.get("media", ""))} · {_e(disk.get("bus", ""))}</div>' \
            if disk else ""
        vols += (f"<tr><td>{name}</td><td>{_e(d['fstype'])}{media}</td>"
                 f"<td>{_human(d['free'])}</td><td>{_human(d['total'])}</td>"
                 f'<td style="color:{color}">{free_pct:.0f}% libre</td>'
                 f'<td><div class="track"><div class="fill" style="width:{d["percent"]:.0f}%;'
                 f'background:{color}"></div></div></td></tr>')
    if vols:
        out.append('<div class="card"><div class="sub-h">Volúmenes</div><div class="tw">'
                   "<table><tr><th>Unidad</th><th>Formato</th><th>Libre</th><th>Total</th>"
                   "<th>Estado</th><th>Ocupación</th></tr>" + vols + "</table></div></div>")
    return "".join(out)


def _benchmark_table(bench: Benchmark) -> str:
    dispersion = getattr(bench, "dispersion", {})
    # Las claves de dispersión de CPU no coinciden con las de resultado: los
    # cuatro subtests monohilo se agregan en una sola fila.
    equivalencias = {"cpu_single": ("sieve", "float", "hash", "compress")}
    trs = ""
    for key, r in bench.results.items():
        letter, _ = grade(r.score)
        measure = f"{r.raw:,.0f}" if r.unit == "IOPS" else f"{r.raw:,.2f}"
        detail = f'<div class="tags">{_e(r.detail)}</div>' if r.detail else ""
        relacionadas = [dispersion[k] for k in equivalencias.get(key, (key,))
                        if k in dispersion]
        margen = "—"
        if relacionadas:
            peor = max(relacionadas, key=lambda d: d["spread_pct"])
            color = "var(--dim)" if peor["stable"] else "var(--warn)"
            margen = (f'<span style="color:{color}" title="dispersión entre '
                      f'{peor["runs"]} medidas de «{_e(peor["label"])}»">'
                      f'±{peor["spread_pct"]:.0f}%</span>')
        trs += (f"<tr><td>{_e(r.name)}{detail}</td><td>{measure} {_e(r.unit)}</td>"
                f"<td>{margen}</td>"
                f"<td><b>{r.score:.0f}</b></td><td>{letter}</td>"
                f"<td>{_html_bar(r.score)}</td></tr>")
    overall = bench.overall()
    letter, _ = grade(overall)
    inestables = bench.unstable() if hasattr(bench, "unstable") else []
    aviso = ""
    if inestables:
        aviso = ('<p class="scan-note" style="color:var(--warn)">Medidas poco estables: '
                 + ", ".join(f"{_e(d['label'])} (±{d['spread_pct']:.0f}%)" for d in inestables)
                 + ". La misma prueba dio resultados distintos entre tramos, así que esas "
                   "cifras valen como orden de magnitud pero no para comparar con otra "
                   "ejecución.</p>")
    ocupada = bench.busy_during_run() if hasattr(bench, "busy_during_run") else 0.0
    if ocupada >= BUSY_CPU_PCT:
        aviso += ('<p class="scan-note" style="color:var(--warn)">El equipo no estaba en '
                  f"reposo: un {ocupada:.0f}% de CPU la consumían otros procesos con el "
                  "benchmark parado.</p>")
    # Una tabla sin filas de GPU no distingue «este equipo no tiene» de «no se
    # ha mirado». La razón concreta va aquí, junto a la ausencia.
    if getattr(bench, "gpu_unavailable", ""):
        aviso += (f'<p class="scan-note">La gráfica no se ha podido medir por '
                  f'{_term("OpenCL", "opencl")}: {_e(bench.gpu_unavailable)}. Su peso se '
                  "reparte entre el resto de componentes, así que la nota global no la "
                  "penaliza.</p>")
    return ('<div class="card"><div class="tw"><table>'
            "<tr><th>Prueba</th><th>Medida</th><th>Margen</th><th>Puntos</th><th>Nota</th>"
            "<th>Relativo a la referencia</th></tr>" + trs
            + f"<tr><td><b>Puntuación global</b></td><td></td><td></td>"
              f'<td style="color:{_score_color(overall)}"><b>{overall:.0f}</b></td>'
              f"<td><b>{letter}</b></td><td>{_html_bar(overall)}</td></tr>"
              "</table></div>"
            + aviso
            + f'<p class="scan-note">El {_term("margen", "margen")} es cuánto varió cada '
              "prueba consigo misma entre repeticiones o tramos del mismo trabajo. Un "
              "número sin margen no revela nunca que está contaminado.</p></div>")


def _howto_subcard(card: ComponentCard) -> str:
    """Subtarjeta plegada con el «cómo» de cada mejora del componente.

    Va cerrada por defecto a propósito: la ficha se lee de un vistazo para
    decidir, y solo se despliega el procedimiento del que se vaya a aplicar.
    """
    steps_total = sum(len(f.steps) for f in card.findings)
    if not steps_total:
        return ""
    blocks = ""
    for i, f in enumerate(card.findings, 1):
        if not f.steps:
            continue
        steps = "".join(f"<li>{_e(s)}</li>" for s in f.steps)
        gain = (f'<span class="gain">+{f.gain * 100:.0f}%</span> ' if f.gain else "")
        blocks += (f'<div class="howto-item"><h4>{i}. {_e(f.title)}</h4>'
                   f'<div class="tags">{gain}<span class="badge b-{f.severity}">{f.severity}'
                   f"</span> &nbsp; esfuerzo {_e(f.effort)} · riesgo {_e(f.risk)}"
                   f"{' · ' + _e(f.gain_note) if f.gain_note else ''}</div>"
                   f"<ol>{steps}</ol></div>")
    procedures = sum(1 for f in card.findings if f.steps)
    count = (f"{procedures} procedimiento{'s' if procedures != 1 else ''} · "
             f"{steps_total} paso{'s' if steps_total != 1 else ''}")
    return (f'<details class="howto"><summary>{_icon("i-wrench")}'
            f'Cómo aplicar estas mejoras<span class="cnt">{count}</span>'
            f'{_icon("i-chev", "ic chev")}</summary>'
            f'<div class="howto-body">{blocks}</div></details>')


def _html_component_cards(cards: list[ComponentCard]) -> str:
    parts = []
    for card in cards:
        if card.score is not None:
            note = (f'<span class="note" style="color:{_score_color(card.score)}">'
                    f"{card.score:.0f} pts · nota {card.letter}</span>")
        else:
            note = f'<span class="note" style="color:var(--dim)">{_no_score_text(card)}</span>'

        icon = _icon(COMPONENT_ICONS.get(card.key, "i-sys"), "ic lg")
        block = [f'<div class="card" id="c-{_e(card.key)}"><div class="chead">'
                 f"<h3>{icon}{_e(card.label)}</h3>{note}</div>"]

        if card.specs:
            specs = "".join(f'<div class="k">{_e(k)}</div><div>{_e(v)}</div>'
                            for k, v in card.specs)
            block.append(f'<div class="kvs">{specs}</div>')

        if card.tests:
            trs = ""
            for r in card.tests:
                measure = f"{r.raw:,.0f}" if r.unit == "IOPS" else f"{r.raw:,.2f}"
                letter, _ = grade(r.score)
                trs += (f"<tr><td>{_e(r.name)}</td><td>{measure} {_e(r.unit)}</td>"
                        f"<td><b>{r.score:.0f}</b></td><td>{letter}</td>"
                        f"<td>{_html_bar(r.score)}</td></tr>")
            block.append('<div class="sub-h">Pruebas medidas</div><div class="tw"><table>'
                         + trs + "</table></div>")

        if card.findings:
            head = "Mejoras aplicables"
            if card.gain > 0.005:
                head += f' — <span class="gain">+{card.gain * 100:.0f}% combinado</span>'
                if card.projected_score is not None:
                    head += (f' <span style="color:var(--dim)">({card.score:.0f} → '
                             f"{card.projected_score:.0f} pts)</span>")
            items = ""
            for f in card.findings:
                gain = (f'<span class="gain">+{f.gain * 100:.0f}%</span>' if f.gain
                        else '<span style="color:var(--dim)">sin ganancia directa</span>')
                items += (f'<li>{gain} &nbsp; <a href="#h-{_e(f.id)}">{_e(f.title)}</a>'
                          f'<div class="tags"><span class="badge b-{f.severity}">{f.severity}'
                          f"</span> &nbsp; {_e(f.category)} · esfuerzo {_e(f.effort)} · riesgo "
                          f"{_e(f.risk)} · {_e(f.gain_note)}</div></li>")
            block.append(f'<div class="sub-h">{head}</div><ul class="imp">{items}</ul>')
            block.append(_howto_subcard(card))
        else:
            block.append('<div class="sub-h">Mejoras aplicables</div>'
                         '<p class="ok-note">Sin mejoras pendientes.</p>')

        block.append("</div>")
        parts.append("".join(block))
    return "".join(parts)


def _conditions_block(bench: Benchmark) -> str:
    """En qué condiciones se midió: qué más corría y cómo llegó el equipo al
    final de la carga.

    Las dos cosas se guardaban en el JSON y no se enseñaban en ninguna parte, así
    que el margen de una prueba salía marcado en amarillo sin que se pudiera ver
    la causa. La causa suele tener nombre y estar en esta tabla.
    """
    ambiente = getattr(bench, "ambient_load", {}) or {}
    fotos = getattr(bench, "load_snapshots", []) or []
    out = ""

    filas = ""
    for momento, datos in ambiente.items():
        culpables = ", ".join(f"{_e(n)} ({p:.0f}%)" for n, p in datos.get("top") or []) or "—"
        color = "var(--warn)" if datos.get("cpu_pct", 0) >= BUSY_CPU_PCT else "inherit"
        filas += (f"<tr><td>{_e(momento.capitalize())}</td>"
                  f'<td style="color:{color}"><b>{datos.get("cpu_pct", 0):.1f}%</b></td>'
                  f"<td>{culpables}</td></tr>")
    if filas:
        out += ('<div class="card"><div class="sub-h">Carga ajena durante la sesión</div>'
                f'<p class="scan-note">La {_term("carga ajena", "carga-ajena")} se mide con '
                "el benchmark parado, justo antes y justo después: lo que aparezca aquí lo "
                "consumía otro programa. Es lo que decide si esta ejecución se puede "
                "comparar con otra.</p><div class=\"tw\"><table>"
                "<tr><th>Momento</th><th>CPU ajena</th><th>Procesos que la usaban</th></tr>"
                + filas + "</table></div></div>")

    utiles = [f for f in fotos if any(f.get(k) is not None for k in
                                      ("cpu_mhz", "cpu_temp", "gpu_temp", "gpu_power_w"))]
    if utiles:
        def celda(foto: dict, clave: str, fmt: str) -> str:
            valor = foto.get(clave)
            return format(valor, fmt) if valor is not None else "—"

        filas = ""
        for foto in utiles:
            fuente = foto.get("cpu_mhz_source") or ""
            nota = f'<div class="tags">{_e(fuente)}</div>' if fuente else ""
            filas += (f"<tr><td>{_e(str(foto.get('moment') or '—').capitalize())}{nota}</td>"
                      f"<td>{celda(foto, 'cpu_mhz', '.0f')} MHz</td>"
                      f"<td>{celda(foto, 'cpu_temp', '.0f')} °C</td>"
                      f"<td>{celda(foto, 'gpu_temp', '.0f')} °C</td>"
                      f"<td>{celda(foto, 'gpu_power_w', '.0f')} W</td></tr>")
        out += ('<div class="card"><div class="sub-h">Sensores antes y después de la carga'
                '</div><p class="scan-note">La misma foto en dos momentos. Si la frecuencia '
                "baja o la temperatura se dispara entre una y otra, el equipo se está "
                "limitando solo y la nota de la prueba larga lo refleja.</p>"
                '<div class="tw"><table><tr><th>Momento</th><th>CPU</th><th>Temp. CPU</th>'
                "<th>Temp. GPU</th><th>Consumo GPU</th></tr>" + filas + "</table></div></div>")
    return out


def _metrics_block(bench: Benchmark) -> str:
    """Métricas que no puntúan pero explican el porqué de la puntuación."""
    if not bench.metrics and not bench.memory_hierarchy:
        return ""
    out = ""
    if bench.memory_hierarchy:
        peak = max(lv["gbs"] for lv in bench.memory_hierarchy) or 1
        trs = ""
        for lv in bench.memory_hierarchy:
            trs += (f"<tr><td><b>{_e(lv['level'])}</b></td><td>bloques de "
                    f"{_human(lv['size'])}</td><td>{lv['gbs']:.1f} GB/s</td>"
                    f'<td><div class="track"><div class="fill" '
                    f'style="width:{lv["gbs"] / peak * 100:.0f}%;background:var(--acc)">'
                    f"</div></div></td></tr>")
        out += ('<div class="card"><div class="sub-h">Jerarquía de memoria</div>'
                '<p class="scan-note">Ancho de banda de copia según el tamaño del bloque. '
                'Mientras el dato cabe en caché la cifra se mantiene; la caída final es el '
                'salto a la RAM.</p><div class="tw"><table>' + trs + "</table></div></div>")
    if bench.metrics:
        trs = ""
        for m in bench.metrics.values():
            value = f"{m['value']} {m['unit']}".strip()
            note = f'<div class="tags">{_e(m["note"])}</div>' if m["note"] else ""
            trs += f"<tr><td>{_e(m['label'])}{note}</td><td><b>{_e(value)}</b></td></tr>"
        out += ('<div class="card"><div class="sub-h">Métricas de diagnóstico</div>'
                '<div class="tw"><table>' + trs + "</table></div></div>")
    return out


def _system_state_block(auditor: Auditor, bench: Benchmark | None) -> str:
    """Estado del sistema que la auditoría mide pero no cabía en un hallazgo:
    arranque, programas de inicio uno a uno, procesos residentes y de dónde salió
    (o no) cada sensor."""
    out = []

    # Va la primera: define hasta dónde llega lo que el resto del informe afirma.
    sin_datos = getattr(auditor, "unverified", [])
    no_aplican = getattr(auditor, "not_applicable", [])
    if sin_datos or no_aplican:
        cuerpo = ""
        if sin_datos:
            filas = "".join(f"<tr><td>{_e(c)}</td><td>{_e(r)}</td></tr>" for c, r in sin_datos)
            cuerpo += ('<div class="tw"><table><tr><th>Comprobación</th>'
                       "<th>Por qué no hay veredicto</th></tr>" + filas + "</table></div>")
        if no_aplican:
            cuerpo += ('<p class="scan-note">No aplican a este equipo: '
                       + ", ".join(f"{_e(c)} ({_e(r)})" for c, r in no_aplican) + ".</p>")
        out.append('<div class="card"><div class="sub-h">Cobertura de la auditoría</div>'
                   f'<p class="scan-note">La {_term("cobertura", "cobertura")} de esta '
                   f"ejecución es de {auditor.checks_run} sobre "
                   f'{getattr(auditor, "checks_total", auditor.checks_run)} comprobaciones. '
                   "Las de abajo no llegaron a un veredicto: no significan «correcto», "
                   "significan que no hay dato con el que opinar.</p>" + cuerpo + "</div>")

    boot = getattr(auditor, "boot_report", {}) or {}
    segundos = getattr(auditor, "boot_seconds", None)
    if segundos or boot.get("error"):
        if segundos:
            arranques = len([b for b in boot.get("boots", [])
                             if str(b["fields"].get("BootIsRebootAfterInstall") or "0") != "1"])
            cuerpo = (f'<div class="kvs"><div class="k">Duración</div>'
                      f"<div><b>{segundos:.0f} s</b> de mediana sobre {arranques} arranques</div>")
            # El mismo lector que usa la auditoría, y por el mismo motivo: los
            # campos vienen de un XML del registro de eventos y uno puede llegar
            # vacío o con texto donde debería haber milisegundos. `_event_ms`
            # descarta lo que no sea un número en vez de reventar el informe
            # entero por un elemento de arranque mal declarado.
            retrasos = sorted(
                ((str(d["fields"].get("Name") or d["fields"].get("FriendlyName") or ""),
                  Auditor._event_ms(d["fields"], "TotalTime", "DegradationTime"),
                  d.get("kind") or "") for d in boot.get("delays", [])),
                key=lambda x: -(x[1] or 0.0))
            vistos, filas = set(), ""
            for nombre, ms, tipo in retrasos:
                if not nombre or nombre in vistos or not ms:
                    continue
                vistos.add(nombre)
                filas += (f"<tr><td>{_e(nombre)}</td><td>{_e(tipo)}</td>"
                          f"<td>{ms / 1000:.1f} s</td></tr>")
                if len(vistos) >= 12:
                    break
            cuerpo += "</div>"
            if filas:
                cuerpo += ('<div class="sub-h">Lo que más lo retrasa</div><div class="tw">'
                           "<table><tr><th>Elemento</th><th>Tipo</th><th>Retraso</th></tr>"
                           + filas + "</table></div>")
        else:
            cuerpo = ('<p class="scan-note">No se ha podido medir: '
                      f"{_e(boot['error'])}.</p>")
        out.append('<div class="card"><div class="sub-h">Arranque medido por Windows</div>'
                   '<p class="scan-note">Lo cronometra el propio sistema en cada encendido. '
                   "Se descartan los reinicios por actualización, que siempre son lentos.</p>"
                   + cuerpo + "</div>")

    startup = getattr(auditor, "startup_items", [])
    if startup:
        filas = ""
        for item in sorted(startup, key=lambda i: (not i.get("enabled"), i["name"].lower())):
            activo = item.get("enabled")
            estado = ('<span class="badge b-medium">activo</span>' if activo
                      else '<span class="badge b-info">desactivado</span>')
            filas += (f"<tr><td>{_e(item['name'])}</td>"
                      f"<td>{_e(item.get('location') or '')}</td><td>{estado}</td></tr>")
        activos = sum(1 for i in startup if i.get("enabled"))
        out.append('<div class="card"><div class="sub-h">Programas de inicio</div>'
                   f'<p class="scan-note">{activos} activos de {len(startup)}. Los desactivados '
                   "siguen registrados pero Windows no los ejecuta.</p><div class=\"tw\">"
                   "<table><tr><th>Programa</th><th>Origen</th><th>Estado</th></tr>"
                   + filas + "</table></div></div>")

    procesos = getattr(auditor, "top_processes", [])
    if procesos:
        filas = "".join(f"<tr><td>{_e(p['name'])}</td><td>{_human(p['rss'])}</td></tr>"
                        for p in procesos)
        out.append('<div class="card"><div class="sub-h">Procesos que más memoria ocupan</div>'
                   '<div class="tw"><table><tr><th>Proceso</th><th>Memoria</th></tr>'
                   + filas + "</table></div></div>")

    intentos = temperature_report()
    if intentos:
        fuente = temperature_source()
        filas = "".join(f"<tr><td>{_e(s)}</td><td>{_e(r)}</td></tr>" for s, r in intentos)
        out.append('<div class="card"><div class="sub-h">Sensores de temperatura</div>'
                   + (f'<p class="scan-note">Fuente en uso: <b>{_e(fuente)}</b>.</p>' if fuente
                      else '<p class="scan-note">Ninguna fuente respondió. Leer la temperatura '
                           "de CPU en Windows exige un driver en modo kernel; instalar "
                           "LibreHardwareMonitor y dejarlo abierto la hace accesible.</p>")
                   + '<div class="tw"><table><tr><th>Fuente</th><th>Resultado</th></tr>'
                   + filas + "</table></div></div>")

    if bench:
        filas = ""
        for k, v in REFERENCE.items():
            origen = REFERENCE_ORIGIN.get(k, "")
            filas += (f"<tr><td>{_e(k)}</td><td>{v}</td>"
                      f'<td class="tags">{_e(origen)}</td></tr>')
        caducada = ""
        if reference_is_stale():
            caducada = ('<p class="scan-note" style="color:var(--warn)">La escala se fijó hace '
                        f"{reference_age_months() / 12:.1f} años. Sigue midiendo bien, pero "
                        "«100 puntos = gama media» ya no describe lo que se vende hoy: "
                        "la nota está inflada respecto a un equipo actual.</p>")
        out.append('<div class="card"><div class="sub-h">Escala de referencia</div>'
                   f'<p class="scan-note">Los 100 puntos de la '
                   f'{_term("referencia", "referencia")} equivalen a estos valores, fijados '
                   f"en <b>{_e(REFERENCE_DATE)}</b> sobre un equipo tipo "
                   f"{_e(REFERENCE_MACHINE)}. "
                   "Los tiempos van en segundos; el resto, en su unidad.</p>"
                   + caducada + '<div class="tw">'
                   "<table><tr><th>Prueba</th><th>Referencia</th><th>De dónde sale</th></tr>"
                   + filas + "</table></div></div>")
    return "".join(out)


def _network_block(red: dict[str, Any]) -> str:
    """Enlace de red. Sin SSID, sin BSSID y sin MAC: identifican la red y el
    equipo, no dicen nada del rendimiento, y esto se comparte."""
    if not red.get("adapters"):
        return ""
    filas = ""
    for a in red["adapters"]:
        velocidad = f"{a['link_mbps']:,.0f} Mbps" if a.get("link_mbps") else "—"
        conectado = a["status"].lower() in ("up", "conectado")
        estado = (f'<span class="badge b-{"low" if conectado else "info"}">'
                  f'{_e(a["status"])}</span>')
        filas += (f"<tr><td>{_e(a['name'])}</td><td>{_e(a['description'])}</td>"
                  f"<td>{velocidad}</td><td>{estado}</td></tr>")
    out = ('<div class="card"><div class="sub-h">Adaptadores</div><div class="tw">'
           "<table><tr><th>Interfaz</th><th>Adaptador</th><th>Enlace</th><th>Estado</th></tr>"
           + filas + "</table></div></div>")

    wifi = red.get("wifi") or {}
    if wifi:
        kv = [("Generación del enlace", wifi.get("radio", "—")),
              ("Velocidad negociada", f"{wifi['rate_mbps']:,.0f} Mbps"
               if wifi.get("rate_mbps") else "—"),
              ("Banda", f"{wifi['band_ghz']} GHz" if wifi.get("band_ghz") else "—"),
              ("Canal", str(wifi.get("channel") or "—")),
              ("Señal", f"{wifi.get('rssi_dbm', '—')} dBm"
                        + (f" ({wifi['signal_pct']}%)" if wifi.get("signal_pct") else ""))]
        out += ('<div class="card"><div class="sub-h">Enlace wifi</div>'
                '<p class="scan-note">No se recogen el nombre de la red, el punto de '
                "acceso ni la dirección física: identifican tu red y no aportan nada "
                'sobre el rendimiento.</p><div class="kvs">'
                + "".join(f'<div class="k">{_e(k)}</div><div>{_e(v)}</div>' for k, v in kv)
                + "</div></div>")

    latencia = red.get("latency") or {}
    if latencia.get("targets"):
        filas = ""
        for t in latencia["targets"]:
            medida = f"{t['median_ms']:.1f} ms" if t["median_ms"] else "sin respuesta"
            jitter = f"{t['jitter_ms']:.1f} ms" if t.get("jitter_ms") else "—"
            filas += (f"<tr><td>{_e(t['name'])}</td><td>{_e(t['host'])}</td>"
                      f"<td>{medida}</td><td>{jitter}</td><td>{t['loss_pct']}%</td></tr>")
        dns = red.get("dns") or {}
        nota = (f'<p class="scan-note">Resolución DNS: <b>{dns["median_ms"]:.1f} ms</b> '
                f'de mediana.</p>' if dns.get("median_ms") else "")
        out += ('<div class="card"><div class="sub-h">Latencia</div>'
                '<p class="scan-note">Tiempo del saludo TCP contra resolutores DNS '
                "públicos. Mide el camino hasta internet, no la velocidad de bajada.</p>"
                + nota + '<div class="tw"><table><tr><th>Destino</th><th>Dirección</th>'
                "<th>Latencia</th><th>Variación</th><th>Pérdida</th></tr>"
                + filas + "</table></div></div>")
    elif not red.get("active"):
        out += ('<div class="card"><p class="scan-note">La latencia y el DNS no se han '
                "medido: esta ejecución usó <code>--no-net</code>, que impide abrir "
                "conexiones a servidores de terceros.</p></div>")
    return out


def _storage_scan_block(scan: ScanResult) -> str:
    safe, review = candidate_bytes(scan)
    head = (f'<p class="scan-note">Umbral {_human(scan.min_size)} · '
            f"{scan.scanned_files:,} ficheros y {scan.scanned_dirs:,} carpetas revisados en "
            f"{scan.elapsed:.0f} s".replace(",", ".") + "</p>")
    if scan.truncated:
        head += ('<p class="scan-note" style="color:var(--warn)">Rastreo parcial: se agotó el '
                 "presupuesto de tiempo. Amplíalo con <code>--scan-time 60</code>.</p>")
    if not scan.files and not scan.special:
        return head + '<p class="ok-note">Nada por encima del umbral.</p>'

    out = f'<div class="card">{head}'
    if scan.by_category:
        rows = ""
        biggest = max(v["size"] for v in scan.by_category.values()) or 1
        for cat, data in scan.by_category.items():
            if cat in RECLAIMABLE:
                tag = '<span class="badge b-low">se puede borrar</span>'
                color = "var(--ok)"
            elif cat in REVIEWABLE:
                tag = '<span class="badge b-medium">revisar</span>'
                color = "var(--warn)"
            else:
                tag = ""
                color = "var(--dim)"
            files = data.get("files") or []
            listado = ""
            for f in files:
                listado += (f"<tr><td>{_human(f['size'])}</td><td>{f['age_days']} días</td>"
                            f'<td class="pathcell">{_e(f["path"])}</td></tr>')
            restantes = data["count"] - len(files)
            pie = (f'<p class="scan-note">…y {restantes} más de esta categoría.</p>'
                   if restantes > 0 else "")
            # Cada categoría se despliega para ver exactamente qué hay dentro:
            # decidir si sobra un grupo entero solo se puede con los nombres.
            rows += (
                f'<details class="cat"><summary>'
                f'<span class="cat-name">{_e(cat.capitalize())}</span>'
                f'<span class="cat-size">{_human(data["size"])}</span>'
                f'<span class="cat-count">{data["count"]} ficheros</span>'
                f"<span class=\"cat-tag\">{tag}</span>"
                f'<span class="track cat-bar"><span class="fill" '
                f'style="width:{data["size"] / biggest * 100:.0f}%;background:{color}"></span>'
                f"</span>"
                f'{_icon("i-chev", "ic chev")}</summary>'
                f'<div class="cat-body"><div class="tw"><table>'
                f"<tr><th>Tamaño</th><th>Sin tocar</th><th>Ruta</th></tr>{listado}</table></div>"
                f"{pie}</div></details>")
        out += ('<div class="sub-h">Por tipo</div>'
                '<p class="scan-note">Despliega una categoría para ver qué ficheros la '
                "componen.</p>" + rows
                + f'<p class="scan-note" style="margin-top:14px">Total '
                  f'{_human(scan.total_large)}: <span class="gain">{_human(safe)}</span> es '
                  f'basura y <span style="color:var(--warn)">{_human(review)}</span> son '
                  f"candidatos a revisar antes de borrar.</p>")
    out += "</div>"

    if scan.files:
        trs = ""
        for f in scan.files:
            trs += (f"<tr><td>{_human(f['size'])}</td><td>{_e(f['category'])}</td>"
                    f"<td>{f['age_days']} días</td>"
                    f'<td class="pathcell">{_e(f["path"])}</td></tr>')
        out += ('<div class="card"><div class="sub-h">Los ficheros más grandes</div>'
                '<div class="tw"><table><tr><th>Tamaño</th><th>Tipo</th><th>Sin tocar</th>'
                "<th>Ruta</th></tr>" + trs + "</table></div></div>")

    if scan.special:
        trs = ""
        for s in scan.special:
            trs += (f"<tr><td>{_e(s['name'])}</td>"
                    f"<td>{_human(s['size']) if s['size'] else '—'}</td>"
                    f"<td>{_e(s['note'])}</td></tr>")
        out += ('<div class="card"><div class="sub-h">Archivos de sistema</div>'
                '<p class="scan-note">Ocupan mucho y tienen su función: no se borran a mano.'
                '</p><div class="tw"><table><tr><th>Archivo</th><th>Tamaño</th><th>Qué es</th>'
                "</tr>" + trs + "</table></div></div>")
    return out


def _projection_tables(projection: dict[str, Any]) -> str:
    trs = ""
    for k, cur in projection["current_components"].items():
        gain = projection["component_gain"].get(k, 0.0)
        proj = projection["projected_components"][k]
        trs += (f"<tr><td>{COMPONENT_LABELS.get(k, k)}</td><td>{cur:.0f} pts</td>"
                f"<td>{proj:.0f} pts</td>"
                f"<td class=\"gain\">{'+' + format(gain * 100, '.0f') + '%' if gain else '—'}</td>"
                f"<td>{_html_bar(proj)}</td></tr>")
    sysgain = projection.get("system_gain", 0.0)
    if sysgain:
        trs += (f"<tr><td>Arranque / fluidez</td><td colspan=2>sin métrica sintética</td>"
                f'<td class="gain">+{sysgain * 100:.0f}%</td>'
                f"<td>{_html_bar(sysgain * 100, ref=False)}</td></tr>")
    out = ('<div class="card"><div class="tw"><table>'
           "<tr><th>Componente</th><th>Ahora</th><th>Optimizado</th><th>Ganancia</th><th></th>"
           "</tr>" + trs + "</table></div></div>")

    cats = ""
    for cat, gain in sorted(projection.get("category_gain", {}).items(), key=lambda x: -x[1]):
        cats += (f"<tr><td>{_e(cat.capitalize())}</td>"
                 f'<td class="gain">+{gain * 100:.0f}%</td>'
                 f"<td>{_html_bar(min(100, gain * 100), ref=False)}</td></tr>")
    if cats:
        out += ('<div class="card"><div class="sub-h">Margen por área</div><div class="tw">'
                "<table>" + cats + "</table></div></div>")
    return out


def _worst(findings: list) -> str:
    """La severidad más grave de un grupo de hallazgos, o cadena vacía."""
    if not findings:
        return ""
    return min((f.severity for f in findings), key=lambda s: SEVERITY_ORDER.get(s, 9))


def _search_hints(auditor: Auditor, cards: list[ComponentCard]) -> list[str]:
    """Ejemplos de filtro sacados de ESTE informe.

    Un campo de búsqueda vacío no dice qué se puede buscar, y una lista de
    ejemplos escrita a mano acabaría proponiendo cosas que en este equipo no
    aparecen: buscar «wifi» en un sobremesa por cable no devuelve nada y hace
    parecer que el buscador no funciona. Salen de lo que el informe contiene.
    """
    vistos: list[str] = []

    def añadir(*terminos: str) -> None:
        for t in terminos:
            t = (t or "").strip().lower()
            if t and t not in vistos:
                vistos.append(t)

    # Las categorías de los hallazgos son lo que la gente busca: «arranque»,
    # «térmico», «almacenamiento».
    añadir(*[f.category for f in auditor.findings])
    scan = getattr(auditor, "scan", None)
    if scan is not None and getattr(scan, "by_category", None):
        añadir(*list(scan.by_category)[:3])
    if (getattr(auditor, "network", {}) or {}).get("wifi"):
        añadir("wifi")
    # Componentes con nota: sirven para saltar a sus filas en cualquier tabla.
    añadir(*[c.label for c in cards if c.score is not None])
    return vistos[:7]


def export_html(path: Path, si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                projection: dict[str, Any]) -> None:
    # El glosario marca solo la primera aparición de cada término, así que su
    # memoria es por informe: sin esto, generar dos seguidos dejaría el segundo
    # sin ninguna explicación.
    _TERMINOS_VISTOS.clear()

    cards = build_component_cards(si, bench, auditor)
    findings = sorted(auditor.findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.gain))
    actionable = sorted([f for f in auditor.findings if f.gain > 0], key=priority_rank)

    secs: list[Seccion] = [
        Seccion("inventario", "Inventario", "i-box", _inventory(si)),
    ]
    if bench and bench.results:
        secs.append(Seccion("benchmark", "Benchmark", "i-chart",
                            _benchmark_table(bench) + _metrics_block(bench)
                            + _conditions_block(bench),
                            f"{len(bench.results)} pruebas"))
    secs.append(Seccion("componentes", "Ficha por componente", "i-cpu",
                        _html_component_cards(cards), f"{len(cards)} componentes",
                        _worst(auditor.findings), len(auditor.findings)))

    estado = _system_state_block(auditor, bench)
    if estado:
        arranque = getattr(auditor, "boot_seconds", None)
        secs.append(Seccion("estado", "Estado del sistema", "i-clock", estado,
                            f"arranque {arranque:.0f} s" if arranque else ""))

    red = _network_block(getattr(auditor, "network", {}) or {})
    if red:
        wifi = (getattr(auditor, "network", {}) or {}).get("wifi") or {}
        de_red = [c for c in cards if c.key == "network"]
        hallazgos_red = de_red[0].findings if de_red else []
        secs.append(Seccion("red", "Red", "i-net", red, wifi.get("radio", ""),
                            _worst(hallazgos_red), len(hallazgos_red)))

    scan = getattr(auditor, "scan", None)
    if scan is not None and scan.available and (scan.files or scan.special):
        safe, review = candidate_bytes(scan)
        secs.append(Seccion("archivos", "Archivos grandes", "i-folder",
                            _storage_scan_block(scan),
                            f"{_human(safe + review)} prescindibles"))
    if projection.get("current_components"):
        secs.append(Seccion("proyeccion", "Proyección de mejora", "i-trend",
                            _projection_tables(projection),
                            f"+{projection.get('headroom_pct', 0.0):.0f}% sintético"))

    # Antes del plan y fuera de él: el plan ordena por retorno dividido por
    # esfuerzo, y estos hallazgos no tienen retorno que ordenar.
    riesgos = security_findings(auditor.findings)
    if riesgos:
        tarjetas = ""
        for f in riesgos:
            pasos = "".join(f"<li>{_e(p)}</li>" for p in f.steps)
            tarjetas += (f'<div class="card finding" id="s-{_e(f.id)}">'
                         f"<h3>{_e(f.title)}</h3>"
                         f'<div class="tags">'
                         f'<span class="badge b-{_e(f.severity)}">{_e(f.severity)}</span>'
                         f" &nbsp; esfuerzo {_e(f.effort)} · riesgo {_e(f.risk)}</div>"
                         f"<p>{_e(f.detail)}</p>"
                         + (f"<ul>{pasos}</ul>" if pasos else "") + "</div>")
        secs.append(Seccion("seguridad", "Seguridad", "i-alert",
                            '<p class="hint">Esto no acelera el equipo: son riesgos. No '
                            "entran en la proyección de mejora porque no hay mejora que "
                            "proyectar.</p>" + tarjetas,
                            f"{len(riesgos)} riesgos",
                            _worst(riesgos), len(riesgos)))

    if actionable:
        trs = ""
        for i, f in enumerate(actionable, 1):
            trs += (f'<tr><td>{i}</td><td><a href="#h-{_e(f.id)}">{_e(f.title)}</a></td>'
                    f'<td class="gain">+{f.gain * 100:.0f}%</td>'
                    f"<td>{_e(f.effort)}</td><td>{_e(f.risk)}</td>"
                    f'<td><span class="badge b-{f.severity}">{f.severity}</span></td></tr>')
        secs.append(Seccion("plan", "Plan de acción priorizado", "i-list",
                            '<div class="card"><div class="tw"><table>'
                            "<tr><th>#</th><th>Acción</th><th>Ganancia est.</th>"
                            "<th>Esfuerzo</th><th>Riesgo</th><th>Severidad</th></tr>"
                            + trs + "</table></div>"
                            '<p class="tags">Ordenado por retorno estimado dividido por '
                            "esfuerzo. Aplica de arriba hacia abajo y vuelve a medir tras "
                            "cada bloque.</p></div>",
                            f"{len(actionable)} acciones",
                            _worst(actionable), len(actionable)))

    if findings:
        # Aquí va el diagnóstico, no el procedimiento: los pasos viven en la
        # ficha del componente, dentro de «Cómo aplicar estas mejoras». Repetirlos
        # obligaría a mantener dos copias y alargaría esta sección sin aportar.
        labels = {c.key: c.label for c in cards}
        detail = ""
        for f in findings:
            gain = (f'<span class="gain">Mejora estimada +{f.gain * 100:.0f}%</span> '
                    f'<span style="color:var(--dim)">({_e(f.gain_note)})</span>') if f.gain else ""
            group = finding_group(f)
            enlace = ""
            if f.steps and group in labels:
                enlace = (f'<p class="steps-link"><a href="#c-{_e(group)}">{_icon("i-wrench")}'
                          f"Los {len(f.steps)} pasos para solucionarlo están en la ficha de "
                          f"{_e(labels[group])}</a></p>")
            detail += (f'<div class="card finding" id="h-{_e(f.id)}"><h3>{_e(f.title)}</h3>'
                       f'<div class="tags"><span class="badge b-{f.severity}">{f.severity}</span>'
                       f" &nbsp; {_e(f.category)} · esfuerzo {_e(f.effort)} · riesgo "
                       f"{_e(f.risk)} &nbsp; {gain}</div><p>{_e(f.detail)}</p>{enlace}</div>")
        secs.append(Seccion("hallazgos", "Hallazgos en detalle", "i-alert", detail,
                            f"{len(findings)} hallazgos",
                            _worst(findings), len(findings)))

    verdict, extra = build_verdict(si, bench, auditor, projection)
    extras = "".join(f"<li>{_e(x)}</li>" for x in extra)
    secs.append(Seccion("veredicto", "Veredicto", "i-award",
                        f'<div class="card verdict">{_logo("brandmark wm", uid="ver")}'
                        f"<p>{_e(verdict)}</p>"
                        f'{"<ul>" + extras + "</ul>" if extras else ""}</div>'))

    nav = f'<a href="#resumen" data-target="resumen">{_icon("i-zap")}Resumen</a>'
    for s in secs:
        # Punto de severidad en la propia navegación: dónde está lo urgente sin
        # tener que entrar a mirar sección por sección.
        punto = f'<span class="sev s-{s.severity}"></span>' if s.severity else ""
        nav += (f'<a href="#{s.sid}" data-target="{s.sid}" title="{_e(s.label)}">'
                f"{_icon(s.icon)}{_e(NAV_LABELS.get(s.sid, s.label))}{punto}</a>")
    body = "".join(_section(s) for s in secs)

    # Bandeja de selección: mientras se eligen secciones hay que poder ver
    # cuáles van y quitar una sin salir a buscarla por la página.
    presets = (
        f'<div class="presets">'
        f'<button type="button" data-preset="all">{_icon("i-check", "ic sm")}Todo</button>'
        f'<button type="button" data-preset="none">{_icon("i-x", "ic sm")}Ninguno</button>'
        f'<button type="button" data-preset="accion">{_icon("i-alert", "ic sm")}'
        f"Solo lo accionable</button>"
        f'<button type="button" data-preset="tecnico">{_icon("i-layers", "ic sm")}'
        f"Solo diagnóstico técnico</button></div>"
    )
    tray = (
        f'<aside class="tray" id="tray" aria-label="Secciones seleccionadas para exportar">'
        f'<h4>{_icon("i-download", "ic sm")}Se exportarán'
        f'<span class="n" id="tray-count">0</span></h4>'
        f'<ul id="tray-list"></ul>{presets}'
        f'<div class="row"><button class="btn gold" type="button" id="tray-export">'
        f'{_icon("i-download")}Descargar</button>'
        f'<button class="btn" type="button" data-preset="none">Vaciar</button></div></aside>'
    )

    # Ejemplos de filtro: como <datalist> para quien use el teclado, y como
    # botones al enfocar el campo para quien no sepa que hay algo que escribir.
    sugerencias = _search_hints(auditor, cards)
    lista_sugerencias = ""
    chips_sugerencias = ""
    if sugerencias:
        lista_sugerencias = ('<datalist id="sugerencias">'
                             + "".join(f'<option value="{_e(s)}">' for s in sugerencias)
                             + "</datalist>")
        chips_sugerencias = (
            '<div class="tips"><div class="th">Prueba a filtrar por</div><div class="tr">'
            + "".join(f'<button type="button" data-tip="{_e(s)}">{_e(s)}</button>'
                      for s in sugerencias)
            + "</div></div>")

    date = f"{datetime.now():%d/%m/%Y %H:%M}"
    html = (
        '<!DOCTYPE html>\n<html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Informe de rendimiento · {_e(si.hostname)}</title>"
        f"{_favicon()}"
        f"<style>{HTML_CSS}</style></head>"
        f'<body data-host="{_e(_slug(si.hostname))}" data-stamp="{date}">'
        f"{_sprite()}"
        '<header class="topbar"><div class="prog"></div><div class="tb">'
        f'<a class="logo" href="#resumen" data-target="resumen">{_logo(uid="nav")}'
        "<span>Quilate <b>Suite</b></span></a>"
        f"<nav>{nav}</nav>"
        f'<div class="find">{_icon("i-search", "ic sm")}'
        '<label class="vh" for="buscar">Filtrar el informe por texto</label>'
        '<input id="buscar" type="search" placeholder="Filtrar el informe…" '
        'autocomplete="off" list="sugerencias">'
        f"{lista_sugerencias}"
        '<span class="cnt" id="buscar-cnt"></span>'
        f"{chips_sugerencias}</div>"
        '<button class="btn" type="button" id="export-sel" disabled '
        'title="Marca secciones con su casilla para exportarlas juntas en un solo fichero">'
        f'{_icon("i-download")}<span>Exportar (0)</span></button>'
        '<button class="btn" type="button" id="toggle-all" data-open="1">'
        f'{_icon("i-list")}<span>Colapsar todo</span></button>'
        "</div>"
        f'<div class="crumb"><div class="cin">{_icon("i-chev", "ic sm")}'
        '<span>Estás en</span><b id="crumb-name">Resumen</b>'
        '<span class="pct" id="crumb-pct">0% leído</span></div></div>'
        "</header>"
        '<div class="layout">'
        f"{_sidebar(si, bench, auditor, projection, secs)}"
        '<main class="main">'
        '<header class="page">'
        f'{_logo("brandmark hero", uid="cab")}'
        '<div class="htxt"><div class="kicker">Quilate Suite</div>'
        "<h1>Informe de rendimiento y optimización</h1>"
        f'<div class="meta">{_e(si.hostname)} · {_e(si.os_name)} · generado el {date}</div>'
        "</div></header>"
        f"{_hero(bench, auditor, projection)}{_component_strip(bench)}{body}"
        "</main></div>"
        f'<footer><span class="fbrand">{_logo("brandmark foot", uid="pie")}'
        f"{_e(APP_NAME)} v{APP_VERSION} · escala de referencia: "
        f"100 pts = gama media reciente</span>"
        f'<span>{_e(AUTHOR)} — <a href="{WEBSITE_URL}">{_e(WEBSITE)}</a></span></footer>'
        f"{tray}"
        '<button class="btn" type="button" id="top" title="Volver arriba" '
        "onclick=\"window.scrollTo({top:0,behavior:'smooth'})\">"
        f'{_icon("i-up")}</button>'
        f"<script>{HTML_JS}</script></body></html>"
    )
    path.write_text(html, encoding="utf-8")
