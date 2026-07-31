"""La hoja de estilo del informe, entera y embebida.

Un solo fichero que se pueda enviar por correo significa que el CSS viaja
dentro. Va aparte porque son 550 lineas que no se leen nunca a la vez que el
codigo que las escupe, y porque `test_contraste` las lee de aqui para hacer
la aritmetica de WCAG sobre los colores de verdad y no sobre una copia.
"""

from __future__ import annotations

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

/* El enlace de salto se esconde con .vh como cualquier otra etiqueta para
   lectores, pero al recibir el foco tiene que verse: quien tabula mirando la
   pantalla necesita saber dónde ha ido a parar el foco, y un enlace que actúa
   sin aparecer es peor que no tenerlo. Deshacer el recorte hace falta entero:
   con `clip` puesto, cambiar solo la posición lo deja igual de invisible. */
.skip:focus{position:fixed;top:10px;left:10px;z-index:100;width:auto;height:auto;
margin:0;padding:10px 16px;clip:auto;overflow:visible;border-radius:var(--r-xs);
background:var(--ink2);border:1px solid var(--acc);color:var(--acc);
font-weight:600;box-shadow:var(--sh-lg)}

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

.tb nav{display:flex;gap:2px;flex:1 1 380px;flex-wrap:wrap;align-items:center}
/* Los enlaces se agrupan por para qué sirve cada sección —lo que explica la
   nota, y lo que hay que hacer con ella—, que es el mismo criterio que ya
   reparte las secciones en `data-group`.

   El filete cuelga del primer enlace del grupo, no es un elemento suelto entre
   dos. Suelto dependía de dónde cayera el ajuste de línea: en cuanto la barra
   envolvía se quedaba al final de una fila, detrás del último enlace y sin nada
   que separar, mientras el grupo siguiente empezaba abajo igualmente. Forzar el
   salto de fila lo arreglaba pero gastaba una fila entera de barra fija, que es
   altura robada en todas las pantallas. Atado al enlace no puede quedarse
   huérfano: si va a mitad de fila separa, y si abre fila queda como marca de
   entrada del grupo.

   No lleva rótulos: con la barra envolviendo, un rótulo se despega de su grupo y
   acaba encabezando el de al lado. El oro es color de estructura, nunca de dato. */
.tb nav a.ini-grupo{margin-left:12px}
.tb nav a.ini-grupo::before{content:"";position:absolute;left:-7px;top:50%;
transform:translateY(-50%);width:1px;height:15px;border-radius:1px;
background:linear-gradient(180deg,transparent,var(--gold-dk),transparent)}
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
/* El tamaño es el mismo de siempre a partir de unos 500px de ancho; por debajo
   baja hasta 22px. A 30px fijos, «Informe de rendimiento y optimización» ocupa
   tres líneas en una pantalla de 320px y empuja el resumen fuera de la vista
   antes de haber dicho nada. */
header.page h1{margin:0 0 6px;font-size:clamp(22px,6vw,30px);letter-spacing:-.028em;
line-height:1.12;font-weight:800}
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
/* Sin barra: la misma escala con la misma marca de referencia se pinta entera
   en la tabla de Benchmark, a media pantalla de aquí. Esta tira responde a «¿por
   dónde falla?» de un vistazo, y para eso basta el orden de las filas —van de
   peor a mejor— más la cifra. La barra la repetía sin añadir nada. */
.cs-row{display:grid;grid-template-columns:minmax(0,1fr) 82px;
align-items:center;gap:14px;padding:7px 0}
/* La etiqueta envuelve en vez de recortarse: «Almacenamiento» con el distintivo
   al lado no cabe en una línea, y cortarlo a «Almacenam...» deja al componente
   más importante del informe sin nombre. */
.cs-name{display:flex;align-items:center;gap:8px;font-size:14px;min-width:0;flex-wrap:wrap}
.cs-name svg{color:var(--faint)}
.cs-name .badge{font-size:9px;padding:1px 7px}
.cs-num{text-align:right;font-weight:800;font-size:18px;letter-spacing:-.03em}
.cs-let{color:var(--faint);font-size:11px;font-weight:700;margin-left:6px;letter-spacing:.08em}
.strip .hint{border-top:1px solid var(--line);margin-top:10px}

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
/* Las tarjetas con id son destino del índice interno de su sección: sin margen
   de desplazamiento, el salto las deja con la cabecera debajo de la barra. */
.card[id]{scroll-margin-top:calc(var(--nav) + 16px)}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--faint);font-weight:700;font-size:10.5px;text-transform:uppercase;
letter-spacing:.11em;padding:9px 10px;border-bottom:1px solid var(--line2);white-space:nowrap}
td{padding:10px;border-bottom:1px solid var(--line)}
/* La fila 1 es la de encabezados, así que el rayado empieza en el primer dato. */
table tr:nth-of-type(2n){background:rgba(255,255,255,.02)}
table tr:hover td{background:var(--acc-soft)}
tr:last-child td{border-bottom:none}
.track{height:7px;background:#1e2532;border-radius:4px;min-width:104px;position:relative}
.fill{height:100%;border-radius:4px;
animation:grow .8s cubic-bezier(.16,.84,.34,1) both}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
.fill{transform-origin:left center}
/* Escalonado dentro de cada tabla. Un informe con benchmark trae del orden de
   noventa barras contando la tabla de pruebas, las fichas y la proyección, y
   arrancarlas en el mismo fotograma obliga al compositor a levantarlas todas a
   la vez: la entrada se ve a tirones justo donde están las cifras. Cuatro
   tramos de 40 ms reparten el trabajo sin que se perciba espera. El ciclo se
   repite cada cinco filas para que una tabla de treinta no acabe con la última
   barra un segundo y medio tarde. */
table tr:nth-of-type(5n+2) .fill{animation-delay:.04s}
table tr:nth-of-type(5n+3) .fill{animation-delay:.08s}
table tr:nth-of-type(5n+4) .fill{animation-delay:.12s}
table tr:nth-of-type(5n+5) .fill{animation-delay:.16s}
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
/* Dos columnas de 200px + resto no caben en un móvil: la clave se partía en
   tres líneas para dejarle sitio a un valor que tampoco cabía. Apiladas, la
   clave pasa a ser un rótulo pequeño encima de su dato. */
@media (max-width:560px){
.kvs{grid-template-columns:minmax(0,1fr);gap:0}
.kvs .k{font-size:11.5px;color:var(--faint);margin-top:9px}
.kvs .k:first-child{margin-top:0}
}
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
/* `.xref` es lo mismo con otro nombre: una remisión a donde está el dato
   completo, en vez de repetirlo aquí. El informe tiene el detalle de cada
   medida en un solo sitio y el resto apunta a él. */
.steps-link,.xref{margin:0}
.steps-link a,.xref a{display:inline-flex;align-items:center;gap:7px;font-size:13px;
text-decoration:none;color:var(--acc)}
.steps-link a:hover,.xref a:hover{text-decoration:underline}
.xref{margin-top:4px}
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
/* Ese desplazamiento sirve mientras la bandeja no ocupe la pantalla entera. Por
   debajo de 360px de ancho `100vw - 40px` es menor que 320px, el cálculo daba
   `right:calc(100vw - 8px)` y el botón se iba fuera de la ventana: seguía
   pulsable para el teclado pero no había forma de verlo. Con la bandeja abierta
   y sin sitio al lado, se retira. */
@media (max-width:420px){
.tray.on ~ #top{display:none}
}

@media (max-width:980px){
.layout{grid-template-columns:minmax(0,1fr);padding-top:18px}
.side{position:static;flex-direction:row;flex-wrap:wrap}
.side .panel{flex:1;min-width:236px}
.tb .logo span{display:none}
}
@media (prefers-reduced-motion:reduce){
html{scroll-behavior:auto}
/* El retardo va con la duración: acortar la animación sin quitar el retardo
   deja la barra invisible —`both` mantiene el fotograma inicial— durante los
   160 ms del escalonado, que es justo el parpadeo que se quería evitar. */
*,*::before,*::after{animation-duration:.001ms!important;animation-delay:0s!important;
animation-iteration-count:1!important;transition-duration:.001ms!important}
}
@media print{
.topbar,.side,#top,.exp,.pick,.tray,.subnav,.backtop,.term>svg{display:none}
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
