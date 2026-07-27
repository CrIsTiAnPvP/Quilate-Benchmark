"""Exportacion del plan PowerShell comentado (solo Windows).

El script generado no se ejecuta solo: cada bloque pide confirmacion y el
primero crea un punto de restauracion.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..audit import Auditor
from ..benchmark import Benchmark
from ..components import ComponentCard, build_component_cards
from ..const import APP_NAME, APP_VERSION, AUTHOR, WEBSITE_URL
from ..projection import priority_rank
from ..sysinfo import SystemInfo


PLAN_HEADER = """# ==============================================================================
#  Plan de optimizacion generado por {app} v{version}
#  {author} - {url}
#  Generado: {date}
# ------------------------------------------------------------------------------
#  LEE ESTO ANTES DE EJECUTAR
#  1. Este script NO se ejecuta solo. Cada bloque pide confirmacion.
#     Revisalo linea por linea y responde 's' solo a lo que entiendas.
#  2. El bloque 0 crea un punto de restauracion y exporta el registro. Hazlo.
#  3. Aplica los cambios por bloques y vuelve a medir entre bloques: si aplicas
#     diez cosas de golpe no sabras cual funciono.
#  4. Ejecutar como Administrador:
#       powershell -ExecutionPolicy Bypass -File plan_optimizacion.ps1
# ==============================================================================

if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{
    Write-Host "Ejecuta este script como Administrador." -ForegroundColor Red
    exit 1
}}

function Confirmar($texto) {{
    $r = Read-Host "$texto  [s/N]"
    return ($r -eq 's' -or $r -eq 'S')
}}

# --- BLOQUE 0: RED DE SEGURIDAD -----------------------------------------------
if (Confirmar "Crear un punto de restauracion del sistema antes de empezar?") {{
    try {{
        Enable-ComputerRestore -Drive "$env:SystemDrive\\"
        Checkpoint-Computer -Description "Antes de PCBench" -RestorePointType MODIFY_SETTINGS
        Write-Host "Punto de restauracion creado." -ForegroundColor Green
    }} catch {{
        Write-Host "No se pudo crear el punto de restauracion: $_" -ForegroundColor Red
    }}
}}

if (Confirmar "Exportar copia de seguridad del registro (HKLM y HKCU) al escritorio?") {{
    $dest = Join-Path ([Environment]::GetFolderPath('Desktop')) "backup_registro_$(Get-Date -f yyyyMMdd_HHmm)"
    New-Item -ItemType Directory -Path $dest -Force | Out-Null
    reg export HKLM "$dest\\HKLM.reg" /y | Out-Null
    reg export HKCU "$dest\\HKCU.reg" /y | Out-Null
    Write-Host "Copia guardada en $dest" -ForegroundColor Green
}}
"""

PLAN_FOOTER = f"""
# --- BLOQUE FINAL: MANTENIMIENTO SEGURO (recomendado siempre) ------------------
if (Confirmar "Ejecutar comprobacion de integridad del sistema (DISM + SFC)?") {{
    Write-Host "Esto puede tardar 15-30 minutos..." -ForegroundColor Yellow
    DISM /Online /Cleanup-Image /RestoreHealth
    sfc /scannow
}}

if (Confirmar "Limpiar archivos temporales?") {{
    Remove-Item "$env:TEMP\\*" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item "$env:WINDIR\\Temp\\*" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Temporales limpiados." -ForegroundColor Green
}}

if (Confirmar "Ejecutar el asistente de Liberador de espacio en disco?") {{ cleanmgr /d $env:SystemDrive }}

Write-Host ""
Write-Host "Terminado. Reinicia y vuelve a ejecutar el benchmark para comparar." -ForegroundColor Cyan
Write-Host "{AUTHOR} - {WEBSITE_URL}" -ForegroundColor Magenta
"""

# Comandos de remediación automatizables (solo los seguros y reversibles)
PLAN_ACTIONS: dict[str, tuple[str, str]] = {
    "power_plan": (
        "Activar el plan de energia de Alto rendimiento",
        'powercfg /setactive SCHEME_MIN\n'
        '    Write-Host "Plan de energia = Alto rendimiento" -ForegroundColor Green',
    ),
    "trim_off": (
        "Reactivar TRIM en el SSD y forzar un pase de TRIM",
        'fsutil behavior set DisableDeleteNotify 0\n'
        '    Optimize-Volume -DriveLetter $env:SystemDrive.TrimEnd(":") -ReTrim -Verbose',
    ),
    "visual_fx": (
        "Ajustar efectos visuales para maximo rendimiento (manteniendo suavizado de fuentes)",
        'Set-ItemProperty "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\VisualEffects" '
        'VisualFXSetting 2\n'
        '    Set-ItemProperty "HKCU:\\Control Panel\\Desktop" UserPreferencesMask '
        '([byte[]](0x90,0x12,0x03,0x80,0x10,0x00,0x00,0x00))\n'
        '    Set-ItemProperty "HKCU:\\Control Panel\\Desktop" MenuShowDelay "0"\n'
        '    Set-ItemProperty "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize" '
        'EnableTransparency 0\n'
        '    Write-Host "Reinicia el explorador o la sesion para aplicar." -ForegroundColor Yellow',
    ),
    "game_dvr": (
        "Desactivar la grabacion en segundo plano de Game Bar (deja el Modo de juego activo)",
        'Set-ItemProperty "HKCU:\\System\\GameConfigStore" GameDVR_Enabled 0\n'
        '    New-Item "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\GameDVR" -Force | Out-Null\n'
        '    Set-ItemProperty "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\GameDVR" '
        'AppCaptureEnabled 0',
    ),
    "pagefile_off": (
        "Volver a dejar el archivo de paginacion en modo automatico",
        '$cs = Get-CimInstance Win32_ComputerSystem\n'
        '    Set-CimInstance $cs -Property @{AutomaticManagedPagefile=$true}',
    ),
    "fast_startup": (
        "Activar hibernacion e inicio rapido",
        'powercfg /hibernate on\n'
        '    Set-ItemProperty "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Power" '
        'HiberbootEnabled 1',
    ),
    "sysmain": (
        "Desactivar SysMain (Superfetch) - reversible con 'sc config SysMain start=auto'",
        'Stop-Service SysMain -Force -ErrorAction SilentlyContinue\n'
        '    Set-Service SysMain -StartupType Disabled\n'
        '    Write-Host "Si notas peor respuesta, reactivalo: Set-Service SysMain -StartupType Automatic" '
        '-ForegroundColor Yellow',
    ),
    "wsearch_hdd": (
        "Desactivar el indexador de Windows Search (las busquedas seran mas lentas)",
        'Stop-Service WSearch -Force -ErrorAction SilentlyContinue\n'
        '    Set-Service WSearch -StartupType Disabled',
    ),
    "defrag_hdd": (
        "Desfragmentar y optimizar el disco mecanico",
        'Optimize-Volume -DriveLetter $env:SystemDrive.TrimEnd(":") -Defrag -Verbose',
    ),
    "fs_dirty": (
        "Programar CHKDSK en el proximo reinicio",
        'chkdsk $env:SystemDrive /f /r',
    ),
    "startup_bloat": (
        "Listar los programas de inicio para que decidas cuales desactivar",
        'Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location | Format-Table -Auto\n'
        '    Write-Host "Desactivalos en Administrador de tareas > Inicio (ordenado por Impacto)." '
        '-ForegroundColor Yellow',
    ),
}


def _plan_component_summary(cards: list[ComponentCard]) -> str:
    lines = ["\n# ==============================================================================",
             "#  RESUMEN POR COMPONENTE (nota medida y margen de mejora agrupado)",
             "# =============================================================================="]
    for card in cards:
        note = (f"{card.score:.0f} pts (nota {card.letter})" if card.score is not None
                else "sin medir" if card.measurable else "sin nota sintetica")
        margin = f"margen +{card.gain * 100:.0f}%" if card.gain > 0.005 else "sin margen estimado"
        lines.append(f"#  {card.label}: {note} | {margin} | {len(card.findings)} hallazgo(s)")
        for f in card.findings:
            gain = f"+{f.gain * 100:.0f}%" if f.gain > 0 else "n/a"
            auto = "automatizado abajo" if f.id in PLAN_ACTIONS else "manual"
            lines.append(f"#      - [{gain}] {f.title}  ({auto})")
    return "\n".join(lines) + "\n"


def export_plan(path: Path, si: SystemInfo, bench: Benchmark | None, auditor: Auditor) -> int:
    blocks: list[str] = [_plan_component_summary(build_component_cards(si, bench, auditor))]
    n = 0
    for f in sorted([x for x in auditor.findings if x.gain > 0], key=priority_rank):
        action = PLAN_ACTIONS.get(f.id)
        if not action:
            continue
        n += 1
        desc, code = action
        gain = f"ganancia estimada +{f.gain * 100:.0f}% ({f.gain_note})"
        blocks.append(
            f"\n# --- BLOQUE {n}: {desc} ---\n"
            f"# Hallazgo: {f.title}\n"
            f"# Impacto: {gain} | esfuerzo {f.effort} | riesgo {f.risk}\n"
            f'if (Confirmar "{desc}?") {{\n    {code}\n}}\n'
        )

    manual = [f for f in auditor.findings
              if f.gain > 0 and f.id not in PLAN_ACTIONS]
    if manual:
        lines = ["\n# ==============================================================================",
                 "#  ACCIONES QUE NO SE PUEDEN AUTOMATIZAR (requieren decision o hardware)",
                 "# =============================================================================="]
        for f in manual:
            lines.append(f"#  · {f.title}  (+{f.gain * 100:.0f}%)")
            for s in f.steps:
                lines.append(f"#      - {s}")
        blocks.append("\n".join(lines) + "\n")

    content = (PLAN_HEADER.format(date=f"{datetime.now():%d/%m/%Y %H:%M}", app=APP_NAME,
                                  version=APP_VERSION, author=AUTHOR, url=WEBSITE_URL)
               + "".join(blocks) + PLAN_FOOTER)
    path.write_text(content, encoding="utf-8")
    return n
