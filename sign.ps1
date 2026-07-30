# ==============================================================================
#  Firma Quilate.exe con Authenticode y comprueba que la firma vale.
#
#  Uso:
#    .\sign.ps1 -Pfx cert.pfx                      # pide la contrasena
#    .\sign.ps1 -Pfx cert.pfx -PfxPassword "..."   # o por variable de entorno
#    .\sign.ps1 -Thumbprint A1B2...                # certificado del almacen
#    .\sign.ps1 -Thumbprint A1B2... -Csp "..." -KeyContainer "..."   # token EV
#    .\sign.ps1 -SoloVerificar                     # no firma, solo comprueba
#
#  Sin certificado no hay nada que hacer aqui: firmar es lo unico que quita el
#  "Editor desconocido" de SmartScreen y del aviso de UAC, y para eso hace falta
#  un certificado de una autoridad reconocida. Este script no genera ninguno ni
#  se salta nada; si no encuentra con que firmar, lo dice y sale.
#
#  --- Que certificado comprar: OV o EV ---
#
#  OV (Organization Validation), unos 200-400 EUR/ano. La clave viene en un .pfx,
#  asi que se puede firmar desde CI pasandolo como secreto. Lo importante, porque
#  sorprende: firmar con OV NO quita el aviso de SmartScreen de golpe. Lo que hace
#  es empezar a acumular reputacion asociada al certificado, y el aviso
#  desaparece cuando esa reputacion pasa un umbral que Microsoft no publica. Con
#  pocas descargas puede tardar semanas. Aun asi firmar es imprescindible: sin
#  firma la reputacion no se acumula nunca, porque no hay a que asociarla, y cada
#  compilacion nueva parte de cero.
#
#  EV (Extended Validation), unos 400-800 EUR/ano. La reputacion es inmediata:
#  cero avisos desde la primera descarga. A cambio, la clave privada tiene que
#  vivir en hardware (token USB o HSM), asi que no hay .pfx que subir a CI, y en
#  la practica se exige ser persona juridica.
#
#  Para este proyecto —herramienta gratuita de un autor individual— lo razonable
#  es OV y aceptar la espera. Si esa espera se hace insoportable, el paso
#  siguiente no es el EV clasico sino un servicio de firma en la nube (Azure
#  Trusted Signing, DigiCert KeyLocker): dan reputacion de tipo EV y resuelven
#  ademas el problema de firmar desde CI.
#
#  Si despues de firmar Defender o SmartScreen siguen marcando el binario, es un
#  falso positivo y se informa subiendo el .exe YA FIRMADO (no el de desarrollo) a
#  https://www.microsoft.com/en-us/wdsi/filesubmission, eligiendo "Software
#  developer" como tipo de envio.
# ==============================================================================

[CmdletBinding()]
param(
    # Que firmar. Acepta ficheros y directorios: un directorio se expande a los
    # binarios que contenga. Existe en plural aunque hoy solo haya un .exe
    # porque el dia que la distribucion lleve una DLL aparte, esa DLL tiene que
    # ir firmada tambien —una firma en el .exe no cubre lo que el .exe cargue— y
    # eso no puede depender de que alguien se acuerde de anadir otra llamada.
    [string[]]$Path = @("dist\Quilate.exe"),

    [string]$Pfx,
    [string]$PfxPassword,

    # Huella del certificado dentro del almacen de Windows. Es la via de los
    # certificados EV en token USB o HSM: la clave privada no sale del
    # dispositivo, asi que no hay .pfx que pasar.
    [string]$Thumbprint,
    [string]$Csp,
    [string]$KeyContainer,

    # El sellado de tiempo es lo que hace que la firma siga valiendo despues de
    # que el certificado caduque. Sin el, el .exe deja de estar firmado el dia
    # que expire, y el usuario que se lo descargue en dos anos vuelve a ver el
    # aviso. Se usa un servidor RFC 3161 (/tr), no el protocolo viejo (/t).
    [string]$TimestampUrl = "http://timestamp.digicert.com",

    [switch]$SoloVerificar
)

# Igual que en build.ps1, y por el mismo motivo que alli esta explicado: aqui NO
# se pone `ErrorActionPreference = Stop`. signtool escribe en stderr tanto cuando
# falla como cuando informa, y con Stop la primera linea de stderr aborta el
# script entero. Eso se llevaria por delante justo lo que aqui interesa que
# funcione: el bucle que prueba otro servidor de sellado cuando el primero no
# contesta. Lo unico fiable es $LASTEXITCODE, y es lo que se comprueba.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

# ----------------------------------------------------------- buscar signtool --
function Buscar-Signtool {
    <#
      signtool.exe no viene con Windows: lo trae el SDK. Se busca en el PATH y en
      los dos Kits, y de todo lo que aparezca se coge la version mas nueva de
      x64. Ordenar por nombre de carpeta no sirve —"10.0.19041.0" y
      "10.0.22621.0" ordenan bien de casualidad, pero "10.0.9.0" y "10.0.22621.0"
      no— asi que se ordena por la version parseada.
    #>
    $enPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($enPath) { return $enPath.Source }

    $raices = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin",
        "${env:ProgramFiles(x86)}\Windows Kits\8.1\bin"
    ) | Where-Object { $_ -and (Test-Path $_) }

    $candidatos = foreach ($raiz in $raices) {
        Get-ChildItem $raiz -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.DirectoryName -match '\\(x64|amd64)(\\|$)' } |
            ForEach-Object {
                # La version va en el nombre del directorio abuelo: .../10/bin/10.0.22621.0/x64
                $carpeta = Split-Path (Split-Path $_.FullName -Parent) -Leaf
                $v = [version]"0.0.0.0"
                [void][version]::TryParse($carpeta, [ref]$v)
                [pscustomobject]@{ Ruta = $_.FullName; Version = $v }
            }
    }

    $mejor = $candidatos | Sort-Object Version -Descending | Select-Object -First 1
    if ($mejor) { return $mejor.Ruta }
    return $null
}

$signtool = Buscar-Signtool
if (-not $signtool) {
    Write-Host "No se ha encontrado signtool.exe." -ForegroundColor Red
    Write-Host ""
    Write-Host "Lo trae el SDK de Windows, en el componente 'Windows SDK Signing" -ForegroundColor Yellow
    Write-Host "Tools for Desktop Apps'. La forma corta de instalarlo:" -ForegroundColor Yellow
    Write-Host "  winget install --id Microsoft.WindowsSDK.10.0.26100 --exact" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "En Linux o macOS el equivalente es osslsigncode, que hace lo mismo" -ForegroundColor DarkGray
    Write-Host "con un .pfx pero no puede usar un token EV." -ForegroundColor DarkGray
    exit 1
}
Write-Host "signtool: $signtool" -ForegroundColor DarkGray

# ------------------------------------------------------- que hay que firmar --
$objetivos = foreach ($p in $Path) {
    if (Test-Path $p -PathType Container) {
        Get-ChildItem $p -Recurse -Include *.exe, *.dll, *.pyd -File | ForEach-Object { $_.FullName }
    } elseif (Test-Path $p) {
        (Get-Item $p).FullName
    } else {
        Write-Host "No existe: $p" -ForegroundColor Red
        exit 1
    }
}
$objetivos = @($objetivos | Select-Object -Unique)
if (-not $objetivos) { Write-Host "No hay nada que firmar." -ForegroundColor Red; exit 1 }
Write-Host "A firmar: $($objetivos.Count) fichero(s)" -ForegroundColor DarkGray

# ------------------------------------------------------------------- firmar --
function Verificar {
    param([string[]]$Ficheros)

    # Ojo con como sale la informacion de aqui. Todo lo que una funcion de
    # PowerShell escriba en la tuberia forma parte de su valor de retorno, y la
    # salida de un ejecutable nativo va a la tuberia. Sin el `| ForEach-Object`
    # de abajo, esta funcion devolvia las lineas de signtool ademas del booleano,
    # el `if (Verificar ...)` de quien llama recibia un array no vacio, y un array
    # no vacio es verdadero: daba por buena la firma de un fichero sin firmar.
    # Estaba pasando. Por eso la salida se ensena con Write-Host, que no toca la
    # tuberia, y lo unico que se devuelve es el booleano.
    #
    # `/pa` usa la politica de Authenticode, que es la que aplica a un programa.
    # Sin ella signtool valida contra la politica por defecto del controlador de
    # Windows, que es otra cosa y da un fallo enganoso en un .exe normal.
    # `/v` saca la cadena completa, que es lo que se quiere leer cuando falla.
    $ok = $true
    foreach ($f in $Ficheros) {
        Write-Host ""
        Write-Host "--- signtool verify /pa /v  $(Split-Path $f -Leaf) ---" -ForegroundColor Cyan
        # Se captura antes de ensenarlo. Sin capturar, lo que signtool manda por
        # stderr lo envuelve PowerShell en un NativeCommandError y lo pinta con
        # todo el aparato de una excepcion —"En ... Caracter: 9", la traza— para
        # una linea que solo dice "no hay firma". Asi sale el texto y nada mas.
        $salida = & $signtool verify /pa /v $f 2>&1
        $codigo = $LASTEXITCODE
        $salida | ForEach-Object { Write-Host "   $_" }
        if ($codigo -ne 0) {
            Write-Host "   -> la verificacion ha fallado" -ForegroundColor Red
            $ok = $false
            continue
        }

        # Segunda comprobacion, con otra herramienta y a proposito: signtool dice
        # si la firma es criptograficamente valida; esto dice ademas si el sello
        # de tiempo esta presente, que es lo que se olvida y no se nota hasta que
        # el certificado caduca.
        $s = Get-AuthenticodeSignature $f
        Write-Host "  Estado : $($s.Status)"
        Write-Host "  Firmante: $($s.SignerCertificate.Subject)"
        if ($s.TimeStamperCertificate) {
            Write-Host "  Sello de tiempo: si ($($s.TimeStamperCertificate.Subject))" -ForegroundColor Green
        } else {
            Write-Host "  Sello de tiempo: NO -- la firma morira con el certificado" -ForegroundColor Red
            $ok = $false
        }
    }
    return $ok
}

if ($SoloVerificar) {
    if (Verificar -Ficheros $objetivos) {
        Write-Host ""; Write-Host "Firma valida." -ForegroundColor Green; exit 0
    }
    Write-Host ""; Write-Host "La verificacion ha fallado." -ForegroundColor Red; exit 1
}

# El modo de acceso a la clave: o un .pfx, o un certificado del almacen.
$credencial = @()
if ($Pfx) {
    if (-not (Test-Path $Pfx)) { Write-Host "No existe el .pfx: $Pfx" -ForegroundColor Red; exit 1 }
    $credencial += @("/f", (Get-Item $Pfx).FullName)

    # La contrasena, por orden: parametro, variable de entorno, y si no, se pide
    # por teclado. Lo ultimo es lo preferible en una maquina de desarrollo: un
    # `/p` en la linea de ordenes se ve en la lista de procesos y se queda en el
    # historial de la consola.
    if (-not $PfxPassword) { $PfxPassword = $env:QUILATE_PFX_PASSWORD }
    if (-not $PfxPassword) {
        $segura = Read-Host "Contrasena del .pfx" -AsSecureString
        $PfxPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura))
    }
    if ($PfxPassword) { $credencial += @("/p", $PfxPassword) }
}
elseif ($Thumbprint) {
    # `/sha1` selecciona por huella y `/sm` mira el almacen de la maquina ademas
    # del del usuario. Es la via de los EV: la clave se queda en el token.
    $credencial += @("/sha1", ($Thumbprint -replace '[^0-9A-Fa-f]', ''))
    if ($Csp)          { $credencial += @("/csp", $Csp) }
    if ($KeyContainer) { $credencial += @("/kc", $KeyContainer) }
}
else {
    Write-Host "Hace falta un certificado: -Pfx <fichero> o -Thumbprint <huella>." -ForegroundColor Red
    Write-Host "Este script no genera certificados: hay que comprar uno. La" -ForegroundColor Yellow
    Write-Host "cabecera de este fichero explica cual y por que." -ForegroundColor Yellow
    exit 1
}

# Servidores de sellado de tiempo. El primero es el que se pide; los otros son
# el plan B, porque un sello de tiempo se cae de vez en cuando y perder una
# release por eso es absurdo. Todos son RFC 3161.
$selladores = @($TimestampUrl,
                "http://timestamp.sectigo.com",
                "http://timestamp.globalsign.com/tsa/r6advanced1") | Select-Object -Unique

$fallos = 0
foreach ($f in $objetivos) {
    Write-Host ""
    Write-Host "Firmando $(Split-Path $f -Leaf)..." -ForegroundColor Cyan

    $firmado = $false
    foreach ($sellador in $selladores) {
        # /fd sha256  huella del fichero con SHA-256 (SHA-1 esta muerto)
        # /td sha256  huella del sello de tiempo, tambien SHA-256. Se olvida a
        #             menudo y sin ella el sello se hace en SHA-1.
        # /tr         sellado por RFC 3161
        $argumentos = @("sign", "/fd", "sha256", "/tr", $sellador, "/td", "sha256") +
                      $credencial + @("/v", $f)
        $salida = & $signtool @argumentos 2>&1
        $codigo = $LASTEXITCODE
        $salida | ForEach-Object { Write-Host "   $_" }
        if ($codigo -eq 0) { $firmado = $true; break }
        Write-Host "  el sellador $sellador no ha respondido; probando otro" -ForegroundColor Yellow
    }

    if (-not $firmado) {
        Write-Host "No se ha podido firmar $f" -ForegroundColor Red
        $fallos++
    }
}

if ($fallos) { exit 1 }

if (-not (Verificar -Ficheros $objetivos)) {
    Write-Host ""
    Write-Host "Firmado, pero la verificacion no pasa. No distribuir esto." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Firmado y verificado." -ForegroundColor Green
Write-Host ""
Write-Host "Queda un paso que no se puede automatizar: si SmartScreen sigue" -ForegroundColor Yellow
Write-Host "avisando (normal con un certificado OV recien estrenado), hay que" -ForegroundColor Yellow
Write-Host "enviar el binario FIRMADO a Microsoft:" -ForegroundColor Yellow
Write-Host "  https://www.microsoft.com/en-us/wdsi/filesubmission" -ForegroundColor Cyan
exit 0
