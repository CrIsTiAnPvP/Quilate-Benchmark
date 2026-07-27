"""Exportacion a HTML: informe autocontenido, sin recursos externos.

Todo va embebido (CSS, iconos SVG y un puñado de JS) para que el fichero se
pueda enviar por correo o abrir sin conexion y siga funcionando igual.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from ..audit import Auditor, SEVERITY_ORDER
from ..benchmark import Benchmark, REFERENCE
from ..sensors import temperature_report, temperature_source
from ..components import (ComponentCard, _no_score_text, build_component_cards,
                          finding_group)
from ..console import grade
from ..const import APP_NAME, APP_VERSION, AUTHOR, WEBSITE, WEBSITE_URL
from ..projection import priority_rank
from ..report import build_verdict
from ..storage_scan import (RECLAIMABLE, REVIEWABLE, ScanResult, candidate_bytes)
from ..sysinfo import KIND_LABELS, SystemInfo, gpu_label, primary_gpu

COMPONENT_LABELS = {"cpu_single": "CPU monohilo", "cpu_multi": "CPU multihilo",
                    "memory": "Memoria", "disk": "Almacenamiento"}

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
}

COMPONENT_ICONS = {"cpu": "i-cpu", "memory": "i-ram", "disk": "i-disk",
                   "gpu": "i-gpu", "system": "i-sys"}

# La barra de navegación se queda sin sitio con los títulos completos y acaba
# recortando las últimas entradas: ahí van estos nombres cortos.
NAV_LABELS = {"componentes": "Componentes", "proyeccion": "Proyección",
              "plan": "Plan de acción", "hallazgos": "Hallazgos"}

HTML_CSS = """
:root{--bg:#0d1117;--card:#161b22;--card2:#1b2028;--line:#262d38;--txt:#e6edf3;
--dim:#8b949e;--acc:#58a6ff;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--brand:#e8b33e;
--brand-dark:#8a6a1e;
--nav:56px}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--txt);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
a{color:var(--acc)}
svg.ic{width:16px;height:16px;flex:none;fill:none;stroke:currentColor;stroke-width:2;
stroke-linecap:round;stroke-linejoin:round}
svg.ic.lg{width:20px;height:20px}

/* barra de navegación */
.topbar{position:sticky;top:0;z-index:50;background:rgba(13,17,23,.93);
backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
/* Altura variable: con muchas secciones la navegación se repartía en una fila
   con scroll horizontal y la barra oculta, así que los últimos enlaces quedaban
   fuera de la vista sin ninguna pista de que seguían ahí. Ahora envuelve.
   OJO: la altura mínima va en píxeles fijos a propósito. Con min-height:var(--nav)
   la barra se dimensionaba con la variable que el JS calcula midiéndola, y como
   offsetHeight incluye el borde inferior cada medida salía un píxel más alta que
   la anterior: la barra crecía sin parar. --nav es solo salida, nunca entrada. */
.tb{max-width:1300px;margin:0 auto;min-height:40px;display:flex;align-items:center;
flex-wrap:wrap;gap:10px 16px;padding:8px 20px}
.tb .logo{font-weight:800;letter-spacing:-.3px;white-space:nowrap;font-size:15px;
display:flex;align-items:center;gap:8px}
.tb .logo b{color:var(--brand)}
.brandmark{width:22px;height:22px;flex:none;display:block}
.brandmark.foot{width:16px;height:16px;vertical-align:-3px;margin-right:7px}
.fbrand{display:flex;align-items:center}
.tb nav{display:flex;gap:2px;flex:1 1 420px;flex-wrap:wrap}
.tb nav a{display:flex;align-items:center;gap:6px;padding:7px 10px;border-radius:8px;
color:var(--dim);text-decoration:none;font-size:13px;white-space:nowrap;transition:.15s}
.tb nav a:hover{color:var(--txt);background:var(--card)}
.tb nav a.active{color:var(--acc);background:rgba(88,166,255,.13)}
.btn{background:var(--card);color:var(--dim);border:1px solid var(--line);border-radius:8px;
padding:7px 12px;font:inherit;font-size:12px;cursor:pointer;white-space:nowrap;
display:flex;align-items:center;gap:6px}
.btn:hover{color:var(--txt);border-color:var(--acc)}

/* estructura */
.layout{max-width:1300px;margin:0 auto;padding:26px 20px 72px;display:grid;
grid-template-columns:286px minmax(0,1fr);gap:26px;align-items:start}
.side{position:sticky;top:calc(var(--nav) + 20px);display:flex;flex-direction:column;gap:14px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.panel .lbl{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:1.2px;
font-weight:700;margin-bottom:10px}
.panel .big{font-size:46px;font-weight:800;letter-spacing:-2px;line-height:1}
.panel .big span{font-size:15px;font-weight:600;letter-spacing:0;color:var(--dim);margin-left:6px}
.panel .sub{color:var(--dim);font-size:13px;margin-top:4px}
.panel .delta{margin-top:12px;font-size:13px;color:var(--dim);border-top:1px solid var(--line);
padding-top:10px}
.mini{list-style:none;margin:0;padding:0;font-size:13px}
.mini li{display:flex;gap:9px;align-items:flex-start;padding:5px 0}
.mini li svg{color:var(--dim);margin-top:4px}
.mini li span{min-width:0;overflow-wrap:anywhere}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.hint{margin-top:12px;font-size:12px;color:var(--dim);border-top:1px solid var(--line);
padding-top:10px}
header.page{margin-bottom:22px}
header.page h1{margin:0 0 6px;font-size:27px;letter-spacing:-.6px}
header.page .meta{color:var(--dim);font-size:13px}

/* secciones colapsables */
details.sec{background:var(--card);border:1px solid var(--line);border-radius:12px;
margin-bottom:16px;scroll-margin-top:calc(var(--nav) + 14px)}
details.sec>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:10px;
padding:15px 18px;font-size:12px;text-transform:uppercase;letter-spacing:1.3px;font-weight:700;
color:var(--acc);user-select:none;border-radius:12px}
details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary:hover{background:rgba(88,166,255,.05)}
details.sec>summary .cnt{margin-left:auto;color:var(--dim);font-size:11px;letter-spacing:.4px;
text-transform:none;font-weight:600}
details.sec>summary .chev{color:var(--dim);transition:transform .18s}
details.sec[open]>summary{border-bottom:1px solid var(--line);border-radius:12px 12px 0 0}
details.sec[open]>summary .chev{transform:rotate(90deg)}
.body{padding:18px}

/* contenido */
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;
margin-bottom:22px;scroll-margin-top:calc(var(--nav) + 14px)}
.hero .box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
.hero .n{font-size:34px;font-weight:800;letter-spacing:-1.4px;line-height:1.1}
.hero .l{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-top:8px}
.card{background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:16px;
margin-bottom:12px}
.card:last-child{margin-bottom:0}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--dim);font-weight:600;font-size:11px;text-transform:uppercase;
letter-spacing:1px;padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:none}
.track{height:7px;background:#21262d;border-radius:4px;overflow:hidden;min-width:96px}
.fill{height:100%;border-radius:4px}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;
text-transform:uppercase;letter-spacing:.6px;white-space:nowrap}
.b-critical{background:rgba(248,81,73,.18);color:var(--bad)}
.b-high{background:rgba(248,81,73,.13);color:var(--bad)}
.b-medium{background:rgba(210,153,34,.16);color:var(--warn)}
.b-low{background:rgba(88,166,255,.14);color:var(--acc)}
.b-info{background:rgba(139,148,158,.16);color:var(--dim)}
.gain{color:var(--ok);font-weight:700}
.kvs{display:grid;grid-template-columns:190px minmax(0,1fr);gap:7px 16px;font-size:14px}
.kvs .k{color:var(--dim)}
.kvs div{overflow-wrap:anywhere}
.sub-h{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:1px;
margin:18px 0 8px;font-weight:700}
.sub-h:first-child{margin-top:0}
.chead{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;
border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:14px}
.chead h3{margin:0;font-size:17px;display:flex;align-items:center;gap:9px}
.chead h3 svg{color:var(--acc)}
.chead .note{font-size:15px;font-weight:700;white-space:nowrap}
.imp{list-style:none;margin:0;padding:0;font-size:14px}
.imp li{padding:8px 0;border-bottom:1px solid var(--line)}
.imp li:last-child{border-bottom:none}
.imp a{text-decoration:none}
.imp a:hover{text-decoration:underline}
.tags{color:var(--dim);font-size:12px;margin-top:3px}
.finding h3{margin:0 0 9px;font-size:16px}
.finding p{margin:0 0 12px;color:#c9d1d9}
.finding ul{margin:0;padding-left:20px;color:#c9d1d9;font-size:14px}
.finding li{margin-bottom:5px}
.finding:target{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc)}
.steps-link{margin:0}
.steps-link a{display:inline-flex;align-items:center;gap:7px;font-size:13px;
text-decoration:none;color:var(--acc)}
.steps-link a:hover{text-decoration:underline}
details.howto{margin-top:14px;border:1px solid var(--line);border-radius:9px;
background:rgba(88,166,255,.04)}
details.howto>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:9px;
padding:10px 13px;font-size:13px;font-weight:600;color:var(--acc);user-select:none}
details.howto>summary::-webkit-details-marker{display:none}
details.howto>summary:hover{background:rgba(88,166,255,.07)}
details.howto>summary .cnt{margin-left:auto;color:var(--dim);font-size:11px;font-weight:500}
details.howto>summary .chev{color:var(--dim);transition:transform .18s}
details.howto[open]>summary{border-bottom:1px solid var(--line)}
details.howto[open]>summary .chev{transform:rotate(90deg)}
.howto-body{padding:6px 14px 14px}
.howto-item{padding:12px 0;border-bottom:1px dashed var(--line)}
.howto-item:last-child{border-bottom:none;padding-bottom:0}
.howto-item h4{margin:0 0 5px;font-size:14px}
.howto-item ol{margin:9px 0 0;padding-left:20px;font-size:14px;color:#c9d1d9}
.howto-item li{margin-bottom:6px}
.scan-note{color:var(--dim);font-size:13px;margin:0 0 12px}

/* selección y exportación por secciones */
.pick{display:flex;align-items:center;margin-right:2px}
.pick input{width:15px;height:15px;accent-color:var(--acc);cursor:pointer;margin:0}
.exp{background:transparent;border:1px solid var(--line);color:var(--dim);border-radius:7px;
padding:3px 8px;font:inherit;font-size:11px;cursor:pointer;display:inline-flex;align-items:center;
gap:5px;margin-left:10px;text-transform:none;letter-spacing:0}
.exp:hover{color:var(--acc);border-color:var(--acc)}
details.sec.picked{border-color:var(--acc);box-shadow:0 0 0 1px rgba(88,166,255,.35)}
.btn[disabled]{opacity:.45;cursor:default}
.btn[disabled]:hover{color:var(--dim);border-color:var(--line)}
.exp-wrap{max-width:1040px;margin:0 auto;padding:28px 20px 60px}
.exp-sec{margin-bottom:34px}
.exp-h2{font-size:13px;text-transform:uppercase;letter-spacing:1.4px;color:var(--acc);
margin:0 0 14px;font-weight:700;display:flex;align-items:center;gap:9px}
.exp-src{color:var(--dim);font-size:12px;border-left:2px solid var(--line);padding-left:10px;
margin:0 0 26px}

/* categorías desplegables del rastreo de archivos */
details.cat{border-bottom:1px solid var(--line)}
details.cat:last-of-type{border-bottom:none}
details.cat>summary{list-style:none;cursor:pointer;display:grid;
grid-template-columns:minmax(120px,1.4fr) 90px 90px 130px minmax(80px,1fr) 18px;
align-items:center;gap:10px;padding:10px 4px;font-size:14px}
details.cat>summary::-webkit-details-marker{display:none}
details.cat>summary:hover{background:rgba(88,166,255,.05)}
details.cat[open]>summary .chev{transform:rotate(90deg)}
details.cat .chev{color:var(--dim);transition:transform .18s}
details.cat .cat-count{color:var(--dim);font-size:12px}
details.cat .cat-bar{display:block;height:7px}
details.cat .cat-bar .fill{display:block}
.cat-body{padding:4px 4px 16px}
@media (max-width:720px){
details.cat>summary{grid-template-columns:1fr auto 18px}
details.cat .cat-count,details.cat .cat-bar{display:none}
}
.pathcell{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;
word-break:break-all}
code{background:#21262d;padding:2px 6px;border-radius:4px;font-size:13px;
font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.verdict{border-left:3px solid var(--acc);background:rgba(88,166,255,.06)}
.ok-note{color:var(--ok);margin:0}
footer{max-width:1300px;margin:0 auto;padding:20px;border-top:1px solid var(--line);
color:var(--dim);font-size:12px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
footer a{color:var(--brand);text-decoration:none}
#top{position:fixed;right:22px;bottom:22px;z-index:40;opacity:0;pointer-events:none;
transition:.2s;border-radius:50%;width:42px;height:42px;justify-content:center;padding:0}
#top.on{opacity:1;pointer-events:auto}

@media (max-width:960px){
.layout{grid-template-columns:minmax(0,1fr);padding-top:18px}
.side{position:static;flex-direction:row;flex-wrap:wrap}
.side .panel{flex:1;min-width:230px}
.tb .logo{display:none}
}
@media print{
.topbar,.side,#top,.exp,.pick{display:none}
body{background:#fff;color:#000}
.layout{display:block;padding:0}
details.sec,.card,.panel,.hero .box{border-color:#ccc;background:#fff;break-inside:avoid}
details.sec>summary{color:#000}
/* el dorado sobre papel blanco no se lee: se oscurece solo al imprimir */
footer a,.hero .n[style*="--brand"]{color:var(--brand-dark)}
}
"""

HTML_JS = """
(function(){
  var secs = Array.prototype.slice.call(document.querySelectorAll('details.sec'));
  var links = Array.prototype.slice.call(document.querySelectorAll('.tb nav a'));
  var toggle = document.getElementById('toggle-all');
  var topbar = document.querySelector('.topbar');

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

  function setAll(open){
    secs.forEach(function(s){ s.open = open; });
    toggle.dataset.open = open ? '1' : '0';
    toggle.querySelector('span').textContent = open ? 'Colapsar todo' : 'Expandir todo';
  }
  toggle.addEventListener('click', function(){ setAll(toggle.dataset.open !== '1'); });

  // Saltar a una seccion plegada no serviria de nada: se abre antes de ir.
  links.forEach(function(a){
    a.addEventListener('click', function(){
      var t = document.getElementById(a.dataset.target);
      if (t && t.tagName === 'DETAILS') t.open = true;
    });
  });

  var targets = links.map(function(a){ return document.getElementById(a.dataset.target); })
                     .filter(Boolean);
  var topBtn = document.getElementById('top');
  function spy(){
    var best = null, bestTop = -1e9;
    targets.forEach(function(t){
      var top = t.getBoundingClientRect().top - (navHeight() + 34);
      if (top <= 0 && top > bestTop) { bestTop = top; best = t; }
    });
    links.forEach(function(a){
      a.classList.toggle('active', !!best && a.dataset.target === best.id);
    });
    topBtn.classList.toggle('on', window.scrollY > 500);
  }
  window.addEventListener('scroll', spy, {passive:true});
  window.addEventListener('resize', spy);
  spy();

  // ---- Exportacion de secciones -------------------------------------------
  // El fichero resultante se construye con el mismo <style> y el mismo sprite de
  // iconos que este informe, asi que sale igual de autocontenido: se puede
  // enviar por correo y abrir sin conexion.
  var exportBtn = document.getElementById('export-sel');
  var exportLabel = exportBtn.querySelector('span');
  var host = (document.body.dataset.host || 'equipo');
  var stamp = (document.body.dataset.stamp || '');

  function picked(){
    return secs.filter(function(s){
      var box = s.querySelector('input[data-pick]');
      return box && box.checked;
    });
  }

  function refresh(){
    var n = picked().length;
    exportLabel.textContent = 'Exportar (' + n + ')';
    exportBtn.disabled = n === 0;
    secs.forEach(function(s){
      var box = s.querySelector('input[data-pick]');
      s.classList.toggle('picked', !!box && box.checked);
    });
  }

  function buildDocument(sections){
    var style = document.querySelector('style').outerHTML;
    var sprite = document.getElementById('sprite').outerHTML;
    var head = document.querySelector('header.page').innerHTML;
    var foot = document.querySelector('footer').outerHTML;
    var titles = sections.map(function(s){ return s.dataset.title; });
    var origen = 'Extracto del informe completo' +
                 (stamp ? ' generado el ' + stamp : '') +
                 '. Secciones incluidas: ' + titles.join(' · ') + '.';
    var cuerpo = sections.map(function(s){
      var ico = '<svg class="ic lg" viewBox="0 0 24 24" aria-hidden="true"><use href="#' +
                s.dataset.icon + '"/></svg>';
      return '<section class="exp-sec"><h2 class="exp-h2">' + ico + s.dataset.title +
             '</h2>' + s.querySelector('.body').innerHTML + '</section>';
    }).join('');
    return '<!DOCTYPE html>\\n<html lang="es"><head><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<title>' + titles.join(' + ') + ' \\u00b7 ' + host + '</title>' + style +
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
    var box = s.querySelector('input[data-pick]');
    var btn = s.querySelector('button[data-export]');
    // Sin esto, cualquier clic dentro del <summary> pliega la seccion.
    if (box) box.addEventListener('click', function(ev){ ev.stopPropagation(); refresh(); });
    if (btn) btn.addEventListener('click', function(ev){
      ev.preventDefault(); ev.stopPropagation(); descargar([s]);
    });
  });
  exportBtn.addEventListener('click', function(){ descargar(picked()); });
  refresh();
})();
"""


def _e(value: Any) -> str:
    """Escapa cualquier valor. Los nombres de dispositivo vienen de WMI y pueden
    traer &, < o > que romperían el marcado."""
    return escape(str(value), quote=True)


def _icon(name: str, cls: str = "ic") -> str:
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true"><use href="#{name}"/></svg>'


def _logo(cls: str = "brandmark", uid: str = "a") -> str:
    """Isotipo de Quilate, mismo trazado que quilate.svg.

    Va inline y completo en cada sitio donde aparece, no como <use> del sprite:
    el sprite lleva display:none y ahí Chromium no resuelve el degradado ni la
    máscara —los iconos de trazo sí salen porque no referencian nada—, así que
    el logotipo quedaba invisible. El sufijo evita que dos copias compartan id.
    """
    oro, muesca = f"ql-oro-{uid}", f"ql-muesca-{uid}"
    return (
        f'<svg class="{cls}" viewBox="0 0 100 100" aria-hidden="true">'
        f'<defs><linearGradient id="{oro}" x1="20" y1="10" x2="84" y2="94" '
        'gradientUnits="userSpaceOnUse">'
        '<stop offset="0" stop-color="#ffeeb0"/><stop offset=".34" stop-color="#f8d156"/>'
        '<stop offset=".70" stop-color="#e9ab1e"/><stop offset="1" stop-color="#b87c0b"/>'
        "</linearGradient>"
        f'<mask id="{muesca}"><rect width="100" height="100" fill="#fff"/>'
        '<rect x="56.5" y="46" width="21" height="52" fill="#000" '
        'transform="rotate(-45 67 72)"/></mask></defs>'
        f'<circle cx="48" cy="46" r="30" fill="none" stroke="url(#{oro})" stroke-width="14" '
        f'mask="url(#{muesca})"/>'
        f'<rect x="60.5" y="50.8" width="13" height="42.4" rx="1" fill="url(#{oro})" '
        'transform="rotate(-45 67 72)"/></svg>'
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


def _html_bar(pct: float) -> str:
    pct = max(0, min(100, pct))
    return (f'<div class="track"><div class="fill" style="width:{pct:.0f}%;'
            f'background:{_score_color(pct)}"></div></div>')


def _section(sid: str, label: str, icon: str, inner: str, count: str = "") -> str:
    cnt = f'<span class="cnt">{_e(count)}</span>' if count else ""
    # La casilla y el botón viven dentro del <summary>: el JS corta ahí la
    # propagación del clic para que marcar o exportar no pliegue la sección.
    pick = (f'<span class="pick"><input type="checkbox" data-pick="{sid}" '
            f'title="Marcar para exportar varias secciones juntas"></span>')
    export = (f'<button class="exp" data-export="{sid}" '
              f'title="Exportar solo esta sección">{_icon("i-download")}Exportar</button>')
    return (f'<details class="sec" id="{sid}" data-title="{_e(label)}" '
            f'data-slug="{_slug(label)}" data-icon="{icon}" open>'
            f"<summary>{pick}{_icon(icon)} {_e(label)}{cnt}{export}"
            f'{_icon("i-chev", "ic chev")}</summary>'
            f'<div class="body">{inner}</div></details>')


# ------------------------------------------------------------------ bloques --
def _hero(bench: Benchmark | None, auditor: Auditor, projection: dict[str, Any]) -> str:
    boxes = []
    if bench and bench.results:
        overall = bench.overall()
        letter, _ = grade(overall)
        proj = projection.get("projected_overall", overall)
        exp_pct = projection.get("experiential_pct", 0.0)
        boxes.append(f'<div class="box"><div class="n" style="color:{_score_color(overall)}">'
                     f'{overall:.0f}</div><div class="l">Puntuación actual · nota {letter}</div>'
                     f"</div>")
        boxes.append(f'<div class="box"><div class="n" style="color:var(--ok)">{proj:.0f}</div>'
                     f'<div class="l">Tras optimizar</div></div>')
        boxes.append(f'<div class="box"><div class="n" style="color:var(--brand)">+{exp_pct:.0f}%'
                     f'</div><div class="l">Fluidez percibida estimada</div></div>')
    boxes.append(f'<div class="box"><div class="n">{len(auditor.findings)}</div>'
                 f'<div class="l">Hallazgos en {auditor.checks_run} pruebas</div></div>')
    return f'<div class="hero" id="resumen">{"".join(boxes)}</div>'


def _sidebar(si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
             projection: dict[str, Any]) -> str:
    panels = []

    if bench and bench.results:
        overall = bench.overall()
        letter, _ = grade(overall)
        delta = ""
        if projection.get("projected_overall"):
            delta = (f'<div class="delta">Tras optimizar '
                     f'<b style="color:var(--ok)">{projection["projected_overall"]:.0f} pts</b>'
                     f' · fluidez percibida +{projection.get("experiential_pct", 0):.0f}%</div>')
        panels.append(f'<div class="panel"><div class="lbl">Puntuación global</div>'
                      f'<div class="big" style="color:{_score_color(overall)}">{overall:.0f}'
                      f"<span>pts</span></div>"
                      f'<div class="sub">nota {letter} · 100 = gama media reciente</div>'
                      f'<div style="margin-top:12px">{_html_bar(overall)}</div>{delta}</div>')
    else:
        panels.append('<div class="panel"><div class="lbl">Puntuación global</div>'
                      '<div class="sub">Ejecutado sin benchmark: informe solo de auditoría.'
                      "</div></div>")

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
                f"<b>{COMPONENT_LABELS.get(weakest, weakest)}</b> ({comp[weakest]:.0f} pts)</div>")
    panels.append(f'<div class="panel"><div class="lbl">Hallazgos</div>'
                  f'<div class="chips">{chips or "ninguno"}</div>{hint}</div>')

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
    trs = ""
    for r in bench.results.values():
        letter, _ = grade(r.score)
        measure = f"{r.raw:,.0f}" if r.unit == "IOPS" else f"{r.raw:,.2f}"
        detail = f'<div class="tags">{_e(r.detail)}</div>' if r.detail else ""
        trs += (f"<tr><td>{_e(r.name)}{detail}</td><td>{measure} {_e(r.unit)}</td>"
                f"<td><b>{r.score:.0f}</b></td><td>{letter}</td>"
                f"<td>{_html_bar(r.score)}</td></tr>")
    overall = bench.overall()
    letter, _ = grade(overall)
    return ('<div class="card"><div class="tw"><table>'
            "<tr><th>Prueba</th><th>Medida</th><th>Puntos</th><th>Nota</th>"
            "<th>Relativo a la referencia</th></tr>" + trs
            + f"<tr><td><b>Puntuación global</b></td><td></td>"
              f'<td style="color:{_score_color(overall)}"><b>{overall:.0f}</b></td>'
              f"<td><b>{letter}</b></td><td>{_html_bar(overall)}</td></tr>"
              "</table></div></div>")


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

    boot = getattr(auditor, "boot_report", {}) or {}
    segundos = getattr(auditor, "boot_seconds", None)
    if segundos or boot.get("error"):
        if segundos:
            arranques = len([b for b in boot.get("boots", [])
                             if str(b["fields"].get("BootIsRebootAfterInstall") or "0") != "1"])
            cuerpo = (f'<div class="kvs"><div class="k">Duración</div>'
                      f"<div><b>{segundos:.0f} s</b> de mediana sobre {arranques} arranques</div>")
            retrasos = sorted(
                ((str(d["fields"].get("Name") or d["fields"].get("FriendlyName") or ""),
                  d["fields"].get("TotalTime") or d["fields"].get("DegradationTime"),
                  d.get("kind") or "") for d in boot.get("delays", [])),
                key=lambda x: -float(x[1] or 0))
            vistos, filas = set(), ""
            for nombre, ms, tipo in retrasos:
                if not nombre or nombre in vistos or not ms:
                    continue
                vistos.add(nombre)
                filas += (f"<tr><td>{_e(nombre)}</td><td>{_e(tipo)}</td>"
                          f"<td>{float(ms) / 1000:.1f} s</td></tr>")
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
        filas = "".join(f"<tr><td>{_e(k)}</td><td>{v}</td></tr>"
                        for k, v in REFERENCE.items())
        out.append('<div class="card"><div class="sub-h">Escala de referencia</div>'
                   '<p class="scan-note">100 puntos equivalen a estos valores. Los tiempos van '
                   "en segundos; el resto, en su unidad.</p><div class=\"tw\">"
                   "<table><tr><th>Prueba</th><th>Referencia</th></tr>" + filas
                   + "</table></div></div>")
    return "".join(out)


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
                f"<td>{_html_bar(sysgain * 100)}</td></tr>")
    out = ('<div class="card"><div class="tw"><table>'
           "<tr><th>Componente</th><th>Ahora</th><th>Optimizado</th><th>Ganancia</th><th></th>"
           "</tr>" + trs + "</table></div></div>")

    cats = ""
    for cat, gain in sorted(projection.get("category_gain", {}).items(), key=lambda x: -x[1]):
        cats += (f"<tr><td>{_e(cat.capitalize())}</td>"
                 f'<td class="gain">+{gain * 100:.0f}%</td>'
                 f"<td>{_html_bar(min(100, gain * 100))}</td></tr>")
    if cats:
        out += ('<div class="card"><div class="sub-h">Margen por área</div><div class="tw">'
                "<table>" + cats + "</table></div></div>")
    return out


def export_html(path: Path, si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                projection: dict[str, Any]) -> None:
    cards = build_component_cards(si, bench, auditor)
    findings = sorted(auditor.findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.gain))
    actionable = sorted([f for f in auditor.findings if f.gain > 0], key=priority_rank)

    # (id, etiqueta, icono, contenido, contador del encabezado)
    secs: list[tuple[str, str, str, str, str]] = [
        ("inventario", "Inventario", "i-box", _inventory(si), ""),
    ]
    if bench and bench.results:
        secs.append(("benchmark", "Benchmark", "i-chart",
                     _benchmark_table(bench) + _metrics_block(bench),
                     f"{len(bench.results)} pruebas"))
    secs.append(("componentes", "Ficha por componente", "i-cpu",
                 _html_component_cards(cards), f"{len(cards)} componentes"))

    estado = _system_state_block(auditor, bench)
    if estado:
        arranque = getattr(auditor, "boot_seconds", None)
        secs.append(("estado", "Estado del sistema", "i-clock", estado,
                     f"arranque {arranque:.0f} s" if arranque else ""))

    scan = getattr(auditor, "scan", None)
    if scan is not None and scan.available and (scan.files or scan.special):
        safe, review = candidate_bytes(scan)
        secs.append(("archivos", "Archivos grandes", "i-folder", _storage_scan_block(scan),
                     f"{_human(safe + review)} prescindibles"))
    if projection.get("current_components"):
        secs.append(("proyeccion", "Proyección de mejora", "i-trend",
                     _projection_tables(projection),
                     f"+{projection.get('headroom_pct', 0.0):.0f}% sintético"))

    if actionable:
        trs = ""
        for i, f in enumerate(actionable, 1):
            trs += (f'<tr><td>{i}</td><td><a href="#h-{_e(f.id)}">{_e(f.title)}</a></td>'
                    f'<td class="gain">+{f.gain * 100:.0f}%</td>'
                    f"<td>{_e(f.effort)}</td><td>{_e(f.risk)}</td>"
                    f'<td><span class="badge b-{f.severity}">{f.severity}</span></td></tr>')
        secs.append(("plan", "Plan de acción priorizado", "i-list",
                     '<div class="card"><div class="tw"><table>'
                     "<tr><th>#</th><th>Acción</th><th>Ganancia est.</th><th>Esfuerzo</th>"
                     "<th>Riesgo</th><th>Severidad</th></tr>" + trs + "</table></div>"
                     '<p class="tags">Ordenado por retorno estimado dividido por esfuerzo. '
                     "Aplica de arriba hacia abajo y vuelve a medir tras cada bloque.</p></div>",
                     f"{len(actionable)} acciones"))

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
        secs.append(("hallazgos", "Hallazgos en detalle", "i-alert", detail,
                     f"{len(findings)} hallazgos"))

    verdict, extra = build_verdict(si, bench, auditor, projection)
    extras = "".join(f"<li>{_e(x)}</li>" for x in extra)
    secs.append(("veredicto", "Veredicto", "i-award",
                 f'<div class="card verdict"><p>{_e(verdict)}</p>'
                 f'{"<ul>" + extras + "</ul>" if extras else ""}</div>', ""))

    nav = f'<a href="#resumen" data-target="resumen">{_icon("i-zap")}Resumen</a>'
    nav += "".join(f'<a href="#{sid}" data-target="{sid}" title="{_e(label)}">'
                   f"{_icon(icon)}{_e(NAV_LABELS.get(sid, label))}</a>"
                   for sid, label, icon, _inner, _cnt in secs)
    body = "".join(_section(sid, label, icon, inner, cnt)
                   for sid, label, icon, inner, cnt in secs)

    date = f"{datetime.now():%d/%m/%Y %H:%M}"
    html = (
        '<!DOCTYPE html>\n<html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Informe de rendimiento · {_e(si.hostname)}</title>"
        f"<style>{HTML_CSS}</style></head>"
        f'<body data-host="{_e(_slug(si.hostname))}" data-stamp="{date}">'
        f"{_sprite()}"
        '<div class="topbar"><div class="tb">'
        f'<div class="logo">{_logo(uid="nav")}<span>Quilate <b>Suite</b></span></div>'
        f"<nav>{nav}</nav>"
        '<button class="btn" id="export-sel" disabled title="Marca secciones con su casilla '
        'para exportarlas juntas en un solo fichero">'
        f'{_icon("i-download")}<span>Exportar (0)</span></button>'
        '<button class="btn" id="toggle-all" data-open="1">'
        f'{_icon("i-list")}<span>Colapsar todo</span></button>'
        "</div></div>"
        '<div class="layout">'
        f"{_sidebar(si, bench, auditor, projection)}"
        '<main class="main">'
        '<header class="page"><h1>Informe de rendimiento y optimización</h1>'
        f'<div class="meta">{_e(si.hostname)} · {_e(si.os_name)} · generado el {date}</div>'
        "</header>"
        f"{_hero(bench, auditor, projection)}{body}"
        "</main></div>"
        f'<footer><span class="fbrand">{_logo("brandmark foot", uid="pie")}'
        f"{_e(APP_NAME)} v{APP_VERSION} · escala de referencia: "
        f"100 pts = gama media reciente</span>"
        f'<span>{_e(AUTHOR)} — <a href="{WEBSITE_URL}">{_e(WEBSITE)}</a></span></footer>'
        '<button class="btn" id="top" title="Volver arriba" '
        "onclick=\"window.scrollTo({top:0,behavior:'smooth'})\">"
        f'{_icon("i-up")}</button>'
        f"<script>{HTML_JS}</script></body></html>"
    )
    path.write_text(html, encoding="utf-8")
