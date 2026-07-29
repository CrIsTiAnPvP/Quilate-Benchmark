"""Las consultas de la auditoría que caben en un solo PowerShell.

Ocho comprobaciones preguntaban a Windows por su cuenta, y cada una arrancaba
su propio `powershell.exe`. Arrancar PowerShell cuesta bastante más que la
consulta que se le pide: cargar el intérprete, el perfil y los ensamblados es
medio segundo largo, y la consulta suele ser una décima. Ocho procesos para
ocho preguntas que caben en uno.

Se reutiliza `guion_de_bloques`, que es la misma herramienta con la que el lote
con permisos hace esto mismo y que ya está probada. Lo importante de ella es
que cada consulta va en su propio try/catch: sin eso, un `Get-PhysicalDisk`
denegado se llevaría por delante la lectura del cortafuegos y el informe daría
por no comprobado lo que sí se pudo mirar.

`check_old_drivers` NO está aquí, y es deliberado. Su consulta
—`Win32_PnPSignedDriver` sobre todos los dispositivos— tarda entre 2 y 11
segundos según el equipo, y meterla en el lote haría que las otras ocho
esperasen a la más lenta. Además va detrás de una bandera: quien no pida
`--check-drivers` no debe pagarla.
"""

from __future__ import annotations

from ..const import IS_WINDOWS
from ..platform_utils import PSResult, _ps_raw, guion_de_bloques, trocear

# Cada consulta con la clave por la que la pide su comprobación. El texto es
# exactamente el que cada una lanzaba por su cuenta: esto reordena cuándo se
# pregunta, no qué se pregunta.
CONSULTAS: dict[str, str] = {
    "discos": "Get-PhysicalDisk | "
              "Select-Object FriendlyName,HealthStatus,OperationalStatus",
    "inicio": "Get-CimInstance Win32_StartupCommand | Select-Object Name,Location",
    "dispositivos": "Get-CimInstance Win32_PnPEntity "
                    "-Filter 'ConfigManagerErrorCode <> 0' | "
                    "Select-Object Name,PNPClass,ConfigManagerErrorCode",
    "pagefile": "Get-CimInstance Win32_PageFileUsage | "
                "Select-Object Name,AllocatedBaseSize,CurrentUsage",
    "servicios": "Get-Service | Where-Object {$_.Status -eq 'Running'} | "
                 "Select-Object Name,DisplayName,StartType",
    "antivirus": 'Get-CimInstance -Namespace "root/SecurityCenter2" '
                 '-ClassName AntiVirusProduct -ErrorAction SilentlyContinue | '
                 'Select-Object displayName,productState',
    "cortafuegos": "Get-NetFirewallProfile -ErrorAction SilentlyContinue | "
                   "Select-Object Name,Enabled",
    "cuentas": "$( if (-not (Get-Command Get-LocalUser -ErrorAction SilentlyContinue)) {"
               "     [PSCustomObject]@{ disponible = $false } } else {"
               "     Get-LocalUser | Select-Object @{n='disponible';e={$true}},"
               "       Name,Enabled,PasswordRequired,@{n='SID';e={$_.SID.Value}} } )",
}

# El presupuesto del lote entero. Es holgado a propósito: son ocho consultas y
# `Get-Service` sola puede pasar del segundo en un equipo con mucho instalado.
TIEMPO_LIMITE = 45


def recoger() -> dict[str, PSResult]:
    """Lanza el lote y devuelve un `PSResult` por consulta.

    El valor de cada clave es el mismo que habría salido lanzándola suelta,
    incluida la distinción entre «falló» y «se ejecutó y no devolvió nada».
    Si el lote entero se va al traste, todas las claves salen con el mismo
    motivo, que es también lo que habría pasado una a una.
    """
    if not IS_WINDOWS:
        return trocear(None, CONSULTAS, "solo Windows")
    datos, error = _ps_raw(
        guion_de_bloques(CONSULTAS) + "$r | ConvertTo-Json -Depth 4 -Compress",
        timeout=TIEMPO_LIMITE)
    return trocear(datos, CONSULTAS,
                   error or "el lote de consultas no ha devuelto nada legible")
