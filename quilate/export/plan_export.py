"""Exportacion del plan PowerShell comentado (solo Windows).

El script generado no se ejecuta solo: cada bloque pide confirmacion y el
primero crea un punto de restauracion.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..audit import Auditor
from ..benchmark import BUSY_CPU_PCT, Benchmark
from ..components import ComponentCard, build_component_cards
from ..console import human_bytes
from ..const import APP_NAME, APP_VERSION, AUTHOR, WEBSITE_URL
from ..projection import priority_rank
from ..storage_scan import ScanResult, candidate_bytes
from ..sysinfo import SystemInfo, gpu_label


PLAN_HEADER = """# ==============================================================================
#  Plan de optimizacion generado por {app} v{version}
#  {author} - {url}
#  Generado: {date}
# ------------------------------------------------------------------------------
#  LEE ESTO ANTES DE EJECUTAR
#  1. Este script NO se ejecuta solo. Cada bloque pide confirmacion.
#     Revisalo linea por linea y responde 's' solo a lo que entiendas.
#  2. Antes de tocar nada, ejecutalo con -WhatIf: enseña que haria cada bloque
#     sin cambiar una sola cosa.
#       powershell -ExecutionPolicy Bypass -File plan_optimizacion.ps1 -WhatIf
#  3. Cada cambio se anota ANTES de aplicarlo en un script de reversion que se
#     escribe al lado de este, con el valor que tenia tu equipo. No se supone
#     el valor "por defecto": se lee el tuyo.
#  4. El bloque 0 crea un punto de restauracion y exporta el registro. Hazlo.
#  5. Aplica los cambios por bloques y vuelve a medir entre bloques: si aplicas
#     diez cosas de golpe no sabras cual funciono. Para comparar:
#       quilate --json antes.json   (antes)
#       quilate --json despues.json (despues)
#       quilate --compare antes.json despues.json
#  6. Ejecutar como Administrador:
#       powershell -ExecutionPolicy Bypass -File plan_optimizacion.ps1
# ==============================================================================

param([switch]$WhatIf)

if (-not $WhatIf -and -not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    Write-Host "Ejecuta este script como Administrador." -ForegroundColor Red
    exit 1
}}

$Base = if ($PSScriptRoot) {{ $PSScriptRoot }} else {{ (Get-Location).Path }}
$RollbackPath = Join-Path $Base "deshacer_quilate_$(Get-Date -f yyyyMMdd_HHmm).ps1"

function Confirmar($texto) {{
    $r = Read-Host "$texto  [s/N]"
    return ($r -eq 's' -or $r -eq 'S')
}}

# Anota como deshacer un cambio ANTES de hacerlo. El script de reversion se crea
# solo si algo llega a aplicarse: un fichero vacio invitaria a confiar en el.
function Registrar($titulo, $lineas) {{
    if (-not (Test-Path $RollbackPath)) {{
        Set-Content -Path $RollbackPath -Encoding UTF8 -Value @(
            "# Reversion de los cambios aplicados por Quilate el $(Get-Date -f 'dd/MM/yyyy HH:mm')",
            "# Cada linea restaura el valor que este equipo tenia justo antes del cambio.",
            "# Ejecutar como Administrador:",
            "#   powershell -ExecutionPolicy Bypass -File `"$RollbackPath`"",
            ""
        )
    }}
    Add-Content -Path $RollbackPath -Encoding UTF8 -Value @("# --- $titulo")
    Add-Content -Path $RollbackPath -Encoding UTF8 -Value $lineas
    Add-Content -Path $RollbackPath -Encoding UTF8 -Value @("")
}}

# Devuelve el comando que restaura un valor del registro, o el que lo borra si
# ahora mismo no existe. Poner el "valor por defecto" en su lugar seria inventar:
# el valor por defecto de Windows y el que tu tenias no tienen por que coincidir.
function DeshacerValor($ruta, $nombre) {{
    $item = Get-ItemProperty -Path $ruta -Name $nombre -ErrorAction SilentlyContinue
    if ($null -eq $item) {{
        return "Remove-ItemProperty -Path '$ruta' -Name '$nombre' -ErrorAction SilentlyContinue"
    }}
    $v = $item.$nombre
    if ($v -is [byte[]]) {{
        $bytes = ($v | ForEach-Object {{ "0x{{0:x2}}" -f $_ }}) -join ','
        return "Set-ItemProperty -Path '$ruta' -Name '$nombre' -Value ([byte[]]($bytes))"
    }}
    if ($v -is [string]) {{
        return "Set-ItemProperty -Path '$ruta' -Name '$nombre' -Value '$v'"
    }}
    return "Set-ItemProperty -Path '$ruta' -Name '$nombre' -Value $v"
}}

function Bloque {{
    param([string]$Titulo, [string]$Hallazgo, [string]$Impacto,
          [scriptblock]$Deshacer, [scriptblock]$Accion)
    Write-Host ""
    Write-Host "--- $Titulo" -ForegroundColor Cyan
    if ($Hallazgo) {{ Write-Host "    $Hallazgo" -ForegroundColor DarkGray }}
    if ($Impacto)  {{ Write-Host "    $Impacto"  -ForegroundColor DarkGray }}

    if ($WhatIf) {{
        Write-Host "    [simulacion] se ejecutaria:" -ForegroundColor Yellow
        foreach ($linea in ($Accion.ToString() -split "`n")) {{
            $t = $linea.Trim()
            if ($t) {{ Write-Host "      $t" -ForegroundColor DarkYellow }}
        }}
        if (-not $Deshacer) {{
            Write-Host "      (sin reversion automatica: no cambia ningun ajuste)" -ForegroundColor DarkGray
        }}
        return
    }}

    if (-not (Confirmar "$Titulo?")) {{
        Write-Host "    omitido" -ForegroundColor DarkGray
        return
    }}
    if ($Deshacer) {{
        try {{
            $lineas = @(& $Deshacer | Where-Object {{ $_ }})
            if ($lineas.Count) {{ Registrar $Titulo $lineas }}
            else {{ throw "no se ha podido leer el valor actual" }}
        }} catch {{
            Write-Host "    No se ha podido anotar como deshacer esto: $_" -ForegroundColor Red
            if (-not (Confirmar "    Aplicarlo igualmente, sin poder revertirlo")) {{
                Write-Host "    omitido" -ForegroundColor DarkGray
                return
            }}
        }}
    }}
    & $Accion
}}

# --- BLOQUE 0: RED DE SEGURIDAD -----------------------------------------------
Bloque -Titulo "Crear un punto de restauracion del sistema antes de empezar" -Accion {{
    try {{
        Enable-ComputerRestore -Drive "$env:SystemDrive\\"
        Checkpoint-Computer -Description "Antes de Quilate" -RestorePointType MODIFY_SETTINGS
        Write-Host "Punto de restauracion creado." -ForegroundColor Green
    }} catch {{
        Write-Host "No se pudo crear el punto de restauracion: $_" -ForegroundColor Red
    }}
}}

Bloque -Titulo "Exportar copia de seguridad del registro (HKLM y HKCU) al escritorio" -Accion {{
    $dest = Join-Path ([Environment]::GetFolderPath('Desktop')) "backup_registro_$(Get-Date -f yyyyMMdd_HHmm)"
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    reg export HKLM "$dest\\HKLM.reg" /y | Out-Null
    reg export HKCU "$dest\\HKCU.reg" /y | Out-Null
    Write-Host "Copia guardada en $dest" -ForegroundColor Green
}}
"""

PLAN_FOOTER = f"""
# --- BLOQUE FINAL: MANTENIMIENTO SEGURO (recomendado siempre) ------------------
Bloque -Titulo "Ejecutar comprobacion de integridad del sistema (DISM + SFC)" -Accion {{
    Write-Host "Esto puede tardar 15-30 minutos..." -ForegroundColor Yellow
    DISM /Online /Cleanup-Image /RestoreHealth
    sfc /scannow
}}

Bloque -Titulo "Limpiar archivos temporales" -Accion {{
    Remove-Item "$env:TEMP\\*" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "$env:WINDIR\\Temp\\*" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Temporales limpiados." -ForegroundColor Green
}}

Bloque -Titulo "Ejecutar el asistente de Liberador de espacio en disco" -Accion {{
    cleanmgr /d $env:SystemDrive
}}

Write-Host ""
if ($WhatIf) {{
    Write-Host "Simulacion terminada: no se ha cambiado nada." -ForegroundColor Cyan
    Write-Host "Vuelve a ejecutarlo sin -WhatIf para aplicar lo que quieras." -ForegroundColor Cyan
}} elseif (Test-Path $RollbackPath) {{
    Write-Host "Reversion disponible en:" -ForegroundColor Cyan
    Write-Host "  $RollbackPath" -ForegroundColor Cyan
    Write-Host "Guardalo: contiene los valores que tenia tu equipo, no los de fabrica." -ForegroundColor Yellow
}} else {{
    Write-Host "No se ha aplicado ningun cambio reversible." -ForegroundColor Cyan
}}
Write-Host "Reinicia y vuelve a medir: quilate --json despues.json" -ForegroundColor Cyan
Write-Host "Y compara:  quilate --compare antes.json despues.json" -ForegroundColor Cyan
Write-Host "{AUTHOR} - {WEBSITE_URL}" -ForegroundColor DarkYellow
"""

# Rutas de registro que se tocan, para no repetirlas entre accion y reversion.
_VFX = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects"
_DESKTOP = "HKCU:\\Control Panel\\Desktop"
_TEMAS = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize"
_GAMEDVR = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\GameDVR"
_POWER = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Power"

# Comandos de remediacion automatizables (solo los seguros y reversibles).
# Cada uno lleva ademas como deshacerse: el codigo que LEE el estado actual y
# devuelve las lineas que lo restauran. Se ejecuta antes de aplicar el cambio,
# asi que lo que se guarda es lo que el equipo tenia de verdad, no un valor de
# fabrica que a lo mejor nunca tuvo. `None` = el bloque no cambia ningun ajuste.
PLAN_ACTIONS: dict[str, tuple[str, str, str | None]] = {
    "power_plan": (
        "Activar el plan de energia de Alto rendimiento",
        'powercfg /setactive SCHEME_MIN\n'
        '    Write-Host "Plan de energia = Alto rendimiento" -ForegroundColor Green',
        # El GUID se saca por patron y no por posicion: el texto de powercfg
        # esta traducido y cambia entre versiones de Windows.
        '$salida = (powercfg /getactivescheme | Out-String)\n'
        '    if ($salida -match \'([0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\') {\n'
        '        "powercfg /setactive $($Matches[1])"\n'
        '    }',
    ),
    "trim_off": (
        "Reactivar TRIM en el SSD y forzar un pase de TRIM",
        'fsutil behavior set DisableDeleteNotify 0\n'
        '    Optimize-Volume -DriveLetter $env:SystemDrive.TrimEnd(":") -ReTrim -Verbose',
        '$salida = (fsutil behavior query DisableDeleteNotify | Out-String)\n'
        '    if ($salida -match \'=\\s*(\\d)\') {\n'
        '        "fsutil behavior set DisableDeleteNotify $($Matches[1])"\n'
        '    }',
    ),
    "visual_fx": (
        "Ajustar efectos visuales para maximo rendimiento (manteniendo suavizado de fuentes)",
        f'Set-ItemProperty "{_VFX}" VisualFXSetting 2\n'
        f'    Set-ItemProperty "{_DESKTOP}" UserPreferencesMask '
        '([byte[]](0x90,0x12,0x03,0x80,0x10,0x00,0x00,0x00))\n'
        f'    Set-ItemProperty "{_DESKTOP}" MenuShowDelay "0"\n'
        f'    Set-ItemProperty "{_TEMAS}" EnableTransparency 0\n'
        '    Write-Host "Reinicia el explorador o la sesion para aplicar." -ForegroundColor Yellow',
        f'DeshacerValor "{_VFX}" VisualFXSetting\n'
        f'    DeshacerValor "{_DESKTOP}" UserPreferencesMask\n'
        f'    DeshacerValor "{_DESKTOP}" MenuShowDelay\n'
        f'    DeshacerValor "{_TEMAS}" EnableTransparency',
    ),
    "game_dvr": (
        "Desactivar la grabacion en segundo plano de Game Bar (deja el Modo de juego activo)",
        'Set-ItemProperty "HKCU:\\System\\GameConfigStore" GameDVR_Enabled 0\n'
        f'    New-Item "{_GAMEDVR}" -Force | Out-Null\n'
        f'    Set-ItemProperty "{_GAMEDVR}" AppCaptureEnabled 0\n'
        f'    Set-ItemProperty "{_GAMEDVR}" HistoricalCaptureEnabled 0',
        'DeshacerValor "HKCU:\\System\\GameConfigStore" GameDVR_Enabled\n'
        f'    DeshacerValor "{_GAMEDVR}" AppCaptureEnabled\n'
        f'    DeshacerValor "{_GAMEDVR}" HistoricalCaptureEnabled',
    ),
    "pagefile_off": (
        "Volver a dejar el archivo de paginacion en modo automatico",
        '$cs = Get-CimInstance Win32_ComputerSystem\n'
        '    Set-CimInstance $cs -Property @{AutomaticManagedPagefile=$true}',
        '$auto = (Get-CimInstance Win32_ComputerSystem).AutomaticManagedPagefile\n'
        '    "Set-CimInstance (Get-CimInstance Win32_ComputerSystem) '
        '-Property @{AutomaticManagedPagefile=`$$auto}"',
    ),
    "fast_startup": (
        "Activar hibernacion e inicio rapido",
        'powercfg /hibernate on\n'
        f'    Set-ItemProperty "{_POWER}" HiberbootEnabled 1',
        '$hib = (Get-ItemProperty "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Power" '
        '-Name HibernateEnabled -ErrorAction SilentlyContinue).HibernateEnabled\n'
        '    if ($hib -eq 0) { "powercfg /hibernate off" }\n'
        f'    DeshacerValor "{_POWER}" HiberbootEnabled',
    ),
    "sysmain": (
        "Desactivar SysMain (Superfetch)",
        'Stop-Service SysMain -Force -ErrorAction SilentlyContinue\n'
        '    Set-Service SysMain -StartupType Disabled\n'
        '    Write-Host "Si notas peor respuesta, deshazlo con el script de reversion." '
        '-ForegroundColor Yellow',
        '$s = Get-Service SysMain -ErrorAction SilentlyContinue\n'
        '    if ($s) {\n'
        '        "Set-Service SysMain -StartupType $($s.StartType)"\n'
        '        if ($s.Status -eq "Running") { "Start-Service SysMain" }\n'
        '    }',
    ),
    "wsearch_hdd": (
        "Desactivar el indexador de Windows Search (las busquedas seran mas lentas)",
        'Stop-Service WSearch -Force -ErrorAction SilentlyContinue\n'
        '    Set-Service WSearch -StartupType Disabled',
        '$s = Get-Service WSearch -ErrorAction SilentlyContinue\n'
        '    if ($s) {\n'
        '        "Set-Service WSearch -StartupType $($s.StartType)"\n'
        '        if ($s.Status -eq "Running") { "Start-Service WSearch" }\n'
        '    }',
    ),
    "defrag_hdd": (
        "Desfragmentar y optimizar el disco mecanico",
        'Optimize-Volume -DriveLetter $env:SystemDrive.TrimEnd(":") -Defrag -Verbose',
        None,   # reorganizar ficheros no es un ajuste: no hay nada que revertir
    ),
    "fs_dirty": (
        "Programar CHKDSK en el proximo reinicio",
        'chkdsk $env:SystemDrive /f /r',
        'if (-not ((chkntfs $env:SystemDrive | Out-String) -match "dirty|sucio")) {\n'
        '        "chkntfs /x $env:SystemDrive"\n'
        '    }',
    ),
    "startup_bloat": (
        "Listar los programas de inicio para que decidas cuales desactivar",
        'Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location | Format-Table -Auto\n'
        '    Write-Host "Desactivalos en Administrador de tareas > Inicio (ordenado por Impacto)." '
        '-ForegroundColor Yellow',
        None,   # solo enseña una lista
    ),
}


def _ps_str(texto: str) -> str:
    """Escapa un texto para meterlo en una cadena de PowerShell entre comillas.

    Los titulos de los hallazgos llevan comillas angulares, cifras y nombres de
    programa que vienen del sistema. Un `$` sin escapar convertiria parte del
    titulo en una variable, y una comilla partiria la cadena y el script.
    """
    return (texto.replace("`", "``").replace('"', '`"').replace("$", "`$"))


def _comentario(texto) -> str:
    """Ninguna cadena del sistema puede salirse de su linea de comentario.

    `_ps_str` no escapa CR/LF, y hace bien: dentro de unas comillas dobles de
    PowerShell son legitimos. Pero en un comentario `#` un salto de linea lo
    termina, y lo que venga detras se parsea como codigo. La diferencia importa
    porque parte de estos textos no los escribe ni el usuario ni el sistema
    operativo: el titulo de `smart_warn`, `disk_wear` o `disk_hot` lleva el
    FriendlyName que declara el firmware del disco, y este fichero se ejecuta
    como Administrador por diseño.

    El recorte a 300 es por legibilidad: un comentario mas largo que eso no lo
    lee nadie y solo sirve para esconder el resto del script.
    """
    return re.sub(r"[\r\n\x00]+", " ", str(texto))[:300]


def _plan_system_summary(si: SystemInfo, bench: Benchmark | None) -> str:
    """Para que equipo se genero este plan.

    Un plan de optimizacion es un script que se guarda, se pasa por correo y se
    ejecuta dias despues. Sin esta cabecera, dos planes de dos maquinas son dos
    ficheros indistinguibles, y el riesgo de aplicar el equivocado es real: los
    bloques tocan el registro y los servicios del equipo donde se ejecuten, no
    del equipo donde se midieron.
    """
    ram = f"{si.ram_total / 1024**3:.0f} GB"
    if si.ram_speed_mhz:
        ram += f" @ {si.ram_speed_mhz} MT/s"
    lines = ["\n# ==============================================================================",
             "#  EQUIPO PARA EL QUE SE GENERO ESTE PLAN",
             "#  Comprueba que coincide antes de ejecutarlo en otra maquina.",
             "# ==============================================================================",
             f"#  Nombre: {_comentario(si.hostname)}  |  "
             f"{_comentario(si.os_name)} {_comentario(si.os_build)}",
             f"#  CPU:    {_comentario(si.cpu_name)} ({si.cpu_cores}C/{si.cpu_threads}T)",
             f"#  RAM:    {ram}",
             f"#  Disco:  {_comentario(si.system_drive)} {_comentario(si.system_drive_media)}"]
    for g in si.gpus:
        lines.append(f"#  GPU:    {_comentario(gpu_label(g))}  |  "
                     f"driver {_comentario(g.get('driver') or 'n/d')}")
    medida = (bench.gpu_info if bench else {}) or {}
    if medida.get("device", {}).get("name"):
        lines.append(f"#  GPU medida por OpenCL: {_comentario(medida['device']['name'])}")
    elif bench is not None and getattr(bench, "gpu_unavailable", ""):
        lines.append(f"#  GPU no medible: {_comentario(bench.gpu_unavailable)}")
    if bench is not None and bench.busy_during_run() >= BUSY_CPU_PCT:
        lines.append(f"#  AVISO: al medir habia un {bench.busy_during_run():.0f}% de CPU "
                     f"ocupada por otros programas; las notas de abajo estan tocadas.")
    return "\n".join(lines) + "\n"


def _plan_component_summary(cards: list[ComponentCard]) -> str:
    lines = ["\n# ==============================================================================",
             "#  RESUMEN POR COMPONENTE (nota medida y margen de mejora agrupado)",
             "# =============================================================================="]
    for card in cards:
        note = (f"{card.score:.0f} pts (nota {card.letter})" if card.score is not None
                else "sin medir" if card.measurable else "sin nota sintetica")
        margin = f"margen +{card.gain * 100:.0f}%" if card.gain > 0.005 else "sin margen estimado"
        lines.append(f"#  {_comentario(card.label)}: {note} | {margin} | "
                     f"{len(card.findings)} hallazgo(s)")
        for f in card.findings:
            gain = f"+{f.gain * 100:.0f}%" if f.gain > 0 else "n/a"
            auto = "automatizado abajo" if f.id in PLAN_ACTIONS else "manual"
            lines.append(f"#      - [{gain}] {_comentario(f.title)}  ({auto})")
    return "\n".join(lines) + "\n"


def _plan_large_files(scan: ScanResult | None) -> str:
    """Los archivos grandes van como comentario, nunca como borrado automatico:
    solo el usuario sabe cual de sus ficheros sobra."""
    if scan is None or not scan.available or not scan.files:
        return ""
    safe, review = candidate_bytes(scan)
    lines = ["\n# ==============================================================================",
             "#  ARCHIVOS GRANDES ENCONTRADOS (revisalos tu: aqui no se borra nada)",
             "# ==============================================================================",
             f"#  Umbral: {human_bytes(scan.min_size)} | total localizado: "
             f"{human_bytes(scan.total_large)}",
             f"#  Basura segura: {human_bytes(safe)} | a revisar antes de borrar: "
             f"{human_bytes(review)}"]
    if scan.truncated:
        lines.append("#  Rastreo parcial: se agoto el presupuesto de tiempo (--scan-time)")
    lines.append("#")
    for f in scan.files[:20]:
        lines.append(f"#  {human_bytes(f['size']).rjust(9)}  "
                     f"{_comentario(f['category'])[:18].ljust(19)}"
                     f"{f['age_days']:>5}d  {_comentario(f['path'])}")
    if scan.special:
        lines.append("#")
        lines.append("#  Archivos de sistema (NO borrar a mano):")
        for s in scan.special:
            size = human_bytes(s["size"]) if s["size"] else "-"
            lines.append(f"#  {size.rjust(9)}  {_comentario(s['name']).ljust(16)} "
                         f"{_comentario(s['note'])}")
    return "\n".join(lines) + "\n"


def export_plan(path: Path, si: SystemInfo, bench: Benchmark | None, auditor: Auditor) -> int:
    blocks: list[str] = [_plan_system_summary(si, bench),
                         _plan_component_summary(build_component_cards(si, bench, auditor)),
                         _plan_large_files(getattr(auditor, "scan", None))]
    n = 0
    for f in sorted([x for x in auditor.findings if x.gain > 0], key=priority_rank):
        action = PLAN_ACTIONS.get(f.id)
        if not action:
            continue
        n += 1
        desc, code, capture = action
        gain = f"ganancia estimada +{f.gain * 100:.0f}% ({f.gain_note})"
        deshacer = f" -Deshacer {{\n    {capture}\n}}" if capture else ""
        blocks.append(
            f"\n# --- BLOQUE {n}: {desc} ---\n"
            f'Bloque -Titulo "{desc}" `\n'
            # `_comentario` antes que `_ps_str`: un salto de linea es legitimo
            # dentro de unas comillas dobles de PowerShell, pero aqui partiria la
            # llamada a `Bloque` en dos y dejaria media orden suelta a la vista.
            f'  -Hallazgo "Hallazgo: {_ps_str(_comentario(f.title))}" `\n'
            f'  -Impacto "{gain} | esfuerzo {f.effort} | riesgo {f.risk}"'
            f"{deshacer} -Accion {{\n    {code}\n}}\n"
        )

    manual = [f for f in auditor.findings
              if f.gain > 0 and f.id not in PLAN_ACTIONS]
    if manual:
        lines = ["\n# ==============================================================================",
                 "#  ACCIONES QUE NO SE PUEDEN AUTOMATIZAR (requieren decision o hardware)",
                 "# =============================================================================="]
        for f in manual:
            lines.append(f"#  · {_comentario(f.title)}  (+{f.gain * 100:.0f}%)")
            for s in f.steps:
                lines.append(f"#      - {_comentario(s)}")
        blocks.append("\n".join(lines) + "\n")

    content = (PLAN_HEADER.format(date=f"{datetime.now():%d/%m/%Y %H:%M}", app=APP_NAME,
                                  version=APP_VERSION, author=AUTHOR, url=WEBSITE_URL)
               + "".join(blocks) + PLAN_FOOTER)
    # Con BOM: PowerShell 5.1 lee los .ps1 sin marca como ANSI, y los titulos de
    # los hallazgos llevan acentos y comillas angulares que llegarian rotos.
    path.write_text(content, encoding="utf-8-sig")
    return n
