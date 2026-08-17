# Quilate Suite

Benchmark + auditoría de optimización para Windows (con soporte parcial en Linux).

**Cristian Alonso** — [cristianac.es](https://cristianac.es)

---

> ## ⚠️ Cambio de política de datos en la versión 2.8.0
>
> Hasta la versión **2.7.0 incluida, Quilate no enviaba ningún dato** de tu
> sistema a ninguna parte, y así estaba escrito aquí.
>
> **Desde la 2.8.0 sí.** Al terminar cada análisis se envía un resumen técnico
> reducido: modelo de CPU, GPU y RAM, tipo de disco, versión del sistema
> operativo, las puntuaciones del benchmark y los identificadores (no los
> textos) de los hallazgos, junto a un identificador aleatorio de instalación
> que se regenera cada 90 días. Sirve para poder comparar tu equipo con otros
> del mismo modelo de procesador.
>
> **No se envía** tu informe, ni el histórico, ni rutas o nombres de fichero, ni
> el nombre de tu equipo o de tu usuario, ni SSID/BSSID/MAC, ni números de serie,
> ni tu dirección IP.
>
> **No se puede desactivar desde el programa, y `--no-net` tampoco lo
> desactiva.** La primera vez que ejecutes la 2.8.0 verás un aviso, y esa
> ejecución todavía no envía nada. Si prefieres el comportamiento anterior, la
> 2.7.0 sigue publicada y seguirá funcionando.
>
> Lista cerrada de datos, tus derechos y las tres formas de evitarlo:
> **[PRIVACY.md](PRIVACY.md)**.

---

## Instalación

Python 3.9 o superior. La única dependencia es `psutil`, y se instala dentro de
un entorno virtual para no tocar el Python del sistema.

### Windows (PowerShell)

```powershell
cd <carpeta del proyecto>
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Si `Activate.ps1` falla por la política de ejecución, o prefieres no activar
nada, puedes llamar al intérprete del entorno directamente:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Linux / macOS

```bash
cd /ruta/al/proyecto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Para salir del entorno: `deactivate`.

## Uso

Abre PowerShell **normal** —no hace falta como Administrador—, activa el entorno
y ejecuta:

```powershell
.venv\Scripts\Activate.ps1
python quilate.py
```

Windows sacará **un** aviso de UAC al empezar. No es para elevar Quilate: es para
un proceso aparte que lee seis cosas que de otro modo no se ven y termina en dos
segundos ([qué son y qué pasa si dices que no](#qué-se-gana-aceptando-el-uac)).
El análisis, el benchmark y los informes corren siempre como tú.

En los ejemplos siguientes, `python` asume el entorno ya activado.

### Opciones

| Flag | Qué hace |
|---|---|
| `--quick` | Benchmark rápido, menor precisión |
| `--no-bench` | Solo auditoría, sin medir |
| `--no-disk` | Omite las pruebas de disco |
| `--no-gpu` | Omite las pruebas de GPU (cómputo, VRAM y PCIe) |
| `--disk-size 2048` | Tamaño del fichero de test en MB (por defecto 512) |
| `--disk-path D:\` | Carpeta donde medir el disco |
| `--no-net` | No mide latencia ni DNS, ni comprueba si hay versión nueva. **No desactiva el envío del resumen** ([PRIVACY.md](PRIVACY.md)) |
| `--no-files` | Omite el rastreo de archivos grandes |
| `--scan-time 60` | Segundos de presupuesto para el rastreo (por defecto 30) |
| `--scan-path D:\Juegos` | Carpeta extra a rastrear (repetible) |
| `--min-file-size 512` | Umbral de "archivo grande" en MB (por defecto 128) |
| `--check-drivers` | Consulta en línea si hay drivers más nuevos (tarda 10-30 s) |
| `--check-updates` | Consulta en línea si faltan actualizaciones de seguridad (tarda 10-30 s) |
| `--html informe.html` | Informe HTML con branding |
| `--json datos.json` | Datos crudos para comparar ejecuciones |
| `--export-plan` | Genera `plan_optimizacion.ps1` (solo Windows) |
| `--compare antes.json despues.json` | Contrasta dos ejecuciones y sale (no mide nada) |
| `--history` | Muestra el histórico local y su deriva, y sale |
| `--no-history` | No guarda esta ejecución en el histórico local (no afecta al envío del resumen) |
| `--mi-id` | Muestra tu identificador de instalación y sale ([PRIVACY.md](PRIVACY.md)) |
| `--elevate` | Pide permisos aunque no haya nadie delante para aceptarlos |
| `--no-elevate` | No pide permisos en ningún caso |
| `--no-color` | Desactiva colores ANSI |

### Qué se gana aceptando el UAC

**Quilate no se ejecuta como administrador.** El aviso de UAC lanza un proceso
aparte que ejecuta ocho consultas de lectura, devuelve el resultado y muere. El
análisis, el benchmark, el rastreo de archivos y la escritura de los informes
corren siempre con tu cuenta, así que los ficheros que salen son tuyos.

Las ocho consultas están juntas en un único sitio del código,
[`quilate/elevacion.py`](quilate/elevacion.py), precisamente para que se puedan
leer de una sentada. **Todas son de lectura**: no se escribe en el registro, no
se cambia ninguna configuración y no se instala nada. Hay un test que recorre
esa lista y falla si aparece un verbo que modifique algo, y otro que fija qué
puede invocar cada consulta, de forma que añadir una obliga a pasar por ahí.

Estas son las comprobaciones que sin permisos aparecen en «Sin comprobar», y no
como correctas:

| Comprobación | Qué consulta | Qué se pierde sin ella |
|---|---|---|
| Duración real del arranque | log `Diagnostics-Performance` de Windows | cuánto tarda de verdad en arrancar y qué programa lo retrasa |
| Integridad del sistema de archivos | `fsutil dirty query` | si el volumen está marcado como «sucio» |
| Cifrado del disco | `Get-BitLockerVolume` | si el disco del sistema está cifrado |
| Arranque seguro | `Confirm-SecureBootUEFI` | si Secure Boot está activo |
| Chip TPM | `Get-Tpm` | si hay TPM y está encendido |
| Protocolo SMB1 | `Get-WindowsOptionalFeature` | si sigue activo el protocolo por el que entró WannaCry |

Además, la **salud SMART** se sigue comprobando sin permisos pero con menos
detalle: el estado general (`HealthStatus`) se lee igual, mientras que el
desgaste, las horas de uso, los errores acumulados y los sectores reasignados y
pendientes salen de `Get-StorageReliabilityCounter` y del blob SMART crudo, que
sí los exigen. Son los que avisan de un disco que va a fallar **antes** de que
Windows lo dé por degradado.

Lo que **no** hace falta elevar, en contra de lo que suele suponerse: TRIM, el
plan de energía, los programas de inicio, los servicios, la memoria, la red y
todo el benchmark funcionan igual sin permisos.

**Si dices que no**, el análisis continúa entero y esas seis comprobaciones
dicen que no se han concedido los permisos, en vez de darse por buenas. El
informe distingue las tres situaciones: que no se pidieran (`--no-elevate`), que
se pidieran y se rechazaran, y que no hubiera nadie delante para contestar.

El aviso solo sale si hay alguien que pueda aceptarlo. Con la salida redirigida
a un fichero o a una tubería no se pide nada, porque un diálogo de UAC en una
tarea programada se queda parado hasta que alguien lo cierre; `--elevate` lo
pide igualmente y `--no-elevate` no lo pide nunca.

### Ejecutable (.exe)

Para usarlo en un equipo sin Python, o para pasárselo a alguien:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

Genera `dist\Quilate.exe`: un único archivo de ~7 MB, autocontenido, que se
puede copiar a un pendrive y ejecutar en cualquier Windows de 64 bits. Lleva el
icono del proyecto, que se regenera desde `quilate.png` con
`python tools/make_icon.py` cuando cambia el logo. Acepta
las mismas opciones que el script.

El ejecutable **no se comprime con UPX**, y eso no es un descuido: la compresión
dejaba sin firma los diecinueve binarios de la Python Software Foundation y de
Microsoft que van dentro del paquete, y el resultado se clasificaba como
`Trojan:Win32/Bearfoos.A!ml`. Cuesta un mega largo de tamaño. `build.ps1` pasa
`--noupx` por eso, y `python tools\comprobar_binario.py` comprueba antes de cada
publicación que no ha vuelto, que los metadatos están puestos y que el manifest
sigue pidiendo `asInvoker`.

El `.exe` **no va firmado**, y por eso Windows avisa de «editor desconocido» al
abrirlo y en el diálogo de UAC. Eso solo se quita con un certificado de firma de
código: [`sign.ps1`](sign.ps1) hace la firma y la verificación, y su cabecera
explica qué certificado comprar (OV frente a EV) y qué implica cada uno.

Si un antivirus se lo lleva, [`tools/diagnostico_defender.ps1`](tools/diagnostico_defender.ps1)
dice con qué nombre lo ha detectado y de dónde salió el veredicto.

Al abrir el `.exe` con doble clic no hay forma de pasarle flags, así que cuando
termina el análisis —y solo si no se pidió ningún fichero por línea de comandos—
aparece un **menú final**: `H` genera el informe HTML, `J` los datos JSON, `P` el
plan PowerShell, `T` los tres de golpe y `A` abre el informe en el navegador.
Reutiliza lo que ya está medido, así que no repite el benchmark; se pueden
generar varios seguidos y `Enter` cierra. Si la salida está redirigida a un
fichero o a otro proceso, el menú no aparece.

Una advertencia sobre las medidas: **el `.exe` puntúa algo más bajo en la prueba
multihilo** (un 10-20%), porque cada proceso hijo tiene que arrancar el
intérprete empaquetado. El informe lo avisa cuando se ejecuta así. Compara
siempre ejecuciones del mismo tipo: `.exe` con `.exe`, script con script.

### Flujo recomendado

```powershell
# 0. Entorno activado (o usa .venv\Scripts\python.exe en lugar de python)
.venv\Scripts\Activate.ps1

# 1. Medir el estado inicial y guardar la línea base
python quilate.py --disk-size 2048 --json antes.json --html antes.html --export-plan

# 2. Ver qué haría el plan sin tocar nada
powershell -ExecutionPolicy Bypass -File plan_optimizacion.ps1 -WhatIf

# 3. Revisarlo línea por línea y ejecutarlo (cada bloque pide confirmación)
powershell -ExecutionPolicy Bypass -File plan_optimizacion.ps1

# 4. Reiniciar, volver a medir y contrastar
python quilate.py --disk-size 2048 --json despues.json --html despues.html
python quilate.py --compare antes.json despues.json
```

## Qué hace

- **Benchmark**: CPU monohilo (4 subtests), CPU multihilo con eficiencia de
  escalado, ancho de banda de memoria, escritura/lectura secuencial, IOPS 4K y
  **GPU** (cómputo FP32, ancho de banda de VRAM y transferencia PCIe).
  Escala normalizada donde 100 pts = equipo de gama media reciente.
- **Margen de error en cada medida**: el mismo trabajo se reparte en tramos o
  repeticiones y se mira cuánto varían entre sí. Un número solo nunca delata que
  está contaminado —el test de disco daba 205.000 IOPS con todo aplomo mientras
  medía la caché del sistema operativo—, y una cifra con margen sí. Las medidas
  inestables se marcan y no valen para comparar dos ejecuciones.
- **Condiciones de la sesión**: cuánta CPU consumían *otros* programas con el
  benchmark parado, y qué programas eran. Si el equipo no estaba en reposo, la
  nota lo dice en vez de atribuir al hardware lo que era un antivirus.
- **Métricas de diagnóstico** que no puntúan pero explican la nota: jerarquía de
  caché (L1/L2/L3/RAM), rendimiento sostenido bajo carga larga —la señal de
  throttling que se ve sin sensores—, frecuencia real con todos los núcleos,
  latencia 4K del disco y telemetría de GPU en vivo.
- **Auditoría**: ~30 comprobaciones (espacio, tipo de disco, TRIM, canales de
  RAM, temperaturas, frecuencia sostenida, plan de energía, programas de inicio,
  servicios, SMART, antivirus solapados, antigüedad de la instalación, enlace de
  red...). Cada una acaba en un veredicto, en un «no aplica» o en un **«no se ha
  podido comprobar» con su motivo**: lo que no se ha podido mirar no cuenta como
  correcto, y el informe dice cuántas comprobaciones llegaron a conclusión.
- **Archivos grandes**: rastrea el disco de sistema con un presupuesto de tiempo
  fijo, clasifica lo que encuentra (temporales, cachés, volcados, instaladores,
  copias, vídeo…) y separa lo que es basura de lo que hay que revisar antes de
  borrar. En el HTML cada categoría se despliega para ver exactamente qué
  ficheros la componen. Solo lee metadatos: nunca abre, mueve ni borra nada.
  El archivo de paginación, el de hibernación y el de intercambio quedan fuera
  del ranking —son enormes y lo encabezarían siempre— y se informan aparte con
  su explicación, porque no se borran a mano.
- **Ficha por componente**: procesador, memoria, almacenamiento, gráfica, red y
  sistema, cada uno con su inventario, la nota que ha sacado en las pruebas y
  las mejoras que le corresponden agrupadas, con la ganancia combinada y la
  puntuación que alcanzaría al aplicarlas. Va en la consola, en el JSON
  (`components`), en el HTML y como resumen en cabecera del plan PowerShell.
- **Proyección**: mejora estimada por componente y por área, con rendimientos
  decrecientes, y plan de acción ordenado por retorno dividido por esfuerzo.

## GPU

La gráfica se mide de verdad, no solo se inventaría: **cómputo FP32**, **ancho
de banda de VRAM** y **transferencia por PCIe**, cada uno con su margen.

Va por OpenCL a través de `ctypes` (`OpenCL.dll` en Windows,
`libOpenCL.so.1` en Linux). Esa biblioteca la instala el propio driver de la
tarjeta, así que **no hay ninguna dependencia nueva que instalar** y funciona
igual con NVIDIA, AMD e Intel. Si no hay ninguna GPU medible, el informe dice
por qué y reparte su peso entre el resto de componentes: no tener gráfica no
penaliza la nota, pero tenerla y no mirarla ya no es una opción.

Dos detalles que no son obvios:

- **El número de vueltas del kernel se calibra** en cada equipo. Con una cifra
  fija, una tarjeta rápida despacha el kernel en tres milisegundos y la
  dispersión se dispara al 18%; calibrado baja al 0,7%. Y una integrada lenta no
  se queda colgada: Windows reinicia el driver si un kernel tarda más de dos
  segundos.
- **Se elige la gráfica que de verdad calcula.** En un portátil con integrada y
  dedicada aparecen las dos, y la ficha dice cuál se ha medido. Medir la que no
  trabaja sería repetir el error del driver que ya se corrigió.

## Red

Se lee el enlace, que es donde está el problema que nadie mira: una tarjeta
**Wi-Fi 6 conectada en 802.11ac**, un gigabit negociando a 100 Mbps por un cable
malo, o la banda de 2,4 GHz con una tarjeta capaz de 5. Es el mismo patrón que
la RAM funcionando a velocidad JEDEC con módulos que dan más.

La latencia y la resolución DNS **se miden por defecto**: se cronometra el
saludo TCP contra tres resolutores públicos y conocidos (1.1.1.1, 8.8.8.8 y
9.9.9.9). En esas sondas no se envía ningún dato: solo se mide el tiempo de ida
y vuelta. `--no-net` impide la llamada, no oculta el resultado.

Ojo con lo anterior, porque desde la 2.8.0 son dos cosas distintas: que las
sondas no manden nada sigue siendo cierto, pero **`--no-net` ya no significa
«no sale nada del equipo»**. Corta estas sondas y la comprobación de versión, no
el envío del resumen de la ejecución. Ver [PRIVACY.md](PRIVACY.md).

**No se recogen el SSID, el BSSID ni la dirección MAC.** Identifican tu red y tu
equipo, no dicen nada sobre el rendimiento, y estos informes se comparten. Hay
tests que lo comprueban. Desde la 2.8.0 esto importa más, no menos: parte del
informe se envía, y estos tres campos están en la lista de lo que nunca sale
porque no se recogen siquiera.

## Comparar dos ejecuciones

```powershell
python quilate.py --compare antes.json despues.json
```

No mide nada: contrasta lo ya medido. La parte que importa no es la resta, es
decidir si la diferencia significa algo — **una mejora del 3% entre dos
ejecuciones cuyas medidas bailan un 12% cada una no es una mejora, es ruido con
buena prensa**. Cada diferencia se compara contra el margen de las dos medidas y
se etiqueta como real o como indistinguible del ruido.

También enfrenta la proyección de la ejecución antigua con lo que de verdad
pasó, que es la única forma de saber si el modelo acierta. Si los dos JSON no
son del mismo equipo, lo dice antes de comparar nada.

## Histórico y deriva

Cada ejecución deja una línea en un fichero local (`LOCALAPPDATA` en Windows,
`XDG_DATA_HOME` en Linux). `--history` lo lee:

```text
Almacenamiento         120.0     99.0   -17.5%   ▇▇█▇▅▄▂▁
Duración del arranque   22.5     36.0   -60.0%   ▁▁▁▁▃▄▆█
```

`--compare` responde a «¿ha servido de algo lo que acabo de aplicar?».
Esto responde a la otra pregunta, la que un equipo acaba haciéndose con el
tiempo: **¿voy a peor?** El disco que se degrada, el arranque que crece mes a
mes, el portátil que baja de frecuencia en cuanto llega el verano.

Se comparan **bloques de al menos tres medidas**, no los extremos: con dos
puntos siempre se puede trazar una recta y no dice nada, y una ejecución rara no
puede declarar que el equipo se degrada. El signo va corregido, así que «+»
siempre significa mejor, también en el arranque y la temperatura, donde el
número baja cuando la cosa mejora.

### Dónde está y qué guarda exactamente

```text
Windows   %LOCALAPPDATA%\Quilate\historico.jsonl
Linux     $XDG_DATA_HOME/Quilate/historico.jsonl   (o ~/.local/share/Quilate/)
```

Una línea de JSON por ejecución, en texto plano. Estos son **todos** los campos
que se escriben, sin excepción:

| Campo | Qué es |
| --- | --- |
| `at` | Fecha y hora de la ejecución (ISO 8601) |
| `version` | Versión de Quilate que la generó |
| `overall` | Puntuación global |
| `cpu_single`, `cpu_multi`, `memory`, `disk`, `gpu` | Nota de cada componente |
| `boot_seconds` | Duración del arranque medida por Windows |
| `cpu_temp` | Temperatura de CPU bajo carga |
| `max_spread_pct` | El mayor margen de variación de la sesión |
| `busy_pct` | El mayor porcentaje de CPU ajena durante la medida |
| `findings` | Cuántos hallazgos hubo (el número, no cuáles) |
| `quick` | Si se midió con `--quick` |

**Solo cifras, banderas y dos fechas.** No hay rutas, ni nombres de programa, ni
nombre de equipo, ni lista de procesos, ni qué hallazgos fueron: para eso está
el JSON completo de cada ejecución, que se genera solo si lo pides con `--json`.
Los dos únicos campos de texto son `at` y `version`, y ninguno sale de tu
equipo. Hay un test que recorre el fichero ya escrito y falla si aparece
cualquier cosa que no sea un número, una bandera o una de esas dos fechas, así
que añadir un campo con texto del sistema rompe la suite el día que se escribe.

El fichero se recorta a las 200 últimas ejecuciones, se puede leer con
cualquier editor, y borrarlo no rompe nada: se vuelve a crear en la siguiente
ejecución. `--no-history` desactiva el registro por completo.

## El informe HTML

Un único fichero autocontenido —sin CDN, sin fuentes externas, sin conexión—
que se puede enviar por correo tal cual. **Todo va dentro**, incluido el
isotipo: el logo es SVG escrito a mano en el propio documento y el icono de la
pestaña viaja en una URL `data:`, así que no hay ningún `.ico` que acompañar.
Un test recorre todos los `href` y `src` del informe y falla si alguno apunta
fuera.

- **Dial de puntuación** con la referencia marcada arriba del todo: la escala
  llega a 200 puntos, así que los 100 de la referencia caen exactos en las doce
  en punto. Pasar de esa marca significa ir por encima de un equipo de gama
  media, y eso se lee sin comparar cifras.
- **Nota por componente** en una sola tira, todas las barras sobre la misma
  escala y con la referencia marcada. Antes las barras se saturaban al llegar a
  100 y un equipo de 105 puntos se pintaba igual que uno de 190; ahora el
  recorrido por encima de la media se ve.
- Barra lateral con la puntuación global, el recuento de hallazgos por severidad
  y el cuello de botella. Se fija en pantalla **solo si cabe entera**: fijarla
  siempre dejaba el último panel fuera de la vista hasta el final de la página, y
  darle scroll propio cortaba las frases a media palabra.
- **Buscador en cliente**: filtra filas de tablas, hallazgos y categorías de
  archivos según escribes, y esconde las secciones que no tienen coincidencias.
  En un rastreo con doscientas filas de ficheros no es un lujo. Al enfocarlo
  propone ejemplos **sacados de este informe** —sus categorías de hallazgo, sus
  componentes, sus tipos de archivo—, no una lista escrita a mano que acabaría
  sugiriendo «wifi» en un sobremesa conectado por cable.
- **Glosario emergente**: los términos que no todo el mundo tiene por qué saber
  —margen, referencia, carga ajena, cobertura— se explican con un globo la
  primera vez que aparecen. Solo la primera: a la tercera nadie lo lee.
- Barra de navegación fija con progreso de lectura, un punto de color en las
  secciones que traen hallazgos y una franja debajo que dice en qué sección
  estás y cuánto llevas leído. Al pulsar un enlace el resaltado salta directo al
  destino en vez de ir encendiéndose por todas las secciones intermedias
  mientras la página viaja.
- Las secciones largas se generan su propio índice a partir de sus subtítulos,
  con un «volver al principio» al final.
- **Cada sección tiene un tono según para qué sirve**: lo que hay que decidir
  (plan, hallazgos), lo que explica la nota (benchmark, componentes, red) y lo
  que solo es referencia (inventario). Sin esa distinción, doce secciones
  idénticas compiten todas por igual.
- Secciones plegables, con botón de *colapsar / expandir todo*.
- **Exportación por secciones**: cada sección tiene su botón para guardarla como
  fichero HTML suelto, y una casilla para marcar varias y bajarlas juntas. Una
  bandeja flotante enseña las elegidas y deja quitarlas una a una, con atajos
  para *todo*, *ninguno*, *solo lo accionable* y *solo diagnóstico técnico*. Lo
  exportado se lleva los estilos, los iconos y la marca incrustados, así que
  sigue siendo autocontenido: hay tests que lo comprueban abriéndolo.
- Inventario completo: discos físicos con su salud y todos los volúmenes con
  su ocupación, no solo la unidad de sistema.
- Cada componente lleva una subtarjeta plegada con el procedimiento paso a paso
  de las mejoras que le tocan. Los pasos viven solo ahí: la sección *Hallazgos
  en detalle* se queda con el diagnóstico y enlaza a la ficha del componente,
  para no mantener dos copias del mismo procedimiento.
- Iconos SVG embebidos, diseño adaptable a móvil, hoja de estilo de impresión y
  respeto por `prefers-reduced-motion`.

Sobre el color: el **dorado es de la marca** y nunca califica un dato; el
**cian** marca lo que se puede pulsar; el **semáforo** es solo diagnóstico. Por
eso su ámbar tira a naranja — con el dorado al lado, un ámbar amarillo se leía
como «esto es de Quilate» en vez de como «esto va regular».

### Las cuatro salidas dicen lo mismo

Consola, HTML, JSON y plan PowerShell son cuatro vistas de la misma ejecución, y
el modo natural de que se desincronicen es añadir un dato, enseñarlo donde
estabas trabajando y olvidarte del resto. Ha pasado: la GPU entró en la
puntuación global y la ficha de la gráfica siguió diciendo «sin nota sintética»
durante toda una versión.

`tests/test_paridad.py` planta un valor reconocible en cada fuente de datos y
comprueba que sale por el otro lado. No mira si existe la clave —una clave
presente con la lista vacía es justo el fallo que se quiere cazar—, mira si el
dato llega.

Ahí mismo se vigila el JavaScript del informe, que hace dos travesías delicadas:
de una cadena de Python a un `<script>`. Un error de sintaxis ahí no falla al
generar el fichero, produce un informe mudo —plegar, buscar y exportar dejan de
responder a la vez— y desde fuera parece un problema de diseño. Por eso el JS no
lleva **ni una sola barra invertida**: sin escapes no hay nada que se pueda
malinterpretar por el camino, y un test lo comprueba.

## Detección de hardware

- **Volúmenes que no son discos**: una carpeta de Google Drive, OneDrive o
  Dropbox se monta como unidad y psutil la ve como un disco fijo de 930 GB.
  Quilate los identifica (etiqueta, tipo de unidad y ausencia de partición
  física detrás) y los excluye de la auditoría, porque su espacio libre no es
  del equipo ni se libera borrando archivos. Se siguen mostrando, marcados.
- **Disco de sistema real**: resuelve la letra de la unidad → partición → disco
  físico, en vez de deducir el tipo del conjunto de discos. Sin esto, un equipo
  con un NVMe de sistema y dos HDD de datos recibía consejos para discos
  mecánicos que no le correspondían.
- **VRAM real**: `Win32_VideoController.AdapterRAM` es un entero de 32 bits y se
  satura en 4 GB, así que cualquier GPU moderna aparecía como de 4 GB. Se lee
  del registro (`qwMemorySize`, 64 bits) y, en NVIDIA, de `nvidia-smi`, que
  además da temperatura, uso, reloj y consumo en vivo.
- **Temperatura de CPU**: Windows no la expone; leerla exige un driver en modo
  kernel. Se prueban en cascada todas las fuentes que no requieren instalar uno
  (psutil, LibreHardwareMonitor y OpenHardwareMonitor por WMI, zona térmica
  ACPI, contadores de rendimiento y `Win32_TemperatureProbe`) y se recuerda cuál
  funcionó. Si ninguna responde, el informe lo dice, enumera lo que intentó y se
  apoya en el rendimiento sostenido y en la temperatura de GPU en su lugar: son
  medidas reales, no una cifra inventada. Para tener la cifra, deja abierto
  LibreHardwareMonitor y Quilate la tomará sola.

## Estructura del proyecto

`quilate.py` es solo el lanzador; la implementación vive en el paquete
`quilate/`, dividido por responsabilidades:

| Módulo | Responsabilidad |
|---|---|
| `const.py` | Branding y detección de plataforma |
| `console.py` | Color ANSI, cajas, barras, notas y formato de texto |
| `platform_utils.py` | Comandos, PowerShell/WMI, registro y privilegios |
| `elevacion.py` | Las consultas que necesitan permisos, y el proceso corto que las hace |
| `sensors.py` | Temperatura de CPU, frecuencia real y telemetría de GPU |
| `workloads.py` | Cargas de trabajo puras del benchmark |
| `sysinfo.py` | Inventario del equipo y clasificación de volúmenes |
| `storage_scan.py` | Rastreo y clasificación de archivos grandes |
| `benchmark.py` | Motor de medición, puntuación y nota global |
| `gpu_bench.py` | Medida de la GPU por OpenCL (enlace con `ctypes`) |
| `network.py` | Enlace de red, latencia y resolución DNS |
| `audit.py` | Comprobaciones de configuración y hallazgos |
| `projection.py` | Combinación de ganancias y proyección |
| `components.py` | Ficha por componente |
| `compare.py` · `compare_report.py` | Contraste de dos ejecuciones |
| `history.py` · `history_report.py` | Histórico local y detección de deriva |
| `report.py` | Informe de consola |
| `export/` | `json_export` · `html_export` · `plan_export` |
| `cli.py` | Argumentos y orquestación |

Las dependencias van siempre en un sentido (de `const` hacia `cli`), sin
importaciones circulares. También funciona como módulo: `python -m quilate`.

### Tests

```powershell
pip install -r requirements-dev.txt
pytest
```

La única dependencia de test es **pytest** (`requirements-dev.txt`); en producción
sigue habiendo una sola, `psutil`, y el `.exe` no lleva ninguna de las dos. Lo que
no cambia es lo que de verdad importaba de «solo biblioteca estándar»: los tests
**no tocan la red ni el hardware real**, así que se reproducen igual en cualquier
máquina. Las capturas de WMI y del registro viven en
`tests/fixtures/` con los SID, los nombres de usuario, los nombres de equipo y
las rutas de perfil ya quitados, así que la lógica se puede probar en cualquier
sistema —también en Linux— sin depender de la máquina que los generó. Los que
necesitan hardware que no siempre está (la GPU con OpenCL) se saltan diciendo
por qué. Hay más detalle en `tests/README.md`.

## El plan PowerShell

- **El script de Quilate no modifica nada.** Solo lee. El plan se genera aparte
  y no se ejecuta solo: cada bloque pide confirmación por separado.
- **`-WhatIf` enseña qué haría cada bloque sin cambiar una sola cosa.**
- **Cada cambio se anota antes de aplicarlo** en un script de reversión que se
  escribe al lado, con **el valor que tenía tu equipo**, leído en ese momento.
  No se pone «el valor por defecto de Windows» en su lugar: el valor por defecto
  y el tuyo no tienen por qué coincidir, y suponerlo sería inventar. Si el ajuste
  no existía, la reversión lo borra en vez de dejarlo puesto a algo.
- Si no se consigue anotar cómo deshacer un cambio, se avisa y se pregunta antes
  de aplicarlo igualmente.
- El script de reversión **solo se crea si algo llega a aplicarse**: un fichero
  vacío invitaría a confiar en él.
- El bloque 0 crea un punto de restauración y exporta el registro.
- La cabecera identifica **para qué equipo se generó** el plan. Es un fichero que
  se guarda y se ejecuta días después; los bloques tocan el registro y los
  servicios de la máquina donde se ejecuten, no de la máquina donde se midió.

## Notas importantes

- Los porcentajes de mejora son **estimaciones heurísticas** basadas en el tipo
  de cuello de botella detectado, no garantías. Mide antes y después, y usa
  `--compare` para saber si la diferencia supera el margen de las medidas.
- **La escala de referencia lleva fecha.** Una escala sin fecha no envejece, se
  pudre: «gama media» significaba una cosa en 2024 y otra en 2028, y la nota
  cambiaría de significado sin que nadie tocara una línea de código. Cuando la
  escala caduca, el informe avisa de que su propia vara de medir se ha quedado
  vieja en vez de seguir dando notas infladas.
- Las lecturas de disco pueden salir infladas por la caché del SO. Si ves IOPS
  por encima de 200.000, sube `--disk-size` a 2048 o más.
- El rastreo de archivos grandes tiene un presupuesto de tiempo: si lo agota,
  el informe lo dice y la cobertura es parcial. Sube `--scan-time` para cubrir
  todo el disco.
- La mayoría de "tweaks de registro" que circulan por internet no hacen nada
  medible. Aquí solo se auditan los que tienen efecto real y documentado.
