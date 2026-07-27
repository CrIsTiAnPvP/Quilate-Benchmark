# PCBench Suite

Benchmark + auditoría de optimización para Windows (con soporte parcial en Linux).

**Cristian Alonso** — [cristianac.es](https://cristianac.es)

---

## Instalación

Python 3.9 o superior. La única dependencia es `psutil`, y se instala dentro de
un entorno virtual para no tocar el Python del sistema.

### Windows (PowerShell)

```powershell
cd E:\Proyectos\PCBench
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
cd /ruta/a/PCBench
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
python pcbench.py
```

Una consola elevada es una sesión nueva: hay que volver a activar el entorno.
Para evitarlo, invoca el intérprete del venv por ruta —no necesita activación:

```powershell
.venv\Scripts\python.exe pcbench.py
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
| `--html informe.html` | Informe HTML con branding |
| `--json datos.json` | Datos crudos para comparar ejecuciones |
| `--export-plan` | Genera `plan_optimizacion.ps1` (solo Windows) |
| `--no-color` | Desactiva colores ANSI |

### Flujo recomendado

```powershell
# 0. Entorno activado (o usa .venv\Scripts\python.exe en lugar de python)
.venv\Scripts\Activate.ps1

# 1. Medir el estado inicial y guardar la línea base
python pcbench.py --disk-size 2048 --json antes.json --html antes.html --export-plan

# 2. Revisar plan_optimizacion.ps1 línea por línea y ejecutarlo
powershell -ExecutionPolicy Bypass -File plan_optimizacion.ps1

# 3. Reiniciar y volver a medir
python pcbench.py --disk-size 2048 --json despues.json --html despues.html
```

## Qué hace

- **Benchmark**: CPU monohilo (4 subtests), CPU multihilo con eficiencia de
  escalado, ancho de banda de memoria, escritura/lectura secuencial y IOPS 4K.
  Escala normalizada donde 100 pts = equipo de gama media reciente.
- **Auditoría**: ~24 comprobaciones (espacio, tipo de disco, TRIM, canales de
  RAM, temperaturas, frecuencia sostenida, plan de energía, programas de inicio,
  servicios, SMART, antivirus solapados, antigüedad de la instalación...).
- **Proyección**: mejora estimada por componente y por área, con rendimientos
  decrecientes, y plan de acción ordenado por retorno dividido por esfuerzo.

## Notas importantes

- **El script no modifica nada.** Solo lee. El plan PowerShell se genera aparte,
  pide confirmación en cada bloque y el primero crea un punto de restauración.
- Los porcentajes de mejora son **estimaciones heurísticas** basadas en el tipo
  de cuello de botella detectado, no garantías. Mide antes y después.
- Las lecturas de disco pueden salir infladas por la caché del SO. Si ves IOPS
  por encima de 200.000, sube `--disk-size` a 2048 o más.
- La temperatura de CPU rara vez es accesible en Windows sin un driver de
  monitorización. Si sale "no disponible", instala LibreHardwareMonitor o
  HWiNFO64 y consúltala manualmente durante el benchmark.
- La mayoría de "tweaks de registro" que circulan por internet no hacen nada
  medible. Aquí solo se auditan los que tienen efecto real y documentado.