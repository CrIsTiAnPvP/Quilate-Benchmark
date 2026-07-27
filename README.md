# Quilate Suite

Benchmark + auditoría de optimización para Windows (con soporte parcial en Linux).

**Cristian Alonso** — [cristianac.es](https://cristianac.es)

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

Abre PowerShell **como Administrador** (sin permisos elevados se pierden las
comprobaciones de SMART, TRIM y servicios), activa el entorno y ejecuta:

```powershell
.venv\Scripts\Activate.ps1
python quilate.py
```

Una consola elevada es una sesión nueva: hay que volver a activar el entorno.
Para evitarlo, invoca el intérprete del venv por ruta —no necesita activación:

```powershell
.venv\Scripts\python.exe quilate.py
```

En los ejemplos siguientes, `python` asume el entorno ya activado.

### Opciones

| Flag | Qué hace |
|---|---|
| `--quick` | Benchmark rápido, menor precisión |
| `--no-bench` | Solo auditoría, sin medir |
| `--no-disk` | Omite las pruebas de disco |
| `--disk-size 2048` | Tamaño del fichero de test en MB (por defecto 512) |
| `--disk-path D:\` | Carpeta donde medir el disco |
| `--no-files` | Omite el rastreo de archivos grandes |
| `--scan-time 60` | Segundos de presupuesto para el rastreo (por defecto 30) |
| `--scan-path D:\Juegos` | Carpeta extra a rastrear (repetible) |
| `--min-file-size 512` | Umbral de "archivo grande" en MB (por defecto 128) |
| `--check-drivers` | Consulta en línea si hay drivers más nuevos (tarda 10-30 s) |
| `--html informe.html` | Informe HTML con branding |
| `--json datos.json` | Datos crudos para comparar ejecuciones |
| `--export-plan` | Genera `plan_optimizacion.ps1` (solo Windows) |
| `--no-color` | Desactiva colores ANSI |

### Ejecutable (.exe)

Para usarlo en un equipo sin Python, o para pasárselo a alguien:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

Genera `dist\quilate.exe`: un único archivo de ~6 MB, autocontenido, que se
puede copiar a un pendrive y ejecutar en cualquier Windows de 64 bits. Lleva el
icono del proyecto, que se regenera desde `quilate.png` con
`python tools/make_icon.py` cuando cambia el logo. Acepta
las mismas opciones que el script. Al ejecutarlo con doble clic espera a que
pulses Enter antes de cerrarse, para que dé tiempo a leer el informe.

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

# 2. Revisar plan_optimizacion.ps1 línea por línea y ejecutarlo
powershell -ExecutionPolicy Bypass -File plan_optimizacion.ps1

# 3. Reiniciar y volver a medir
python quilate.py --disk-size 2048 --json despues.json --html despues.html
```

## Qué hace

- **Benchmark**: CPU monohilo (4 subtests), CPU multihilo con eficiencia de
  escalado, ancho de banda de memoria, escritura/lectura secuencial y IOPS 4K.
  Escala normalizada donde 100 pts = equipo de gama media reciente.
- **Métricas de diagnóstico** que no puntúan pero explican la nota: jerarquía de
  caché (L1/L2/L3/RAM), rendimiento sostenido bajo carga larga —la señal de
  throttling que se ve sin sensores—, frecuencia real con todos los núcleos,
  latencia 4K del disco y telemetría de GPU en vivo.
- **Auditoría**: ~25 comprobaciones (espacio, tipo de disco, TRIM, canales de
  RAM, temperaturas, frecuencia sostenida, plan de energía, programas de inicio,
  servicios, SMART, antivirus solapados, antigüedad de la instalación...).
- **Archivos grandes**: rastrea el disco de sistema con un presupuesto de tiempo
  fijo, clasifica lo que encuentra (temporales, cachés, volcados, instaladores,
  copias, vídeo…) y separa lo que es basura de lo que hay que revisar antes de
  borrar. En el HTML cada categoría se despliega para ver exactamente qué
  ficheros la componen. Solo lee metadatos: nunca abre, mueve ni borra nada.
  El archivo de paginación, el de hibernación y el de intercambio quedan fuera
  del ranking —son enormes y lo encabezarían siempre— y se informan aparte con
  su explicación, porque no se borran a mano.
- **Ficha por componente**: procesador, memoria, almacenamiento, gráfica y
  sistema, cada uno con su inventario, la nota que ha sacado en las pruebas y
  las mejoras que le corresponden agrupadas, con la ganancia combinada y la
  puntuación que alcanzaría al aplicarlas. Va en la consola, en el JSON
  (`components`), en el HTML y como resumen en cabecera del plan PowerShell.
- **Proyección**: mejora estimada por componente y por área, con rendimientos
  decrecientes, y plan de acción ordenado por retorno dividido por esfuerzo.

## El informe HTML

Un único fichero autocontenido —sin CDN, sin fuentes externas, sin conexión—
que se puede enviar por correo tal cual:

- Barra de navegación fija con salto a cada sección y resaltado de la sección
  activa al desplazarte.
- Secciones plegables, con botón de *colapsar / expandir todo*.
- **Exportación por secciones**: cada sección tiene su botón para guardarla como
  fichero HTML suelto, y una casilla para marcar varias y bajarlas juntas en un
  único documento. Lo exportado se lleva los estilos y los iconos incrustados,
  así que sigue siendo autocontenido.
- Barra lateral fija con la puntuación global, la ficha resumida del equipo,
  el recuento de hallazgos por severidad y el cuello de botella detectado.
- Inventario completo: discos físicos con su salud y todos los volúmenes con
  su ocupación, no solo la unidad de sistema.
- Cada componente lleva una subtarjeta plegada con el procedimiento paso a paso
  de las mejoras que le tocan. Los pasos viven solo ahí: la sección *Hallazgos
  en detalle* se queda con el diagnóstico y enlaza a la ficha del componente,
  para no mantener dos copias del mismo procedimiento.
- Iconos SVG embebidos, diseño adaptable a móvil y hoja de estilo de impresión.

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
| `sensors.py` | Temperatura de CPU, frecuencia real y telemetría de GPU |
| `workloads.py` | Cargas de trabajo puras del benchmark |
| `sysinfo.py` | Inventario del equipo y clasificación de volúmenes |
| `storage_scan.py` | Rastreo y clasificación de archivos grandes |
| `benchmark.py` | Motor de medición, puntuación y nota global |
| `audit.py` | Comprobaciones de configuración y hallazgos |
| `projection.py` | Combinación de ganancias y proyección |
| `components.py` | Ficha por componente |
| `report.py` | Informe de consola |
| `export/` | `json_export` · `html_export` · `plan_export` |
| `cli.py` | Argumentos y orquestación |

Las dependencias van siempre en un sentido (de `const` hacia `cli`), sin
importaciones circulares. También funciona como módulo: `python -m quilate`.

## Notas importantes

- **El script no modifica nada.** Solo lee. El plan PowerShell se genera aparte,
  pide confirmación en cada bloque y el primero crea un punto de restauración.
- Los porcentajes de mejora son **estimaciones heurísticas** basadas en el tipo
  de cuello de botella detectado, no garantías. Mide antes y después.
- Las lecturas de disco pueden salir infladas por la caché del SO. Si ves IOPS
  por encima de 200.000, sube `--disk-size` a 2048 o más.
- El rastreo de archivos grandes tiene un presupuesto de tiempo: si lo agota,
  el informe lo dice y la cobertura es parcial. Sube `--scan-time` para cubrir
  todo el disco.
- La mayoría de "tweaks de registro" que circulan por internet no hacen nada
  medible. Aquí solo se auditan los que tienen efecto real y documentado.
