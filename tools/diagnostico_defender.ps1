# ==============================================================================
#  Averigua si Windows Defender se ha llevado el .exe, y con que nombre.
#
#  Uso:  powershell -File tools\diagnostico_defender.ps1
#        powershell -File tools\diagnostico_defender.ps1 -Ruta dist\Quilate.exe
#        powershell -File tools\diagnostico_defender.ps1 -Escanear
#
#  Existe porque "el antivirus lo bloquea" no es un dato con el que se pueda
#  hacer nada. Lo que hace falta es el nombre exacto de la deteccion —cambia lo
#  que hay que arreglar y es lo primero que pide Microsoft en un informe de falso
#  positivo— y de donde ha salido: una firma concreta, el modelo de aprendizaje
#  automatico que corre en local, o la nube. Los nombres que acaban en "!ml" son
#  del modelo local; ver el mapa de origenes mas abajo.
# ==============================================================================

[CmdletBinding()]
param(
    [string]$Ruta = "dist\Quilate.exe",

    # Lanza un escaneo explicito del fichero antes de mirar. Es la forma de
    # comprobar una compilacion nueva sin esperar a que la proteccion en tiempo
    # real se tropiece con ella.
    [switch]$Escanear,

    [int]$Minutos = 1440
)

$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Titulo($texto) {
    Write-Host ""
    Write-Host "=== $texto " -ForegroundColor Cyan -NoNewline
    Write-Host ("=" * [Math]::Max(0, 74 - $texto.Length)) -ForegroundColor DarkCyan
}

# El identificador numerico es lo que guarda `Get-MpThreatDetection`; el nombre
# legible esta en `Get-MpThreat`, que solo lista las amenazas activas. Cuando la
# deteccion ya se ha limpiado, el numero se queda huerfano, asi que los que este
# proyecto se ha encontrado de verdad van anotados aqui.
#
# Las claves son cadenas a proposito. Los ThreatID de Defender son enteros de 32
# bits SIN signo y los hay por encima de 2.147.483.647, asi que un `[int]` sobre
# ellos no desborda en silencio: revienta. El primero que se encontro aqui,
# 2147731250, es justo uno de esos. Comparando el texto no hay nada que
# convertir y no hay forma de equivocarse.
$CONOCIDOS = @{
    "2147731250" = "Trojan:Win32/Bearfoos.A!ml"
}

# --------------------------------------------------------------- el fichero --
Titulo "El fichero"
if (Test-Path $Ruta) {
    $f = Get-Item $Ruta
    Write-Host "  $($f.FullName)"
    Write-Host "  $([math]::Round($f.Length/1MB,2)) MB   modificado $($f.LastWriteTime)"
    $firma = Get-AuthenticodeSignature $f.FullName
    Write-Host "  Firma: $($firma.Status)" -ForegroundColor $(if ($firma.Status -eq 'Valid') {'Green'} else {'Yellow'})
    if ($firma.SignerCertificate) { Write-Host "  Firmante: $($firma.SignerCertificate.Subject)" }
} else {
    Write-Host "  NO EXISTE: $Ruta" -ForegroundColor Red
    Write-Host "  Si acababa de compilar, esto es la cuarentena: el fichero se" -ForegroundColor Yellow
    Write-Host "  escribio y Defender se lo llevo acto seguido." -ForegroundColor Yellow
}

# ------------------------------------------------------------ escaneo a mano --
if ($Escanear -and (Test-Path $Ruta)) {
    Titulo "Escaneo explicito"
    Write-Host "  Start-MpScan sobre el fichero..."
    Start-MpScan -ScanType CustomScan -ScanPath (Get-Item $Ruta).FullName
    if (Test-Path $Ruta) {
        Write-Host "  El fichero sobrevive al escaneo." -ForegroundColor Green
    } else {
        Write-Host "  El escaneo se lo ha llevado." -ForegroundColor Red
    }
}

# ------------------------------------------------------------- detecciones --
Titulo "Detecciones de las ultimas $Minutos min (Get-MpThreatDetection)"

# DetectionSourceTypeID: de donde ha salido. Sin esto no se sabe si hay que
# tocar el binario o discutir con la nube de Microsoft.
$ORIGENES = @{
    0 = "desconocido"; 1 = "proteccion en tiempo real"; 2 = "escaneo programado"
    3 = "proteccion en tiempo real (FastPath: modelo local)"
    4 = "descarga / adjunto (IOAV)"; 5 = "supervision de comportamiento"
    6 = "escaneo peticion del usuario"; 7 = "IOAV"; 8 = "supervision de sistema"
}
# ThreatStatusID: que ha pasado con ella.
$ESTADOS = @{
    0 = "desconocido"; 1 = "detectada"; 2 = "limpiada"; 3 = "activa / en cuarentena pendiente"
    4 = "sospechosa"; 5 = "no limpiada"; 6 = "eliminada"; 102 = "no limpiada"
    103 = "detectada, accion pendiente"; 104 = "no limpiada"; 105 = "permitida"
    106 = "en cuarentena"; 107 = "limpiada, requiere reinicio"
}

$desde = (Get-Date).AddMinutes(-$Minutos)
try {
    $det = @(Get-MpThreatDetection -ErrorAction Stop |
             Where-Object { $_.InitialDetectionTime -gt $desde } |
             Sort-Object InitialDetectionTime -Descending)
} catch {
    Write-Host "  No se ha podido consultar: $($_.Exception.Message)" -ForegroundColor Yellow
    $det = @()
}

if (-not $det) {
    Write-Host "  Ninguna. " -ForegroundColor Green -NoNewline
    Write-Host "(Si el .exe no existe y aqui no hay nada, no ha sido Defender:"
    Write-Host "  mira si hay otro antivirus instalado.)"
} else {
    foreach ($d in $det) {
        $nombre = $CONOCIDOS["$($d.ThreatID)"]
        if (-not $nombre) { $nombre = "(id $($d.ThreatID) -- mirar en Get-MpThreat)" }
        Write-Host ""
        Write-Host "  $($d.InitialDetectionTime)  " -NoNewline
        Write-Host $nombre -ForegroundColor Red
        Write-Host "    origen : $($ORIGENES[[int]$d.DetectionSourceTypeID])"
        Write-Host "    estado : $($ESTADOS[[int]$d.ThreatStatusID])"
        Write-Host "    proceso: $($d.ProcessName)"
        foreach ($r in $d.Resources) { Write-Host "    recurso: $r" -ForegroundColor DarkGray }
    }
    Write-Host ""
    Write-Host "  Los recursos que empiezan por 'process:' significan que Defender" -ForegroundColor Yellow
    Write-Host "  no solo marco el fichero: mato procesos que estaban corriendo." -ForegroundColor Yellow
}

# -------------------------------------------------------- amenazas activas --
Titulo "Amenazas activas con nombre (Get-MpThreat)"
try {
    $amenazas = @(Get-MpThreat -ErrorAction Stop)
    if ($amenazas) {
        $amenazas | Select-Object ThreatName, SeverityID, @{n='Recursos';e={$_.Resources -join ' '}} |
            Format-Table -AutoSize -Wrap
    } else { Write-Host "  Ninguna." -ForegroundColor Green }
} catch { Write-Host "  No disponible: $($_.Exception.Message)" -ForegroundColor Yellow }

# ------------------------------------------------------------ event viewer --
Titulo "Registro de eventos (Microsoft-Windows-Windows Defender/Operational)"
Write-Host "  1116 = deteccion   1117 = accion tomada   1015 = comportamiento" -ForegroundColor DarkGray
Write-Host "  1006/1007 = malware encontrado / accion   5007 = cambio de config" -ForegroundColor DarkGray
Write-Host ""
try {
    $ev = @(Get-WinEvent -LogName "Microsoft-Windows-Windows Defender/Operational" `
                         -MaxEvents 200 -ErrorAction Stop |
            Where-Object { $_.Id -in 1116, 1117, 1015, 1006, 1007 -and $_.TimeCreated -gt $desde })
    if (-not $ev) {
        Write-Host "  Ningun evento de deteccion en la ventana." -ForegroundColor Green
    } else {
        foreach ($e in $ev | Select-Object -First 10) {
            Write-Host "  --- $($e.Id)  $($e.TimeCreated) ---" -ForegroundColor Cyan
            # El mensaje viene traducido al idioma del sistema, asi que se filtra
            # por las etiquetas en los dos idiomas en vez de por una sola.
            $e.Message -split "`r?`n" |
                Where-Object { $_ -match '(?i)(nombre|name|gravedad|severity|categor|ruta|path|tipo de detecc|detection type|origen|origin|acci.n|action|estado|status|usuario|user):' } |
                ForEach-Object { Write-Host "   $($_.Trim())" }
            Write-Host ""
        }
    }
} catch {
    Write-Host "  El log pide permisos de administrador. Vuelve a lanzar esto" -ForegroundColor Yellow
    Write-Host "  desde una consola elevada, o mira el log a mano:" -ForegroundColor Yellow
    Write-Host "    eventvwr.msc -> Registros de aplicaciones y servicios ->" -ForegroundColor Cyan
    Write-Host "    Microsoft -> Windows -> Windows Defender -> Operational" -ForegroundColor Cyan
}

# ---------------------------------------------------------------- interfaz --
Titulo "Como verlo en la interfaz"
Write-Host "  Historial de proteccion (es la pantalla del aviso 'Amenazas actuales'):"
Write-Host "    windowsdefender://history" -ForegroundColor Cyan
Write-Host "  Seguridad de Windows -> Proteccion antivirus y contra amenazas ->"
Write-Host "    Historial de proteccion -> desplegar la entrada -> Ver detalles"
Write-Host ""
Write-Host "  Estado del motor y de las definiciones:"
try {
    $mp = Get-MpComputerStatus -ErrorAction Stop
    Write-Host "    motor $($mp.AMEngineVersion)  ·  firmas $($mp.AntivirusSignatureVersion)  ·  $($mp.AntivirusSignatureLastUpdated)"
    Write-Host "    tiempo real: $($mp.RealTimeProtectionEnabled)   nube: $($mp.MAPSReporting)   envio de muestras: $($mp.SubmitSamplesConsent)"
} catch { Write-Host "    no disponible" }

# --------------------------------------------------------------- que hacer --
Titulo "Que hacer con esto"
Write-Host @"
  1. Anota el nombre exacto. Un nombre que acaba en "!ml" viene del modelo de
     aprendizaje automatico local, no de una firma: es un veredicto estadistico
     sobre la forma del binario, y se corrige cambiando el binario (firma,
     metadatos, no comprimirlo) mucho mas que discutiendo la deteccion.

  2. Para poder seguir trabajando mientras se arregla, se excluye el directorio
     de compilacion -- NO el .exe distribuido, y nunca en la maquina de un
     usuario. Pide permisos de administrador:

       Add-MpPreference -ExclusionPath "$((Get-Location).Path)\dist"

     Para quitarla despues:
       Remove-MpPreference -ExclusionPath "$((Get-Location).Path)\dist"

  3. Si la deteccion persiste sobre un binario ya FIRMADO, es un falso positivo
     y se informa. Hay que subir el binario firmado, no el de desarrollo:

       https://www.microsoft.com/en-us/wdsi/filesubmission

     Elegir "Software developer" como tipo de envio, marcar que se cree que es
     una deteccion incorrecta, y adjuntar el .exe firmado tal cual se distribuye.
"@
Write-Host ""
