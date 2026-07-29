"""Salud de los discos: lo que el firmware sabe y Windows no siempre cuenta.

Dos fuentes que se complementan. `Get-StorageReliabilityCounter` es nativa,
llega ya interpretada y da desgaste, horas y errores acumulados, pero pide
privilegios y no publica los contadores de sectores. Esos hay que sacarlos del
blob SMART de 512 bytes leyendolo byte a byte, y ahi esta la parte delicada:
media docena de decisiones que solo se entienden con un disco delante, y que
estan documentadas una a una en cada funcion.

Se separa de `sysinfo` porque no es inventario. El inventario dice que hay;
esto dice como esta, y es lo unico del modulo que puede acabar diciendole a
alguien que haga una copia de seguridad esta misma tarde.

No depende de nada del resto del paquete: solo stdlib.
"""

from __future__ import annotations


def _storage_reliability(rows) -> dict[int, dict]:
    """Desgaste, horas de uso y errores acumulados de cada disco físico.

    Sale de Get-StorageReliabilityCounter, que es nativo de Windows y expone los
    atributos SMART que de verdad importan. Requiere privilegios de
    administrador: sin ellos devuelve vacío, y quien llame debe tratar la
    ausencia como «no medido», nunca como «disco nuevo».

    Un campo a None significa que ese disco no publica el atributo. Es habitual
    en discos USB y en algunos SATA antiguos.
    """
    def num(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    out: dict[int, dict] = {}
    for row in rows:
        number = num(row.get("DeviceId"))
        if number is None:
            continue
        out[number] = {
            "wear": num(row.get("Wear")),
            "temperature": num(row.get("Temperature")),
            "power_on_hours": num(row.get("PowerOnHours")),
            "read_errors": num(row.get("ReadErrorsUncorrected")),
            "write_errors": num(row.get("WriteErrorsUncorrected")),
        }
    return out


# Los atributos SMART que `Get-StorageReliabilityCounter` no expone, y que son el
# aviso más temprano que da un disco. Los tres primeros son sectores: los que el
# firmware ya ha tenido que sustituir, los que no consigue leer y todavía no ha
# sustituido, y los que ha dado por irrecuperables.
#
# Los tres siguientes no cuestan ni una consulta más —vienen en el mismo blob de
# 512 bytes que ya se lee— y dicen cosas distintas:
#   · 187 son errores que el disco no pudo corregir y reportó al sistema. Junto
#     con 5, 197 y 198 forma el grupo que mejor predice un fallo próximo.
#   · 188 son comandos que expiraron sin respuesta.
#   · 199 son errores de comprobación en lo que viaja por el cable.
# Los dos últimos suelen ser del enlace y no del disco: un cable SATA mal
# encajado da CRC y timeouts en un disco perfectamente sano.
_ATRIBUTOS_SMART = {5: "reallocated", 197: "pending", 198: "uncorrectable",
                    187: "reported_uncorrectable", 188: "command_timeout",
                    199: "crc_errors"}


def _smart_atributos(blob) -> dict[int, int]:
    """Los valores en bruto del blob SMART de 512 bytes, por identificador.

    La estructura son 30 huecos de 12 bytes a partir del offset 2 —los dos
    primeros son la revisión—: identificador (1), banderas (2), valor
    normalizado (1), peor valor (1), valor en bruto (6) y un byte reservado.

    Verificado contra CrystalDiskInfo en dos discos SATA: horas encendido,
    ciclos de encendido, tiempo de arranque y ciclos de carga coinciden byte a
    byte. Solo se leen de aquí los tres contadores de sectores, que son enteros
    de verdad. La temperatura NO se saca de este blob aunque esté: varios
    fabricantes meten la mínima y la máxima en los bytes altos del mismo campo,
    y leerlo como un entero de 48 bits daba 25.769.803.815 °C en el Seagate de
    pruebas. Esa la sigue dando `Get-StorageReliabilityCounter`, que ya la
    entrega interpretada.
    """
    if not isinstance(blob, (list, tuple)) or len(blob) < 2 + 30 * 12:
        return {}
    try:
        crudo = bytes(blob[:2 + 30 * 12])
    except (TypeError, ValueError):
        return {}
    out: dict[int, int] = {}
    for hueco in range(30):
        base = 2 + hueco * 12
        identificador = crudo[base]
        # El identificador 0 marca un hueco sin usar, y los hay intercalados.
        if identificador == 0:
            continue
        out[identificador] = int.from_bytes(crudo[base + 5:base + 11], "little")
    return out


def _modelo_normalizado(texto: str) -> str:
    """El modelo en la forma en que se puede comparar entre dos fuentes.

    En un `InstanceName` los espacios viajan como guiones bajos, así que
    «Samsung SSD 860» llega como «Samsung_SSD_860».
    """
    return " ".join(str(texto).replace("_", " ").upper().split())


def _modelo_de_instancia(nombre: str) -> str:
    """El modelo que lleva dentro un `InstanceName`, y nada más que el modelo.

    El resto de la cadena es la dirección del dispositivo dentro de su
    controlador y, en USB y en NVMe, puede incluir el número de serie del disco.
    Aquí se usa para casar con `FriendlyName` y se tira: ni se guarda ni se
    enseña, por lo mismo que el histórico no guarda rutas.
    """
    partes = str(nombre).split("\\")
    if len(partes) < 2:
        return ""
    campos = {}
    for trozo in partes[1].split("&"):
        clave, _, valor = trozo.partition("_")
        if _:
            campos[clave.lower()] = valor
    return _modelo_normalizado(f"{campos.get('ven', '')} {campos.get('prod', '')}")


def _smart_por_modelo(rows) -> dict[str, dict]:
    """Contadores de sectores de cada disco, indexados por modelo.

    Se indexa por modelo y no por número de disco porque no hay forma fiable de
    sacar el número: se repite mucho que el `_N` del final del `InstanceName` es
    el índice físico, y es falso. Comprobado en el equipo de pruebas, los dos
    discos SATA —que son el 0 y el 1— terminan los dos en `_0`.

    Dos discos del mismo modelo se descartan los dos. Es el caso de quien monta
    dos unidades iguales, y ahí adjudicarle a uno los sectores del otro sería
    peor que no decir nada.
    """
    candidatos: dict[str, list[dict]] = {}
    for row in rows:
        modelo = _modelo_de_instancia(row.get("InstanceName") or "")
        atributos = _smart_atributos(row.get("VendorSpecific"))
        if not modelo or not atributos:
            continue
        contadores = {campo: atributos[aid]
                      for aid, campo in _ATRIBUTOS_SMART.items() if aid in atributos}
        if contadores:
            candidatos.setdefault(modelo, []).append(contadores)
    return {modelo: filas[0] for modelo, filas in candidatos.items() if len(filas) == 1}


def _smart_del_disco(nombre, smart: dict[str, dict]) -> dict:
    """Los contadores del disco que se llama así, si se puede saber cuál es.

    Las dos fuentes recortan el modelo por sitios distintos: WMI da «WDC
    WD20EZRZ-00Z5HB0» completo, pero al Seagate lo deja en «ST1000DM010-2EP1»
    cuando `Get-PhysicalDisk` lo llama «ST1000DM010-2EP102». Por eso vale que
    uno empiece por el otro, en cualquiera de los dos sentidos.
    """
    modelo = _modelo_normalizado(nombre or "")
    if not modelo:
        return {}
    encajan = [datos for clave, datos in smart.items()
               if modelo.startswith(clave) or clave.startswith(modelo)]
    return encajan[0] if len(encajan) == 1 else {}
