# Tests

```bash
python -m unittest discover -s tests -t .
```

Sin dependencias: `unittest` de la biblioteca estándar. No hacen falta permisos
de administrador ni estar en Windows — el acceso al registro y a WMI se sustituye
por los fixtures de `fixtures/`.

## Por qué existen

Los tres primeros fallos que cubren eran del mismo tipo: **leer el campo que dice
lo que el sistema podría estar haciendo en vez de lo que está haciendo.**

| Fallo | Se leía | Se lee ahora |
| --- | --- | --- |
| Driver gráfico | el adaptador más antiguo, que suele ser la iGPU parada | la gráfica que mueve la pantalla |
| Programas de inicio | los valores de `Run`, incluidos los desactivados | `Run` filtrado por `StartupApproved` |
| Velocidad de RAM | `Speed`, el máximo que soporta el módulo | `ConfiguredClockSpeed`, el que corre |
| Game Bar | `GameDVR_Enabled`, que vale 1 de fábrica | `HistoricalCaptureEnabled` |
| Lectura de disco | el fichero recién escrito, servido desde la caché | E/S sin buffer |

Todos son invisibles para un test que se limite a comprobar que el código no
lanza excepciones. Por eso los fixtures guardan **datos crudos** del sistema, no
salidas ya interpretadas.

### La versión general del mismo fallo

`test_cobertura.py` no cubre un caso concreto sino el patrón entero: ciega
**todas** las fuentes externas —registro, WMI, comandos, log de arranque— y
exige que ninguna comprobación conteste con un veredicto. Antes, una fuente que
no respondía se traducía en un mensaje neutro que el informe contaba como
prueba superada; `check_power_plan` estaba pasando por casualidad y solo se
descubrió porque el fallo de codificación reventó otra comprobación primero.

Solo cuatro quedan exentas, declaradas en el propio test: leen de psutil o de
`/proc`, que siguen vivos aunque el resto calle.

### Lo que se mide, se mide con margen

`test_dispersion.py` fija que cada cifra del benchmark venga acompañada de
cuánto varió consigo misma, y `test_comparacion.py` que ese margen decida si una
diferencia entre dos ejecuciones significa algo. El caso real que lo motiva está
en el propio test: seis tramos de una misma escritura de 512 MB en este SSD, de
487 a 2366 MB/s. Su media tiene un aspecto perfectamente creíble.

`test_plan.py` exige que todo bloque del plan que cambie un ajuste sepa
deshacerse leyendo el valor actual, nunca suponiendo el de fábrica.

## Procedencia de los fixtures

| Fichero | Origen |
| --- | --- |
| `startup_windows11.json` | Capturado de un Windows 11 real. 31 entradas, 16 desactivadas |
| `gamedvr_instalacion_limpia.json` | Capturado. Capturas nunca tocadas |
| `memoria_ddr4_xmp_activo.json` | Capturado. DDR4-3200 con XMP puesto |
| `gpu_una_dedicada.json` | Capturado. Un solo adaptador |
| `gpu_dos_adaptadores_reconstruido.json` | **Reconstruido**, no capturado — ver abajo |

### Lo que está reconstruido

`gpu_dos_adaptadores_reconstruido.json` no sale de un volcado de
`Win32_VideoController`, sino de una captura de pantalla del panel de Quilate en
un equipo ajeno al que no hay acceso. Los dos campos que deciden la lógica sí
están respaldados por lo que se veía —la integrada no reportaba resolución, y el
nombre se imprime literal desde WMI—, pero `AdapterRAM` y las fechas de driver
están deducidas del texto renderizado. Es el único fixture que cubre la rama de
dos adaptadores, así que se conserva; si algún día hay acceso al equipo, se
sustituye por un `quilate --json`.

Los casos de RAM sin XMP en `test_memoria.py` también son **sintéticos**: no se
dispone de un equipo con el perfil sin activar. Van marcados en el propio test.

### Lo que está anonimizado

Los nombres de aplicación de `startup_windows11.json` **no son los reales**: van
sustituidos por otros que conservan la misma forma, porque es la forma —y no el
programa— lo que ejercita el manejo de nombres. El fixture cubre a propósito
espacios (`Kilo Client`), puntos (`org.hotelvpn.client`), ambos a la vez
(`runtime.app.November Manager`), guiones bajos (`oscar_PapaHide_Updater`),
sufijos `.exe` y `.lnk`, dígitos (`Y19Z`), mayúsculas completas (`BRAVO`) y un
sufijo hexadecimal largo. Los blobs de estado y la estructura sí son los
capturados.

Un detalle que el fixture conserva y conviene no perder al recapturar: el `.lnk`
de la carpeta Inicio puede llamarse distinto que el valor de `Run` de la misma
aplicación —con espacio uno y sin él el otro—, así que el estado hay que buscarlo
con el nombre correcto en cada sitio.

Los modelos de hardware (gráfica, módulos de RAM) **sí son los reales**: no son
datos personales, y el nombre del adaptador es justo lo que prueba la detección
de integradas.

### Lo que caduca

El estado de los programas de inicio **cambia solo**. Durante la sesión en que se
capturó, tres entradas se reescribieron: una revisó su blob y otras dos pasaron
de activas a desactivadas. Los instaladores y los propios launchers reescriben
tanto el valor de `Run` como su marca en `StartupApproved`. Los totales del
fixture salen de esa captura concreta; si se recaptura, hay que recalcularlos.

## Cómo recapturar

El volcado que genera los fixtures sale de `quilate --json`, que ya incluye
`startup_items` y `gpus` completos. Para las claves crudas del registro hace
falta leerlas directamente; el detalle de cuáles está en el `_origen` de cada
fichero.

Al recapturar de una máquina, **anonimizar SIDs, nombres de usuario y de equipo**
antes de commitear. Los valores de las claves `Run` van vacíos a propósito: el
código solo usa los nombres, y las líneas de comando llevan rutas de perfil.
