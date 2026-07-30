"""El JavaScript del informe, entero y embebido.

Navegacion, indice interno, progreso de lectura y exportacion por secciones.
Todo opcional: sin JS el informe se lee igual, que es la razon de que las
secciones sean `<details>` y no divs con clases.
"""

from __future__ import annotations

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
  // El rotulo sale del estado real de las secciones y no de lo ultimo que se
  // pulso. Quien abre o cierra media docena a mano dejaba el boton diciendo
  // «Colapsar todo» con todo ya cerrado, y entonces la primera pulsacion no
  // hacia nada visible: gastaba el turno en poner el rotulo al dia.
  function sincronizarToggle(){
    var abiertas = secs.some(function(s){ return s.open; });
    toggle.dataset.open = abiertas ? '1' : '0';
    toggle.querySelector('span').textContent = abiertas ? 'Colapsar todo' : 'Expandir todo';
  }
  function setAll(open){
    secs.forEach(function(s){ s.open = open; });
    sincronizarToggle();
  }
  toggle.addEventListener('click', function(){ setAll(toggle.dataset.open !== '1'); });
  secs.forEach(function(s){ s.addEventListener('toggle', sincronizarToggle); });
  sincronizarToggle();

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
    // La ficha por componente se indexa por tarjeta y no por subtitulo: sus
    // `.sub-h` son los mismos tres o cuatro rotulos repetidos dentro de cada una
    // de las seis tarjetas, y un indice con quince entradas que dicen lo mismo
    // seis veces no lleva a ninguna parte. Donde no hay tarjetas con cabecera,
    // los subtitulos siguen siendo los puntos de referencia de la seccion.
    var subs = Array.prototype.slice.call(body.querySelectorAll('.chead h3'));
    if (!subs.length) subs = Array.prototype.slice.call(body.querySelectorAll('.sub-h'));
    if (subs.length < 3) return;
    var nav = document.createElement('div');
    nav.className = 'subnav';
    subs.forEach(function(h, i){
      // Se enlaza la tarjeta entera y no su titulo: el ancla en el <h3> deja la
      // cabecera pegada al borde de la barra y la nota del componente, que va
      // en esa misma linea, se queda medio tapada.
      var destino = h;
      if (h.parentNode && h.parentNode.classList.contains('chead')) {
        destino = h.closest('.card') || h;
      }
      if (!destino.id) destino.id = s.id + '-s' + i;
      var a = document.createElement('a');
      a.href = '#' + destino.id;
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
  // El scroll dispara muchos mas eventos que fotogramas pinta el navegador, y
  // `spy` recorre las doce secciones midiendo cada una. Agrupando por fotograma
  // se hace el calculo una vez por repintado en vez de una por evento; el
  // resultado en pantalla es el mismo porque nada se ve entre dos fotogramas.
  // Las llamadas directas a `spy()` se quedan como estan: quien pulsa un enlace
  // necesita el resaltado ya, no en el siguiente fotograma.
  var pendiente = false;
  function programarSpy(){
    if (pendiente) return;
    pendiente = true;
    requestAnimationFrame(function(){ pendiente = false; spy(); });
  }
  window.addEventListener('scroll', programarSpy, {passive:true});
  window.addEventListener('resize', programarSpy);
  spy();

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
