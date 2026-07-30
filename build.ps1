# ==============================================================================
#  Compila Quilate en un unico ejecutable: dist\Quilate.exe
#  No requiere Python en la maquina donde se ejecute el .exe resultante.
#
#  Uso:  powershell -ExecutionPolicy Bypass -File build.ps1
#        powershell -ExecutionPolicy Bypass -File build.ps1 -Sign -Pfx cert.pfx
#
#  Lo que sale de aqui va a manos de usuarios finales, asi que la compilacion
#  tiene tres obligaciones que no son opcionales, y el porque de cada una esta
#  escrito justo donde se cumple, mas abajo: no pasar nada por UPX, incrustar
#  los metadatos del ejecutable y poder quedar firmada.
# ==============================================================================

[CmdletBinding()]
param(
    # Firma el resultado llamando a sign.ps1. Sin esto la compilacion sale sin
    # firmar, que es lo correcto para una compilacion de trabajo: el certificado
    # no tiene por que estar en la maquina de nadie que solo quiera probar algo.
    [switch]$Sign,
    [string]$Pfx,
    [string]$PfxPassword,
    [string]$Thumbprint
)

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

# El .ico va versionado en el repositorio, asi que solo se regenera cuando el
# PNG de origen es mas reciente. Asi se puede compilar sin tener Pillow.
if ((Test-Path "quilate.png") -and
    (-not (Test-Path "quilate.ico") -or
     (Get-Item "quilate.png").LastWriteTime -gt (Get-Item "quilate.ico").LastWriteTime)) {
    Write-Host "Regenerando quilate.ico desde quilate.png..." -ForegroundColor Cyan
    & $py -c "import PIL" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { & $py -m pip install --quiet pillow }
    & $py tools\make_icon.py
    if ($LASTEXITCODE -ne 0) { Write-Host "No se pudo generar el icono." -ForegroundColor Red; exit 1 }
}

Write-Host "Limpiando compilaciones anteriores..." -ForegroundColor Cyan
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

# Los recursos se generan DESPUES de limpiar, no antes: van a build\, que es
# justo lo que la linea de arriba acaba de borrar.
Write-Host "Generando los recursos de Windows..." -ForegroundColor Cyan
& $py tools\make_win_resources.py
if ($LASTEXITCODE -ne 0) { Write-Host "No se pudieron generar los recursos." -ForegroundColor Red; exit 1 }

# --onefile        un unico .exe autocontenido
# --console        es una herramienta de terminal, necesita consola
# --collect-all    psutil trae extensiones binarias que el analisis no siempre ve
# --hidden-import  el paquete se importa por nombre desde el lanzador
#
# --noupx          NO QUITAR. PyInstaller usa UPX por su cuenta en cuanto lo
#   encuentra en el PATH, sin decir nada: no hay que pedirlo, hay que prohibirlo.
#   Cuando lo hace, comprime los binarios que empaqueta —18 de los 20 que lleva
#   Quilate: python3XX.dll, _ssl.pyd, _ctypes.pyd...— y eso tiene dos efectos,
#   los dos malos.
#
#   El primero es que rompe las firmas. Esos 18 binarios vienen firmados por la
#   Python Software Foundation y por Microsoft, con Authenticode valido. UPX
#   reescribe el PE, y la firma incrustada deja de valer. Es decir: la
#   compresion cambiaba 18 ficheros con firma valida por 18 ficheros
#   comprimidos y sin firmar, que el arranque descomprime en %TEMP%\_MEIxxxxxx
#   y carga desde ahi.
#
#   El segundo es la consecuencia: "binario comprimido, sin firma, escrito en
#   temporales y cargado a continuacion" es la descripcion de un empaquetador
#   malicioso, y el modelo que Defender ejecuta en local (FastPath) lo clasifica
#   como tal. Este proyecto se lo comio: Trojan:Win32/Bearfoos.A!ml, gravedad
#   grave, sobre dist\Quilate.exe recien compilado. El ahorro eran unos 2 MB.
#
# --version-file   los metadatos (editor, version, descripcion). Sin esto salian
#   todos vacios, que ademas de quedar mal en las propiedades del fichero es
#   otra senal para los mismos clasificadores.
#
# --manifest       el manifest propio, que declara asInvoker de forma explicita.
#   Ver el comentario de `NIVEL` en tools\make_win_resources.py: Quilate no se
#   eleva, y conviene que eso este escrito dentro del binario.
& $py -m PyInstaller `
    --onefile `
    --console `
    --name Quilate `
    --icon quilate.ico `
    --noupx `
    --version-file build\version_info.txt `
    --manifest build\quilate.manifest `
    --collect-all psutil `
    --hidden-import quilate `
    --exclude-module tkinter `
    --exclude-module unittest `
    --exclude-module pydoc `
    --noconfirm `
    quilate.py

if ($LASTEXITCODE -ne 0) { Write-Host "Fallo la compilacion." -ForegroundColor Red; exit 1 }

if (-not (Test-Path "dist\Quilate.exe")) {
    # La compilacion puede terminar con codigo 0 y aun asi no dejar el fichero:
    # si el antivirus se lo lleva a cuarentena en cuanto se escribe, PyInstaller
    # ya habia acabado. Decirlo aqui ahorra buscar el fallo donde no esta.
    Write-Host ""
    Write-Host "PyInstaller termino bien pero dist\Quilate.exe no esta." -ForegroundColor Red
    Write-Host "Lo mas probable es que el antivirus se lo haya llevado. Comprueba con:" -ForegroundColor Yellow
    Write-Host "  powershell -File tools\diagnostico_defender.ps1" -ForegroundColor Yellow
    exit 1
}

$exe = Get-Item "dist\Quilate.exe"
Write-Host ""
Write-Host "Listo: $($exe.FullName)  ($([math]::Round($exe.Length/1MB,1)) MB)" -ForegroundColor Green

if ($Sign) {
    Write-Host ""
    $argumentos = @{ Path = $exe.FullName }
    if ($Pfx)         { $argumentos.Pfx = $Pfx }
    if ($PfxPassword) { $argumentos.PfxPassword = $PfxPassword }
    if ($Thumbprint)  { $argumentos.Thumbprint = $Thumbprint }
    & "$PSScriptRoot\sign.ps1" @argumentos
    if ($LASTEXITCODE -ne 0) { Write-Host "La firma ha fallado." -ForegroundColor Red; exit 1 }
} else {
    Write-Host "Sin firmar. Para firmarlo:  .\sign.ps1 -Pfx <cert.pfx>" -ForegroundColor DarkGray
}

Write-Host "Pruebalo con:  .\dist\Quilate.exe --quick" -ForegroundColor DarkGray
