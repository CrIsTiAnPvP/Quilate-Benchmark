# ==============================================================================
#  Compila Quilate en un unico ejecutable: dist\quilate.exe
#  No requiere Python en la maquina donde se ejecute el .exe resultante.
#
#  Uso:  powershell -ExecutionPolicy Bypass -File build.ps1
# ==============================================================================

# Ojo: no se pone ErrorActionPreference = Stop porque cualquier cosa que los
# comandos nativos escriban en stderr abortaria el script aunque hayan ido bien.
# Aqui se comprueba $LASTEXITCODE, que es lo unico fiable.
Set-Location $PSScriptRoot

# Usa el interprete del entorno virtual si existe; si no, el del sistema.
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
Write-Host "Interprete: $py" -ForegroundColor Cyan

& $py -c "import psutil" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Falta psutil en ese interprete. Instalalo con:" -ForegroundColor Red
    Write-Host "  $py -m pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

& $py -c "import PyInstaller" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instalando PyInstaller..." -ForegroundColor Yellow
    & $py -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) { Write-Host "No se pudo instalar PyInstaller." -ForegroundColor Red; exit 1 }
}

Write-Host "Limpiando compilaciones anteriores..." -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

# --onefile        un unico .exe autocontenido
# --console        es una herramienta de terminal, necesita consola
# --collect-all    psutil trae extensiones binarias que el analisis no siempre ve
# --hidden-import  el paquete se importa por nombre desde el lanzador
& $py -m PyInstaller `
    --onefile `
    --console `
    --name quilate `
    --collect-all psutil `
    --hidden-import quilate `
    --exclude-module tkinter `
    --exclude-module unittest `
    --exclude-module pydoc `
    --noconfirm `
    quilate.py

if ($LASTEXITCODE -ne 0) { Write-Host "Fallo la compilacion." -ForegroundColor Red; exit 1 }

$exe = Get-Item "dist\quilate.exe"
Write-Host ""
Write-Host "Listo: $($exe.FullName)  ($([math]::Round($exe.Length/1MB,1)) MB)" -ForegroundColor Green
Write-Host "Pruebalo con:  .\dist\quilate.exe --quick" -ForegroundColor DarkGray
