"""Las comprobaciones del disco: espacio, salud y desgaste.

Cuanto queda libre, que se puede recuperar, si el medio es el que el usuario
cree, si TRIM esta donde debe y que dice SMART del desgaste. Se agrupan aparte
porque comparten fuente —el inventario de volumenes y el lote de consultas con
permisos— y porque casi todas tienen un `NoAplica` propio: preguntar por TRIM
en un disco mecanico o por la desfragmentacion en un SSD no es un dato que
falte, es una pregunta que no procede.
"""

from __future__ import annotations

import re

from .. import elevacion
from ..console import human_bytes
from ..platform_utils import _sys_exe, ps_json, run_cmd
from ..storage_scan import candidate_bytes
from ..sysinfo import KIND_LABELS, local_volumes
from .modelo import NoAplica, SinDato
from .tablas import _NEGACIONES, _SUCIO


class ChecksAlmacenamiento:
    """Mixin de `Auditor`. No se instancia sola: usa `self.si`, `self.scan` y
    `self.add()`, que los pone el `__init__` del paquete."""

    # ------------------------------------------------------- comprobaciones --
    def check_disk_space(self) -> str:
        # Solo almacenamiento físico local: una unidad de Google Drive, OneDrive
        # o de red informa de un tamaño que no es del equipo y no se libera
        # borrando archivos aquí. Auditarla solo genera avisos falsos.
        ignored = [d for d in self.si.disks if d["ignored"] and d["total"] > 5 * 1024**3]
        if ignored:
            self.notes.append(
                "Volúmenes excluidos de la auditoría de almacenamiento por no ser discos "
                "locales: " + ", ".join(
                    f"{d['mount']} ({d['label'] or KIND_LABELS.get(d['kind'], d['kind'])})"
                    for d in ignored) + ".")

        worst = None
        for d in local_volumes(self.si):
            free_pct = 100 - d["percent"]
            if worst is None or free_pct < worst[1]:
                worst = (d, free_pct)
        if not worst:
            raise SinDato("no se ha identificado ningún volumen local")
        d, free_pct = worst
        if free_pct < 10:
            self.add(
                id="disk_space", title=f"Espacio libre crítico en {d['mount']}",
                severity="critical", category="almacenamiento", component="disk",
                detail=f"Solo queda un {free_pct:.1f}% libre ({human_bytes(d['free'])}). "
                       "Por debajo del 10% los SSD pierden capacidad de wear-leveling y "
                       "escribir bloques nuevos obliga a reorganizar celdas, lo que hunde "
                       "la velocidad de escritura. Windows tampoco puede crecer el pagefile.",
                gain=0.22, gain_note="escritura y respuesta general",
                effort="bajo", risk="nulo",
                steps=[
                    "Ejecuta `cleanmgr /sageset:1` y marca Windows Update Cleanup, archivos temporales y volcados",
                    "Revisa Configuración → Sistema → Almacenamiento → Recomendaciones de limpieza",
                    "Libera puntos de restauración antiguos: `vssadmin list shadowstorage`",
                    "Objetivo: dejar al menos un 20% libre en el disco de sistema",
                ])
            return f"{free_pct:.0f}% libre en {d['mount']} (crítico)"
        if free_pct < 20:
            self.add(
                id="disk_space", title=f"Espacio libre bajo en {d['mount']}",
                severity="medium", category="almacenamiento", component="disk",
                detail=f"Queda un {free_pct:.1f}% libre ({human_bytes(d['free'])}). Se recomienda "
                       "mantener un 20% libre para que el SSD mantenga rendimiento estable.",
                gain=0.08, gain_note="estabilidad de escritura",
                effort="bajo", risk="nulo",
                steps=["Limpieza de disco (`cleanmgr`)",
                       "Desinstalar software que no uses desde Aplicaciones instaladas",
                       "Mover bibliotecas grandes (juegos, vídeo) a otra unidad"])
            return f"{free_pct:.0f}% libre en {d['mount']}"
        return f"{free_pct:.0f}% libre (correcto)"


    def check_disk_media(self) -> str:
        media = self.si.system_drive_media
        if not media or "desconocido" in media.lower():
            # De este dato dependen TRIM, desfragmentación y SysMain: darlo por
            # SSD cuando no se sabe apagaría tres avisos de golpe.
            raise SinDato("no se ha podido identificar el tipo de disco de sistema")
        if "HDD" in media and "SSD" not in media:
            self.add(
                id="hdd_system", title="El sistema arranca desde un disco mecánico (HDD)",
                severity="critical", category="almacenamiento", component="disk",
                detail="Es, con mucha diferencia, el mayor cuello de botella posible en un PC "
                       "moderno. Un HDD entrega ~100-200 IOPS aleatorias frente a las 20.000-500.000 "
                       "de un SSD NVMe. Ninguna optimización de software compensa esto: el arranque, "
                       "la apertura de programas y la carga de niveles en juegos están limitados por "
                       "acceso aleatorio, no por ancho de banda secuencial.",
                gain=0.85, gain_note="tiempos de carga y arranque (mejoras de 3-10x son habituales)",
                effort="medio", risk="bajo",
                steps=[
                    "Comprueba si la placa tiene ranura M.2 NVMe; si no, un SSD SATA 2,5\" también sirve",
                    "Instalación limpia de Windows en el SSD (preferible a clonar una instalación vieja)",
                    "Deja el HDD como almacenamiento secundario de datos, no de sistema",
                    "Tras migrar: verifica que TRIM está activo y desactiva la desfragmentación programada",
                ])
            return "HDD como disco de sistema (cuello de botella grave)"
        return f"{media}"


    def check_large_files(self) -> str:
        """Espacio recuperable según el rastreo de archivos grandes."""
        scan = self.scan
        if scan is None or not scan.available:
            raise NoAplica("rastreo de archivos no ejecutado")
        if not scan.files and not scan.special:
            return f"nada por encima de {human_bytes(scan.min_size)}"

        system_free = next((d["free"] for d in local_volumes(self.si)
                            if d["mount"].upper().startswith(str(self.si.system_drive)[:1].upper())),
                           0)
        recoverable, review = candidate_bytes(scan)
        candidates = recoverable + review

        top = ", ".join(f"{f['name'][:38]} ({human_bytes(f['size'])})" for f in scan.files[:3])
        if candidates >= 5 * 1024**3 or (recoverable >= 2 * 1024**3 and system_free
                                         and candidates > system_free * 0.1):
            severity = "medium" if candidates < 20 * 1024**3 else "high"
            self.add(
                id="large_files",
                title=f"{human_bytes(candidates)} en archivos grandes prescindibles",
                severity=severity, category="almacenamiento", component="disk",
                detail=f"El rastreo encontró {human_bytes(scan.total_large)} en ficheros de más de "
                       f"{human_bytes(scan.min_size)}, de los cuales {human_bytes(candidates)} son "
                       "temporales, cachés, volcados, instaladores ya usados o copias antiguas. "
                       "En un SSD el espacio libre no es solo capacidad: por debajo del 20% la "
                       f"velocidad de escritura cae. Los más grandes: {top}.",
                gain=0.06, gain_note="margen de escritura del SSD",
                effort="bajo", risk="bajo",
                steps=["Revisa la lista de la sección «Archivos grandes» antes de borrar nada",
                       "Ejecuta `cleanmgr /sageset:1` y marca temporales, volcados y Windows Update",
                       "Configuración → Sistema → Almacenamiento → Sensor de almacenamiento",
                       "Los instaladores y comprimidos ya usados se pueden volver a descargar",
                       "Mueve vídeos y copias a otra unidad en lugar de borrarlos"])
        note = f"{human_bytes(scan.total_large)} en {len(scan.files)} ficheros grandes"
        if candidates:
            note += f" · {human_bytes(candidates)} prescindibles"
        if scan.truncated:
            note += " (rastreo parcial)"
        return note


    def check_trim(self) -> str:
        if "SSD" not in self.si.system_drive_media:
            raise NoAplica("el disco de sistema no es un SSD")
        out = run_cmd([_sys_exe("fsutil.exe"), "behavior", "query", "DisableDeleteNotify"],
                      timeout=15)
        if not out.ok:
            raise SinDato(f"no se ha podido leer el estado de TRIM: {out.error}")
        if not out.strip():
            raise SinDato("fsutil no ha devuelto el estado de TRIM")
        disabled = any(tok in out for tok in ("= 1", "=1"))
        if disabled:
            self.add(
                id="trim_off", title="TRIM desactivado en un SSD",
                severity="high", category="almacenamiento", component="disk",
                detail="Sin TRIM, el SSD no sabe qué bloques están libres y acaba haciendo "
                       "read-modify-write constantemente. La velocidad de escritura se degrada "
                       "progresivamente y la vida útil del disco se acorta.",
                gain=0.20, gain_note="escritura sostenida y durabilidad",
                effort="bajo", risk="nulo",
                steps=["Como administrador: `fsutil behavior set DisableDeleteNotify 0`",
                       "Fuerza un TRIM manual: `defrag C: /L`",
                       "Verifica que la tarea programada «Optimizar unidades» está activa"])
            return "desactivado"
        return "activo"


    # «Está sucio» y «NO está sucio» solo se diferencian por la negación, que va
    # traducida. Buscarla como palabra suelta es lo único que aguanta el cambio
    # de idioma y que la respuesta llegue con los acentos rotos. Decirle a
    # alguien que su sistema de ficheros está corrupto —y mandarle un chkdsk /r
    # de horas— por no saber leer la respuesta es mucho peor que no decir nada,
    # así que ante la duda no se informa.

    def check_filesystem_health(self) -> str:
        rows = elevacion.recoger()["fsdirty"]
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar el volumen ({rows.error})")
        if not rows:
            raise SinDato("fsutil no ha respondido")
        drive = str(rows[0].get("unidad") or "C:")
        codigo = rows[0].get("codigo")
        out = str(rows[0].get("salida") or "")
        if codigo:
            # `fsutil dirty query` sale con código de error cuando no puede
            # mirar. Decirlo es la diferencia entre «prueba con permisos» y
            # «este Windows no trae fsutil».
            raise SinDato(f"fsutil ha terminado con código {codigo} "
                          f"(suele requerir administrador)")
        if not out:
            raise SinDato("fsutil no ha respondido")
        lowered = out.lower()
        if set(re.split(r"[^\w]+", lowered)) & _NEGACIONES:
            return "limpio"
        if not any(termino in lowered for termino in _SUCIO):
            raise SinDato(f"respuesta de fsutil no reconocida: «{out[:60]}»")
        self.add(
            id="fs_dirty", title="El volumen de sistema está marcado como «sucio»",
            severity="high", category="almacenamiento", component="disk",
            detail="Windows ha detectado inconsistencias en el sistema de archivos y programará "
                   "una comprobación. Puede indicar un apagado incorrecto o, más preocupante, "
                   "un disco con sectores defectuosos.",
            gain=0.10, gain_note="estabilidad y velocidad de E/S",
            effort="bajo", risk="bajo",
            steps=[f"Programa una comprobación: `chkdsk {drive} /f /r` y reinicia",
                   "Revisa el estado SMART del disco con CrystalDiskInfo",
                   "Haz copia de seguridad antes de nada si SMART muestra advertencias"])
        return "marcado como sucio"


    def check_defrag(self) -> str:
        if "HDD" not in self.si.system_drive_media:
            raise NoAplica("desfragmentar un SSD solo desgasta celdas")
        self.add(
            id="defrag_hdd", title="Desfragmentación recomendable en disco mecánico",
            severity="low", category="almacenamiento", component="disk",
            detail="En HDD la fragmentación obliga al cabezal a saltar entre zonas del plato. "
                   "Desfragmentar recupera velocidad secuencial. (En SSD no se debe hacer nunca: "
                   "solo desgasta celdas.)",
            gain=0.07, gain_note="lectura secuencial en HDD",
            effort="bajo", risk="nulo",
            steps=["`defrag C: /U /V /O` como administrador",
                   "Comprueba que la tarea programada «Optimizar unidades» está activa (semanal)"])
        return "pendiente de optimizar"


    def check_smart(self) -> str:
        rows = self._consulta("discos")
        if not getattr(rows, "ok", True) or not rows:
            raise SinDato("no se ha podido consultar el estado de los discos"
                          + (f" ({rows.error})" if getattr(rows, "error", None) else ""))
        bad = [r for r in rows if str(r.get("HealthStatus", "")).lower() not in ("healthy", "0", "sano")]
        if bad:
            names = ", ".join(str(r.get("FriendlyName")) for r in bad)
            self.add(
                id="smart_warn", title=f"Disco con estado de salud degradado ({names})",
                severity="critical", category="almacenamiento", component="disk",
                detail="Windows informa de un estado distinto de «Healthy». Antes de optimizar "
                       "nada, haz copia de seguridad: un disco en degradación puede fallar sin "
                       "más aviso y explica por sí solo cualquier lentitud.",
                gain=0.0, gain_note="no es una optimización: es riesgo de pérdida de datos",
                effort="alto", risk="alto",
                steps=["Copia de seguridad inmediata de los datos importantes",
                       "Revisa los atributos SMART con CrystalDiskInfo (reallocated sectors, pending)",
                       "Planifica la sustitución del disco"])
            return f"degradado: {names}"

        extra = self._check_disk_wear()
        self._check_disco_enlace()
        return extra or f"{len(rows)} disco(s) sano(s)"

    def _check_disco_enlace(self) -> None:
        """Errores de enlace: primero el cable, y solo después el disco.

        Los atributos 199 (errores de comprobación en la transmisión) y 188
        (comandos que expiran sin respuesta) no dicen que el disco esté
        estropeado: dicen que lo que viaja entre el disco y la placa se corrompe
        o no llega. La causa más frecuente con diferencia es un cable SATA mal
        encajado, de mala calidad o pinzado al cerrar la caja, y cambiarlo
        cuesta dos euros. Por eso van aparte de los sectores y con su propio
        identificador: mandar a alguien a comprar un disco por esto sería
        hacerle tirar el dinero y no arreglarle el problema.

        Va en su propio método y no dentro de `_check_disk_wear` a propósito.
        Esa función ya recorre cuatro listas a la vez y tiene la complejidad al
        límite; meterle una quinta rama la habría dejado sin poder tocar.
        """
        afectados = []
        for disk in self.si.physical_disks:
            crc = disk.get("crc_errors") or 0
            expirados = disk.get("command_timeout") or 0
            if crc or expirados:
                afectados.append((str(disk.get("name") or "disco"), crc, expirados))
        if not afectados:
            return

        def cuenta(crc: int, expirados: int) -> str:
            partes = []
            if crc:
                partes.append(f"{crc} de transmisión")
            if expirados:
                partes.append(f"{expirados} comandos expirados")
            return ", ".join(partes)

        lista = "; ".join(f"{n} ({cuenta(c, t)})" for n, c, t in afectados)
        self.add(
            id="disco_cable",
            title=f"Errores de comunicación con el disco ({lista})",
            severity="medium", category="almacenamiento", component="disk",
            detail="El disco y la placa no se están entendiendo bien: hay datos que llegan "
                   "corruptos y hay que reenviarlos, o comandos que no obtienen respuesta a "
                   "tiempo. Ojo con la conclusión, porque es la parte que más se equivoca: "
                   "esto casi nunca significa que el disco esté estropeado. Lo normal es un "
                   "cable SATA mal encajado, de mala calidad o pinzado al cerrar la caja, y a "
                   "veces una alimentación justa. El síntoma que se nota es el equipo "
                   "congelándose unos segundos sin motivo aparente. Estos contadores no bajan "
                   "nunca aunque se arregle la causa, así que lo que importa es si suben.",
            gain=0.0,
            gain_note="no es una optimización: son datos que viajan mal entre el disco y la placa",
            effort="bajo", risk="bajo",
            steps=["Apaga, desenchufa y vuelve a encajar el cable SATA por los dos extremos",
                   "Si tienes otro cable, cámbialo: es la causa más habitual con diferencia",
                   "Prueba otro conector SATA de la placa y otro cable de alimentación",
                   "Apunta el valor actual y míralo dentro de unos días: si no sube, ya está "
                   "resuelto; si sigue subiendo con otro cable, entonces sí sospecha del disco"])


    def _check_disk_wear(self) -> str:
        """Desgaste y errores acumulados, que avisan mucho antes que HealthStatus.

        Un SSD al 90% de vida consumida sigue reportándose como «Healthy» hasta
        que deja de funcionar. Sin privilegios estos contadores no se leen, y
        entonces no se afirma nada: ausencia de dato no es ausencia de problema.
        """
        gastados, con_errores, calientes, con_sectores = [], [], [], []
        medidos = 0
        for disk in self.si.physical_disks:
            nombre = str(disk.get("name") or "disco")
            desgaste = disk.get("wear")
            errores = (disk.get("read_errors") or 0) + (disk.get("write_errors") or 0)
            grados = disk.get("temperature")
            # Los sectores solo los publican los ATA/SATA, así que un NVMe llega
            # con desgaste y sin ellos, y un HDD al revés si el contador de
            # fiabilidad no contestó. Cualquiera de los dos cuenta como medido.
            sectores = {campo: disk.get(campo)
                        for campo in ("reallocated", "pending", "uncorrectable",
                                      "reported_uncorrectable")
                        if disk.get(campo) is not None}
            if (desgaste is None and grados is None and not sectores
                    and disk.get("power_on_hours") is None):
                continue
            medidos += 1
            # Los pendientes son sectores que el disco ya no consigue leer y aún
            # no ha sustituido: son los que más avisan y los que peor van a más.
            if (sectores.get("pending") or sectores.get("uncorrectable")
                    or sectores.get("reported_uncorrectable")):
                con_sectores.append((nombre, sectores, True))
            elif sectores.get("reallocated"):
                con_sectores.append((nombre, sectores, False))
            # El desgaste es un contador de ciclos de escritura: en un disco
            # mecánico no significa nada, y los HDD lo devuelven igualmente a 0.
            es_ssd = "SSD" in str(disk.get("media") or "").upper()
            if es_ssd and desgaste is not None and desgaste >= 60:
                gastados.append((nombre, desgaste))
            if errores > 0:
                con_errores.append((nombre, errores))
            # Los USB suelen mentir con la temperatura; solo se mira lo interno.
            if grados and grados >= 65 and str(disk.get("bus") or "").upper() != "USB":
                calientes.append((nombre, grados))

        if con_errores:
            lista = ", ".join(f"{n} ({e} errores)" for n, e in con_errores)
            self.add(
                id="disk_errors", title=f"Errores de lectura/escritura no corregidos ({lista})",
                severity="critical", category="almacenamiento", component="disk",
                detail="El disco ha registrado operaciones que no pudo corregir. Es pérdida de "
                       "datos ya ocurrida, no un riesgo futuro, y Windows sigue informando del "
                       "disco como «Healthy» hasta que falla del todo.",
                gain=0.0, gain_note="no es una optimización: es pérdida de datos",
                effort="alto", risk="alto",
                steps=["Copia de seguridad inmediata, antes de cualquier otra cosa",
                       "Comprueba los atributos completos con CrystalDiskInfo o smartctl",
                       "Sustituye el disco: los errores no corregidos no se arreglan"])

        if con_sectores:
            urgente = any(grave for _, _, grave in con_sectores)

            def cuenta(datos: dict) -> str:
                partes = []
                if datos.get("pending"):
                    partes.append(f"{datos['pending']} pendientes")
                if datos.get("reallocated"):
                    partes.append(f"{datos['reallocated']} reasignados")
                if datos.get("uncorrectable"):
                    partes.append(f"{datos['uncorrectable']} irrecuperables")
                if datos.get("reported_uncorrectable"):
                    partes.append(f"{datos['reported_uncorrectable']} no corregidos")
                return ", ".join(partes)

            lista = "; ".join(f"{n} ({cuenta(d)})" for n, d, _ in con_sectores)
            self.add(
                id="disk_sectores", title=f"Sectores defectuosos en el disco ({lista})",
                severity="high" if urgente else "medium",
                category="almacenamiento", component="disk",
                detail="Un sector reasignado es un trozo del disco que se estropeó y que el "
                       "firmware sustituyó por uno de repuesto, sin que Windows se entere: el "
                       "disco sigue diciendo «Healthy» y la reserva de repuestos es limitada. "
                       "Los pendientes son peores, porque son sectores que ya no se leen y que "
                       "todavía no se han sustituido: si ahí había un archivo, ese archivo ya "
                       "no está entero. Es el aviso más temprano que da un disco mecánico, y "
                       "llega meses antes de que falle nada visible.",
                gain=0.0, gain_note="no es una optimización: es un disco empezando a fallar",
                effort="medio", risk="alto" if urgente else "medio",
                steps=["Copia de seguridad de lo que haya ahí, hoy y no la semana que viene",
                       "Vigila la cuenta con CrystalDiskInfo: lo que importa no es el número, "
                       "es si sube. Estable durante meses puede convivir; subiendo, no",
                       "`chkdsk /r` obliga al disco a releerlo todo y a sustituir lo que no "
                       "pueda leer: tarda horas y conviene hacerlo con la copia ya hecha",
                       "Si la cuenta sube, sustituye el disco antes de que se lleve algo"])

        if gastados:
            peor = max(gastados, key=lambda x: x[1])
            severidad = "critical" if peor[1] >= 90 else "high" if peor[1] >= 80 else "medium"
            self.add(
                id="disk_wear", title=f"{peor[0]}: {peor[1]}% de vida útil consumida",
                severity=severidad, category="almacenamiento", component="disk",
                detail=f"El contador de desgaste del disco va por el {peor[1]}%. Es una "
                       "estimación del propio firmware sobre los ciclos de escritura "
                       "consumidos. Cerca del 100% el disco pasa a solo lectura o falla, y "
                       "antes de eso la velocidad de escritura ya se degrada.",
                gain=0.0, gain_note="no es una optimización: es vida restante del disco",
                effort="alto", risk="bajo",
                steps=["Planifica la sustitución antes de llegar al 100%",
                       "Mantén copia de seguridad al día mientras tanto",
                       "Evita moverle cargas de escritura intensiva (torrents, edición, swap)"])

        if calientes:
            peor = max(calientes, key=lambda x: x[1])
            self.add(
                id="disk_hot", title=f"{peor[0]} a {peor[1]} °C",
                severity="medium", category="térmico", component="disk",
                detail="Por encima de 65-70 °C los SSD NVMe reducen velocidad para protegerse, "
                       "y el calor sostenido acorta su vida. Suele ser falta de disipador o de "
                       "flujo de aire, no un defecto del disco.",
                gain=0.08, gain_note="velocidad sostenida de disco",
                effort="medio", risk="bajo",
                steps=["Monta el disipador del M.2 si la placa lo trae y no está puesto",
                       "Mejora el flujo de aire de la caja",
                       "Aléjalo de la gráfica si comparte espacio con ella"])

        if not medidos:
            return "sin contadores de fiabilidad (requiere administrador)"
        partes = [f"{medidos} disco(s) con contadores"]
        if gastados:
            partes.append(f"desgaste máx {max(d for _, d in gastados)}%")
        if con_errores:
            partes.append("con errores no corregidos")
        return " · ".join(partes)
