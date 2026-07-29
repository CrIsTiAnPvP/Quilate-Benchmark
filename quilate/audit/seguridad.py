"""Las comprobaciones de seguridad: lo que no acelera nada.

Diez comprobaciones que no prometen ni un punto de rendimiento. Cifrar el
disco, tener el arranque seguro activo o quitar SMB1 no hacen que el equipo
vaya mas rapido, y por eso sus hallazgos van con `gain=0.0` y una nota que lo
dice: sin eso entrarian en la proyeccion de mejora y el informe acabaria
prometiendo un «+8% de fluidez» por cifrar el disco, que es falso.

Van en una categoria propia, `SEGURIDAD`, que las tres salidas sacan en un
bloque aparte del plan de accion. Ver `security_findings` en `modelo`.

Aqui es donde van los escaneres de seguridad nuevos.
"""

from __future__ import annotations

from datetime import date, datetime

from .. import elevacion
from ..platform_utils import pending_security_updates, ps_json, reg_read, winreg
from .modelo import SEGURIDAD, NoAplica, SinDato
from .tablas import (_CIFRADO_NO, _CIFRADO_SI, _EDICIONES_LARGAS,
                     _FIREWALL_ACTIVO, _MSRC_GRAVES, _MSRC_SERIAS,
                     _PERFILES_EXPUESTOS, _RDP_CLAVE, _RDP_TCP_CLAVE,
                     _SMB1_ACTIVO, _SMB1_INACTIVO, _SOPORTE_REVISADO,
                     _SOPORTE_SUELO, _SOPORTE_WINDOWS, _build_de, _clave,
                     _estado_antivirus, _tabla_de_soporte_caducada)


class ChecksSeguridad:
    """Mixin de `Auditor`. No se instancia sola: usa `self.si`, `self.add()` y
    el lote de consultas con permisos, que los pone el `__init__` del paquete."""

    def check_antivirus(self) -> str:
        rows = ps_json('Get-CimInstance -Namespace "root/SecurityCenter2" -ClassName AntiVirusProduct '
                       '-ErrorAction SilentlyContinue | Select-Object displayName,productState')
        if not getattr(rows, "ok", True):
            raise SinDato(f"no se ha podido consultar el Centro de seguridad ({rows.error})")
        names = [str(r.get("displayName")) for r in rows if r.get("displayName")]
        if not names:
            # Windows registra siempre Defender aquí; una lista vacía significa
            # que el Centro de seguridad no ha contestado, no que no haya nada.
            raise SinDato("el Centro de seguridad no ha devuelto ningún producto")
        self._check_proteccion_activa(rows)
        third_party = [n for n in names if "defender" not in n.lower()]
        if len(third_party) >= 2:
            self.add(
                id="av_stack", title=f"Varios antivirus instalados ({', '.join(third_party)})",
                severity="high", category="fluidez", component="system",
                detail="Dos motores de análisis en tiempo real se escanean mutuamente. El resultado "
                       "es un impacto grande en la E/S de disco, conflictos y menor protección real, "
                       "no mayor.",
                gain=0.20, gain_note="E/S de disco y fluidez",
                effort="bajo", risk="bajo",
                steps=["Deja un único antivirus en tiempo real",
                       "Desinstala el resto con la herramienta de limpieza oficial del fabricante",
                       "Windows Defender es suficiente para la mayoría de usuarios"])
            return f"{len(names)} productos ({', '.join(names)})"
        return ", ".join(names)


    # -------------------------------------------------------- seguridad ------
    # Estos hallazgos no aceleran el equipo, así que van todos con `gain=0.0` y
    # una nota que lo dice. Emitirlos con ganancia los metería en la proyección
    # y el informe acabaría prometiendo «+8% de fluidez por cifrar el disco»,
    # que es falso. El patrón ya estaba en `smart_warn`.

    # Cada cuánto se decide que una BIOS se ha quedado atrás. No se cruza con
    # CVE concretas a propósito: eso exigiría una base de datos externa y
    # rompería el «no envía nada a ninguna parte». Lo que sí es cierto sin
    # consultar nada es que Intel y AMD han publicado microcódigo después.
    BIOS_VIEJA_AÑOS = 3

    BIOS_MUY_VIEJA_AÑOS = 5

    def check_bios_age(self) -> str:
        if not self.si.bios_date:
            raise SinDato("el sistema no informa de la fecha de su BIOS")
        try:
            fecha = datetime.strptime(self.si.bios_date, "%Y-%m-%d").date()
        except ValueError:
            raise SinDato(f"fecha de BIOS no reconocida: «{self.si.bios_date}»") from None
        dias = (date.today() - fecha).days
        años = dias / 365.25
        if años < self.BIOS_VIEJA_AÑOS:
            return f"{self.si.bios_date} ({años:.0f} años)"
        severidad = "medium" if años >= self.BIOS_MUY_VIEJA_AÑOS else "low"
        self.add(
            id="bios_vieja", title=f"BIOS de {fecha.year}, con {años:.0f} años de antigüedad",
            severity=severidad, category=SEGURIDAD, component="system",
            detail=f"La BIOS de este equipo es del {self.si.bios_date}. Desde entonces Intel y "
                   "AMD han publicado varias revisiones de microcódigo, que es la vía por la "
                   "que se corrigen los fallos del propio procesador —los de la familia de "
                   "Spectre entre ellos— y que solo llegan al equipo dentro de una "
                   "actualización de BIOS. No se ha comprobado si a este modelo le falta "
                   "alguna en concreto: eso exigiría consultar una base de datos externa, y "
                   "este programa no envía nada a ninguna parte.",
            gain=0.0, gain_note="no es una optimización: son correcciones del procesador",
            effort="medio", risk="medio",
            steps=["Mira el modelo exacto de tu placa o portátil en la web del fabricante",
                   "Compara la versión publicada con la que tienes en el inventario de arriba",
                   "Actualiza con el equipo enchufado a la corriente y sin apagarlo a medias",
                   "Si no hay ninguna posterior, no hay nada que hacer: no fuerces una igual"])
        return f"{self.si.bios_date} ({años:.0f} años)"


    @staticmethod
    def _estado_cifrado(valor) -> bool | None:
        # Las dos formas en las que `Get-BitLockerVolume` puede contestar, y el
        # criterio de qué hacer con lo que no sea ninguna, están en `tablas`.
        if isinstance(valor, bool):
            return None
        clave = valor.strip().lower() if isinstance(valor, str) else valor
        if clave in _CIFRADO_SI:
            return True
        if clave in _CIFRADO_NO:
            return False
        return None


    def check_disk_encryption(self) -> str:
        # La disponibilidad se resuelve con `Get-Command` y no leyendo el texto
        # del error: en Windows Home el cmdlet no existe y el mensaje viene
        # traducido, así que buscarlo por su texto es exactamente el fallo que
        # `check_filesystem_health` documenta y evita.
        rows = elevacion.recoger()["bitlocker"]
        if not rows.ok:
            # Sin privilegios el cmdlet existe pero rechaza contestar. No es «no
            # aplica»: es que no se ha podido mirar.
            raise SinDato(f"no se ha podido consultar BitLocker ({rows.error})")
        if not rows:
            raise SinDato("BitLocker no ha devuelto ningún volumen")
        if not rows[0].get("disponible"):
            raise NoAplica("esta edición de Windows no incluye BitLocker "
                           "(las ediciones Home no lo traen)")

        unidad = (self.si.system_drive or "C:").rstrip("\\")
        sistema = next((r for r in rows
                        if str(r.get("MountPoint") or "").rstrip("\\").upper() == unidad.upper()),
                       None)
        if sistema is None:
            raise SinDato(f"BitLocker no informa del volumen de sistema ({unidad})")
        protegido = self._estado_cifrado(sistema.get("ProtectionStatus"))
        if protegido is None:
            protegido = self._estado_cifrado(sistema.get("VolumeStatus"))
        if protegido is None:
            raise SinDato(f"estado de BitLocker no reconocido en {unidad}: "
                          f"«{sistema.get('ProtectionStatus')}»")
        if protegido:
            return f"{unidad} cifrado"

        self.add(
            id="sin_cifrado", title=f"El disco del sistema ({unidad}) no está cifrado",
            severity="high", category=SEGURIDAD, component="disk",
            detail="Sin cifrado, cualquiera que se lleve el equipo —o solo el disco— lee todos "
                   "los archivos sin necesidad de tu contraseña: basta con arrancar desde un USB "
                   "o poner la unidad en otro ordenador. La contraseña de Windows no protege los "
                   "datos, solo la sesión. Importa sobre todo en portátiles, que es donde se "
                   "pierden y se roban.",
            gain=0.0, gain_note="no es una optimización: es confidencialidad de tus archivos",
            effort="medio", risk="medio",
            steps=["Panel de control → Cifrado de unidad BitLocker → Activar BitLocker",
                   "GUARDA LA CLAVE DE RECUPERACIÓN antes de seguir, fuera de este equipo: "
                   "sin ella, un fallo de la placa o del TPM deja los datos irrecuperables",
                   "El cifrado inicial tarda y conviene hacerlo con el portátil enchufado",
                   "Si tu edición de Windows no lo incluye, VeraCrypt hace lo mismo"])
        return f"{unidad} SIN cifrar"


    def check_secure_boot(self) -> str:
        # El tipo de firmware sale de `$env:firmware_type`, que Windows rellena
        # con «UEFI» o «Legacy» y no está traducido. La alternativa era mirar el
        # texto de la excepción que lanza `Confirm-SecureBootUEFI` en un equipo
        # sin UEFI, y ese sí viene en el idioma del sistema.
        rows = elevacion.recoger()["secureboot"]
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar el arranque seguro ({rows.error})")
        if not rows:
            raise SinDato("el sistema no ha informado del tipo de firmware")
        firmware = str(rows[0].get("firmware") or "").strip().lower()
        if firmware and firmware != "uefi":
            raise NoAplica("este equipo arranca con BIOS heredada, que no tiene arranque seguro")
        activo = rows[0].get("activo")
        if not isinstance(activo, bool):
            # Sin privilegios el cmdlet contesta «Acceso denegado» y aquí llega
            # como None. Eso no es «desactivado».
            raise SinDato("el estado del arranque seguro requiere administrador")
        if activo:
            return "activo"
        self.add(
            id="secureboot_off", title="Arranque seguro (Secure Boot) desactivado",
            severity="medium", category=SEGURIDAD, component="system",
            detail="Con el arranque seguro apagado, el firmware no comprueba la firma de lo que "
                   "carga antes que Windows, así que un bootkit —malware que se instala por "
                   "debajo del sistema operativo— puede arrancar antes que el antivirus y "
                   "volverse invisible para él. Es además uno de los requisitos de Windows 11, "
                   "así que tenerlo apagado puede ser por lo que este equipo no da el salto.",
            gain=0.0, gain_note="no es una optimización: es integridad del arranque",
            effort="medio", risk="medio",
            steps=["Entra en la UEFI al encender (suele ser Supr, F2 o F10)",
                   "Busca «Secure Boot» y actívalo; puede exigir poner el modo de arranque "
                   "en UEFI puro y quitar CSM/Legacy",
                   "Si el disco usa MBR habrá que convertirlo a GPT antes, con `mbr2gpt`: "
                   "haz copia de seguridad primero",
                   "Si arrancas Linux en el mismo equipo, comprueba que tu distribución "
                   "esté firmada antes de activarlo"])
        return "desactivado"


    def check_tpm(self) -> str:
        rows = elevacion.recoger()["tpm"]
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar el TPM ({rows.error})")
        if not rows:
            raise SinDato("Get-Tpm no ha devuelto nada")
        if not rows[0].get("disponible"):
            raise NoAplica("esta edición de Windows no trae la consulta del TPM")
        presente = rows[0].get("TpmPresent")
        if not isinstance(presente, bool):
            # Verificado: sin privilegios `Get-Tpm` no falla, devuelve los campos
            # a null. Darlos por «no hay TPM» acusaría de faltarle el chip a casi
            # cualquier equipo, que es peor que no decir nada.
            raise SinDato("el estado del TPM requiere administrador")
        if not presente:
            self.add(
                id="sin_tpm", title="El equipo no tiene TPM",
                severity="medium", category=SEGURIDAD, component="system",
                detail="El TPM es el chip donde se guardan las claves de cifrado de forma que no "
                       "se puedan copiar. Sin él, BitLocker necesita que escribas una contraseña "
                       "en cada arranque, y este equipo no cumple los requisitos de Windows 11. "
                       "Muchas placas lo traen desactivado de fábrica bajo otro nombre: fTPM en "
                       "AMD, PTT en Intel.",
                gain=0.0, gain_note="no es una optimización: es dónde viven tus claves",
                effort="medio", risk="bajo",
                steps=["Entra en la UEFI y busca «fTPM» (AMD) o «Intel PTT»: suele estar ahí, "
                       "solo que apagado",
                       "Si no aparece ninguno, este equipo no lo tiene y no se le puede añadir",
                       "Comprueba después con `tpm.msc`"])
            return "no hay TPM"
        if rows[0].get("TpmEnabled") is False:
            self.add(
                id="tpm_desactivado", title="El equipo tiene TPM pero está desactivado",
                severity="medium", category=SEGURIDAD, component="system",
                detail="El chip está presente y apagado desde la UEFI, así que ni BitLocker "
                       "puede usarlo ni cuenta para los requisitos de Windows 11. Activarlo es "
                       "cambiar un ajuste, no comprar nada.",
                gain=0.0, gain_note="no es una optimización: es dónde viven tus claves",
                effort="bajo", risk="bajo",
                steps=["Entra en la UEFI y activa «fTPM» (AMD) o «Intel PTT»",
                       "Si ya usabas BitLocker, ten a mano la clave de recuperación: tocar el "
                       "TPM puede pedirla en el siguiente arranque",
                       "Comprueba después con `tpm.msc`"])
            return "presente pero desactivado"
        return "presente y activo"


    # `Get-WindowsOptionalFeature` devuelve una enumeración que, según la
    # versión, llega como texto o como el entero de DISM. Lo que no encaje en
    # ninguna de las dos formas se declara desconocido: dar por desactivado un
    # SMB1 que está activo sería justo el error que importa evitar.

    def check_smb1(self) -> str:
        rows = elevacion.recoger()["smb1"]
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar SMB1 ({rows.error})")
        if not rows:
            raise SinDato("Windows no ha informado del estado de SMB1")
        if not rows[0].get("disponible"):
            raise NoAplica("este Windows no permite consultar las características opcionales")
        estado = rows[0].get("State")
        clave = estado.strip().lower() if isinstance(estado, str) else estado
        if clave in _SMB1_INACTIVO:
            return "desactivado"
        if clave not in _SMB1_ACTIVO:
            raise SinDato(f"estado de SMB1 no reconocido: «{estado}»")
        self.add(
            id="smb1_activo", title="SMB1 sigue activo, un protocolo retirado en 2014",
            severity="high", category=SEGURIDAD, component="system",
            detail="SMB1 es la versión antigua del protocolo de archivos compartidos de Windows. "
                   "Microsoft lo dejó de instalar por defecto porque no se puede asegurar: no "
                   "firma los mensajes ni cifra nada, y es por donde entró WannaCry. Windows lo "
                   "deja activo cuando se ha actualizado desde una versión vieja o cuando algo "
                   "lo pidió: una impresora de red antigua o un NAS de hace años.",
            gain=0.0, gain_note="no es una optimización: es la puerta que usó WannaCry",
            effort="bajo", risk="bajo",
            steps=["Características de Windows → desmarca «Compatibilidad con el protocolo "
                   "para compartir archivos SMB 1.0/CIFS»",
                   "Antes de reiniciar, comprueba si algún NAS o impresora de red vieja "
                   "dependía de él: si deja de verse, casi siempre se arregla actualizando "
                   "su firmware",
                   "Nada de lo que se comparte hoy entre equipos Windows lo necesita"])
        return "ACTIVO"


    def check_firewall(self) -> str:
        """El cortafuegos de Windows, perfil por perfil.

        No basta con «está encendido»: Windows tiene tres perfiles y aplica el
        de la red a la que estés conectado. Un portátil con el perfil Público
        desactivado va sin cortafuegos en la wifi del aeropuerto y con él en
        casa, y desde el panel de control eso se ve como dos de tres en verde.

        Público y Privado pesan más que Dominio a propósito. Dominio solo
        aplica en una red corporativa con controlador, donde casi siempre hay
        una política central que lo gestiona y un cortafuegos perimetral
        delante; los otros dos son la red de casa y la del bar.
        """
        rows = ps_json("Get-NetFirewallProfile -ErrorAction SilentlyContinue | "
                       "Select-Object Name,Enabled")
        if not getattr(rows, "ok", True):
            raise SinDato(f"no se ha podido consultar el cortafuegos ({rows.error})")
        if not rows:
            raise SinDato("Windows no ha informado de ningún perfil de cortafuegos")

        apagados = []
        for fila in rows:
            activo = _FIREWALL_ACTIVO.get(_clave(fila.get("Enabled")))
            if activo is None:
                # Un valor que no se reconoce no es un perfil apagado: callarse
                # es lo único honesto, igual que en `_estado_cifrado`.
                raise SinDato(f"estado de cortafuegos no reconocido en el perfil "
                              f"«{fila.get('Name')}»: «{fila.get('Enabled')}»")
            if not activo:
                apagados.append(str(fila.get("Name") or "?"))
        if not apagados:
            return f"activo en los {len(rows)} perfiles"

        expuestos = [p for p in apagados if p.strip().lower() in _PERFILES_EXPUESTOS]
        cuales = ", ".join(apagados)
        self.add(
            id="firewall_off",
            title=f"El cortafuegos está desactivado ({cuales})",
            severity="high" if expuestos else "medium",
            category=SEGURIDAD, component="system",
            detail="El cortafuegos de Windows decide qué puede conectarse a este equipo desde "
                   "la red. Windows aplica un perfil distinto según dónde estés, así que tenerlo "
                   "apagado en uno solo ya deja el equipo descubierto en esas redes."
                   + (" Los perfiles Público y Privado son la wifi de un aeropuerto y la red de "
                      "casa: ahí no hay nada más entre este equipo e internet."
                      if expuestos else
                      " Solo está apagado el perfil de Dominio, que se usa en redes de empresa "
                      "donde suele haber una política central y un cortafuegos perimetral "
                      "delante. Conviene mirarlo, pero no es lo mismo."),
            gain=0.0,
            gain_note="no es una optimización: es lo que filtra lo que entra por la red",
            effort="bajo", risk="bajo",
            steps=["Seguridad de Windows → Firewall y protección de red",
                   "Activa el cortafuegos en los perfiles que aparezcan apagados",
                   "Si lo apagó un programa —algunas VPN y antivirus lo hacen— revisa que "
                   "ese programa siga instalado y siga filtrando por su cuenta",
                   "Nunca lo dejes apagado «para que funcione algo»: abre la regla concreta"])
        return f"DESACTIVADO en {cuales}"

    def check_windows_soportado(self) -> str:
        """Si esta versión de Windows sigue recibiendo parches de seguridad.

        Es la comprobación que hace inútiles a casi todas las demás: en un
        Windows fuera de soporte, los fallos que se descubran a partir de la
        fecha de fin no se arreglan nunca, y no hay ajuste que lo compense.

        La tabla se escribe a mano y por eso caduca sola. Si lleva más de
        `_SOPORTE_CADUCA_MESES` sin revisarse, esta comprobación se declara sin
        dato en vez de opinar: una tabla vieja diría que una versión está en
        soporte cuando ya no lo está, que es el error que no se puede cometer
        aquí. Es el mismo criterio que aplica la escala de referencia del
        benchmark con `reference_is_stale`.
        """
        build = _build_de(self.si.os_build)
        if build is None:
            raise SinDato(f"no se ha podido leer el número de build "
                          f"(«{self.si.os_build}»)")
        if _tabla_de_soporte_caducada():
            raise SinDato(
                f"la tabla de fin de soporte se revisó en {_SOPORTE_REVISADO} y ya "
                f"no es de fiar: con ella no se puede afirmar si esta versión "
                f"sigue recibiendo parches")

        largo = any(e in (self.si.os_name or "").lower() for e in _EDICIONES_LARGAS)
        fechas = _SOPORTE_WINDOWS.get(build)
        if fechas is None:
            if build < _SOPORTE_SUELO:
                # Por debajo de la tabla no hace falta tabla: son versiones que
                # dejaron de recibir parches hace años.
                fin, etiqueta = None, "una versión anterior a Windows 10 22H2"
            else:
                # Una build más nueva que la tabla: no se sabe, y no saber no es
                # lo mismo que estar bien.
                raise SinDato(f"la build {build} no está en la tabla de fin de "
                              f"soporte (revisada en {_SOPORTE_REVISADO})")
        else:
            fin = date.fromisoformat(fechas[1] if largo else fechas[0])
            etiqueta = f"build {build}"
            if fin >= date.today():
                return f"con soporte hasta {fin.isoformat()}"

        self.add(
            id="windows_sin_soporte",
            title=f"Este Windows ya no recibe actualizaciones de seguridad "
                  f"({etiqueta})",
            severity="high", category=SEGURIDAD, component="system",
            detail="Microsoft dejó de publicar parches para esta versión"
                   + (f" el {fin.isoformat()}" if fin else "")
                   + ". Los fallos que se descubran a partir de ahí no se arreglan nunca en "
                     "este equipo: no es que falte una actualización concreta, es que ya no "
                     "va a haber ninguna. Ningún ajuste de los que propone este informe "
                     "compensa eso, y por eso aparece aquí y no entre las mejoras."
                   + (" Las ediciones Enterprise y Education reciben unos dos años más por "
                      "la misma versión, y esa es la fecha que se ha usado aquí."
                      if largo else ""),
            gain=0.0,
            gain_note="no es una optimización: son parches que ya no van a llegar",
            effort="alto", risk="medio",
            steps=["Comprueba si el equipo admite la versión actual: Configuración → "
                   "Windows Update → Buscar actualizaciones",
                   "Si el equipo no cumple los requisitos de Windows 11, valora Linux antes "
                   "que seguir con un sistema sin parches",
                   "Mientras tanto, no uses este equipo para banca ni correo si puedes "
                   "evitarlo, y ten copia de seguridad al día",
                   "Actualizar el sistema operativo es la única solución real: un antivirus "
                   "no tapa un fallo del propio Windows"])
        return "SIN SOPORTE"

    def check_escritorio_remoto(self) -> str:
        """Si el equipo acepta conexiones de Escritorio remoto, y con qué puerta.

        Se lee del registro y no con PowerShell: son dos valores, no cuesta un
        proceso nuevo y no hace falta ningún privilegio.

        Las dos preguntas no son la misma. Que RDP esté activo no es un fallo
        —hay quien lo usa a diario— pero sí es una superficie expuesta que
        mucha gente tiene encendida sin saberlo, porque la activó un programa
        de asistencia remota y nadie la volvió a apagar. Lo que sí es un fallo
        es tenerlo activo **sin NLA**: sin autenticación a nivel de red, el
        equipo levanta una sesión y pinta la pantalla de acceso antes de saber
        quién llama, que es justo lo que explotó BlueKeep.
        """
        denegado = reg_read(winreg.HKEY_LOCAL_MACHINE, _RDP_CLAVE, "fDenyTSConnections")
        if denegado is None:
            # El valor existe en cualquier Windows de escritorio. Que no esté
            # significa que no se ha podido leer la rama, no que RDP esté
            # apagado: dar por buena su ausencia sería el error de siempre.
            raise SinDato("no se ha podido leer la configuración de Escritorio remoto")
        if denegado != 0:
            return "desactivado"

        nla = reg_read(winreg.HKEY_LOCAL_MACHINE, _RDP_TCP_CLAVE, "UserAuthentication")
        if nla == 1:
            self.add(
                id="rdp_activo",
                title="Escritorio remoto activo (con autenticación previa)",
                severity="low", category=SEGURIDAD, component="system",
                detail="Este equipo acepta conexiones de Escritorio remoto. No está mal "
                       "configurado —exige autenticación antes de abrir sesión— pero es una "
                       "puerta abierta, y mucha gente la tiene encendida sin saberlo porque la "
                       "activó un programa de asistencia remota que nadie volvió a apagar. Si no "
                       "lo usas, ciérralo; si lo usas, que no sea directamente desde internet.",
                gain=0.0,
                gain_note="no es una optimización: es una puerta de entrada al equipo",
                effort="bajo", risk="bajo",
                steps=["Si no lo usas: Configuración → Sistema → Escritorio remoto → desactivar",
                       "Si lo usas, no abras el puerto 3389 en el router: entra por VPN",
                       "Revisa quién está en el grupo «Usuarios de escritorio remoto»"])
            return "activo (con NLA)"

        self.add(
            id="rdp_sin_nla",
            title="Escritorio remoto activo y sin autenticación a nivel de red",
            severity="high", category=SEGURIDAD, component="system",
            detail="El equipo acepta conexiones de Escritorio remoto y no exige NLA "
                   "(autenticación a nivel de red). Sin NLA, Windows levanta una sesión y pinta "
                   "la pantalla de acceso ANTES de saber quién está llamando: cualquiera que "
                   "alcance el puerto consume recursos del equipo sin haberse identificado, y es "
                   "la condición que hizo explotable BlueKeep. Con NLA, quien llama se "
                   "autentica primero y solo entonces se crea la sesión."
                   + ("" if nla is not None else
                      " El valor de NLA no está en el registro, lo que en la práctica equivale a "
                      "no exigirlo."),
            gain=0.0,
            gain_note="no es una optimización: es autenticar antes de abrir la puerta",
            effort="bajo", risk="bajo",
            steps=["Configuración → Sistema → Escritorio remoto → activa «Requerir que los "
                   "equipos usen la autenticación a nivel de red»",
                   "Si algún cliente antiguo deja de conectar, actualízalo antes que quitar NLA",
                   "Si no usas Escritorio remoto, desactívalo entero y te ahorras las dos cosas"])
        return "ACTIVO sin NLA"

    def check_cuenta_administrador(self) -> str:
        """La cuenta Administrador de fábrica, la del RID -500.

        Se reconoce por el final del SID y no por el nombre: «Administrador»,
        «Administrator» y el nombre que le haya puesto quien la renombrara son
        la misma cuenta, y buscarla por texto falla en cuanto el Windows no
        está en inglés — el error que `check_filesystem_health` documenta.

        Windows la deja deshabilitada de fábrica desde Vista. Encontrarla
        habilitada significa que alguien la encendió, casi siempre un manual de
        internet para «arreglar» algo, y lo que queda es una cuenta de
        administrador con nombre conocido y sin las protecciones de UAC que sí
        tiene la cuenta de administrador normal.
        """
        rows = self._cuentas_locales()
        con_sid = [r for r in rows if str(r.get("SID") or "").strip()]
        if not con_sid:
            # Sin SID no se puede identificar, y por nombre no se busca: es
            # mejor decir que no se ha mirado que mirar mal.
            raise SinDato("Windows no ha devuelto el SID de ninguna cuenta local")
        de_fabrica = [r for r in con_sid
                      if str(r["SID"]).strip().endswith("-500")]
        if not de_fabrica:
            # Distinto del caso de arriba: los SID sí han llegado, y entre ellos
            # no está la integrada. El riesgo que se busca no existe aquí.
            return "no aparece la cuenta integrada"
        cuenta = de_fabrica[0]
        if cuenta.get("Enabled") is not True:
            return "deshabilitada, como viene de fábrica"
        self.add(
            id="admin_integrado_activo",
            title=f"La cuenta Administrador de fábrica está habilitada "
                  f"({cuenta.get('Name')})",
            severity="medium", category=SEGURIDAD, component="system",
            detail="Windows trae una cuenta de administrador integrada y la deja deshabilitada "
                   "desde Vista, por dos motivos: su identificador es el mismo en todos los "
                   "equipos del mundo —así que quien ataque no tiene que adivinar el nombre— y "
                   "no pasa por el aviso de UAC, de modo que todo lo que se ejecuta desde ella "
                   "va con permisos totales y sin preguntar. Que esté habilitada casi siempre "
                   "viene de un manual de internet para arreglar otra cosa.",
            gain=0.0,
            gain_note="no es una optimización: es una cuenta de administrador sin UAC",
            effort="bajo", risk="medio",
            steps=["Comprueba antes que tu cuenta habitual es administradora, o te quedas fuera",
                   "Deshabilítala: Administración de equipos → Usuarios y grupos locales → "
                   "Administrador → propiedades → «La cuenta está deshabilitada»",
                   "O desde consola de administrador: `net user Administrador /active:no`",
                   "Si la usas a diario, crea una cuenta propia de administrador y usa esa"])
        return "HABILITADA"

    def _cuentas_locales(self):
        """Las cuentas locales, con su SID. Una sola consulta para las dos
        comprobaciones que las necesitan.

        `Get-LocalUser` ya se lanzaba para las cuentas sin contraseña; añadirle
        el SID no cuesta ni un proceso más. Se cachea en la instancia porque
        `run()` llama a las dos comprobaciones seguidas.
        """
        if self._cuentas is None:
            self._cuentas = ps_json(
                "$( if (-not (Get-Command Get-LocalUser -ErrorAction SilentlyContinue)) {"
                "     [PSCustomObject]@{ disponible = $false } } else {"
                "     Get-LocalUser | Select-Object @{n='disponible';e={$true}},"
                "       Name,Enabled,PasswordRequired,@{n='SID';e={$_.SID.Value}} } )")
        rows = self._cuentas
        if not rows.ok:
            raise SinDato(f"no se han podido enumerar las cuentas locales ({rows.error})")
        if not rows:
            raise SinDato("no se ha devuelto ninguna cuenta local")
        if not rows[0].get("disponible"):
            raise NoAplica("este Windows no trae la consulta de cuentas locales")
        return rows

    def check_powershell_v2(self) -> str:
        """El motor de PowerShell 2.0, que sigue instalable y no debería estar.

        Es la versión de 2009, y lo que importa de ella no es que sea vieja:
        es que **no tiene ninguno de los registros de seguridad** que trae
        PowerShell 5 —ni transcripción, ni registro de bloques de script, ni
        AMSI—. Con el motor v2 presente, basta con `powershell -Version 2` para
        ejecutar exactamente lo mismo sin dejar rastro en ningún log. Es de los
        primeros movimientos de cualquier manual de intrusión y no lo usa nadie
        más: no queda software que necesite v2 y no funcione con 5.

        Sale del mismo lote con permisos que SMB1 y con el mismo cmdlet, así
        que no cuesta ni un proceso ni un aviso de UAC más.
        """
        rows = elevacion.recoger()["powershell2"]
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar PowerShell 2.0 ({rows.error})")
        if not rows:
            raise SinDato("Windows no ha informado del estado de PowerShell 2.0")
        if not rows[0].get("disponible"):
            raise NoAplica("este Windows no permite consultar las características opcionales")
        estado = rows[0].get("State")
        clave = _clave(estado)
        if clave in _SMB1_INACTIVO:
            return "desinstalado"
        if clave not in _SMB1_ACTIVO:
            raise SinDato(f"estado de PowerShell 2.0 no reconocido: «{estado}»")
        self.add(
            id="powershell_v2",
            title="El motor de PowerShell 2.0 sigue instalado",
            severity="medium", category=SEGURIDAD, component="system",
            detail="PowerShell 2.0 es la versión de 2009 y sigue disponible en muchos equipos "
                   "por compatibilidad. El problema no es su edad: es que no tiene ninguna de "
                   "las protecciones que sí trae PowerShell 5 —no registra los bloques de "
                   "script que ejecuta, no deja transcripción y no pasa por el antivirus vía "
                   "AMSI—. Mientras esté, cualquier programa puede pedir `powershell "
                   "-Version 2` y hacer lo mismo sin dejar rastro. Hoy no queda software que "
                   "lo necesite: todo lo que corre en v2 corre en 5.",
            gain=0.0,
            gain_note="no es una optimización: es un intérprete que no deja registro",
            effort="bajo", risk="bajo",
            steps=["Características de Windows → desmarca «Compatibilidad con PowerShell 2.0»",
                   "O desde una consola de administrador: `Disable-WindowsOptionalFeature "
                   "-Online -FeatureName MicrosoftWindowsPowerShellV2Root`",
                   "No hace falta reiniciar y PowerShell 5 sigue funcionando igual",
                   "Si algún programa muy antiguo protesta, se puede volver a activar desde "
                   "el mismo sitio"])
        return "INSTALADO"

    def check_local_accounts(self) -> str:
        rows = self._cuentas_locales()

        # Solo las cuentas habilitadas. `Invitado`, `DefaultAccount` y
        # `WDAGUtilityAccount` vienen de fábrica sin exigir contraseña y
        # deshabilitadas: contarlas convertiría un Windows recién instalado en
        # tres hallazgos graves.
        abiertas = [str(r.get("Name")) for r in rows
                    if r.get("Enabled") is True and r.get("PasswordRequired") is False]
        if not abiertas:
            habilitadas = sum(1 for r in rows if r.get("Enabled") is True)
            return f"{habilitadas} cuenta(s) activa(s), todas con contraseña"
        self.add(
            id="cuenta_sin_clave",
            title=f"Cuenta local que no exige contraseña ({', '.join(abiertas)})",
            severity="high", category=SEGURIDAD, component="system",
            detail="Windows informa de que "
                   f"{'estas cuentas están habilitadas y no exigen' if len(abiertas) > 1 else 'esta cuenta está habilitada y no exige'} "
                   "contraseña. Cualquiera con acceso físico al equipo entra sin más, y en una "
                   "red local también sirve para conectarse a los recursos compartidos. No "
                   "afecta al inicio de sesión con cuenta de Microsoft o PIN, que sí llevan "
                   "credencial detrás.",
            gain=0.0, gain_note="no es una optimización: es quién puede entrar en tu equipo",
            effort="bajo", risk="bajo",
            steps=["Ponle contraseña: `net user NOMBRE *` en una consola de administrador",
                   "O deshabilita la cuenta si no se usa: Administración de equipos → "
                   "Usuarios y grupos locales",
                   "Las cuentas de fábrica deshabilitadas (Invitado, DefaultAccount) no "
                   "cuentan aquí y no hay que tocarlas"])
        return f"{len(abiertas)} sin contraseña"


    # Severidades que Microsoft asigna a sus boletines. Solo las llevan las
    # actualizaciones de seguridad: una de zona horaria viene sin ella, y
    # contarla como riesgo sería inflar el hallazgo con lo que no toca.

    def check_security_updates(self) -> str:
        rows = pending_security_updates()
        if not rows.ok:
            raise SinDato(f"no se ha podido consultar Windows Update ({rows.error})")
        seguridad = []
        for fila in rows:
            nivel = str(fila.get("MsrcSeverity") or "").strip().lower()
            if nivel:
                seguridad.append((nivel, str(fila.get("Title") or "sin título")))
        if not seguridad:
            return f"{len(rows)} pendiente(s), ninguna de seguridad"

        criticas = [t for n, t in seguridad if n in _MSRC_GRAVES]
        importantes = [t for n, t in seguridad if n in _MSRC_SERIAS]
        if criticas:
            severidad, cuantas = "high", len(criticas)
            resumen = f"{cuantas} crítica(s)"
        elif importantes:
            severidad, cuantas = "medium", len(importantes)
            resumen = f"{cuantas} importante(s)"
        else:
            severidad, cuantas = "low", len(seguridad)
            resumen = f"{cuantas} de severidad menor"
        primeras = "; ".join(t[:70] for _, t in seguridad[:3])
        self.add(
            id="updates_pendientes",
            title=f"{len(seguridad)} actualización(es) de seguridad sin instalar ({resumen})",
            severity=severidad, category=SEGURIDAD, component="system",
            detail=f"Windows Update tiene pendientes {len(seguridad)} actualizaciones que "
                   f"Microsoft clasifica como de seguridad, {resumen}. Un parche publicado es "
                   "también un aviso público de dónde está el fallo, así que el riesgo real de "
                   "no instalarlo sube con el tiempo, no baja. Las primeras de la lista: "
                   f"{primeras}.",
            gain=0.0, gain_note="no es una optimización: son fallos ya conocidos y publicados",
            effort="bajo", risk="bajo",
            steps=["Configuración → Windows Update → Buscar actualizaciones",
                   "Reinicia cuando lo pida: muchas no terminan de aplicarse hasta entonces",
                   "Si llevan meses sin instalarse, mira que el servicio Windows Update no "
                   "esté deshabilitado"])
        return f"{len(seguridad)} de seguridad ({resumen})"


    def _check_proteccion_activa(self, rows) -> None:
        """Si queda alguien vigilando, y con las firmas al día.

        Se mira el conjunto y no producto a producto por una razón concreta:
        cuando se instala un antivirus de terceros, **Defender se desactiva
        solo**, y esa es la configuración correcta. Avisar de cada producto
        apagado convertiría el caso más normal de todos en un hallazgo crítico
        falso. Lo que sí es un problema es que no quede ninguno activo.

        Igual con las firmas: unas firmas caducadas en un motor que está apagado
        a propósito no le importan a nadie.
        """
        estados = []
        for fila in rows:
            nombre = str(fila.get("displayName") or "").strip()
            estado = _estado_antivirus(fila.get("productState"))
            if nombre and estado is not None:
                estados.append((nombre,) + estado)
        if not estados:
            # `productState` no viene, o no encaja en el reparto conocido. No se
            # interpreta: acusar a alguien de tener el antivirus apagado por
            # haber leído mal un entero es peor que no decir nada.
            return

        activos = [(n, al_dia) for n, activo, al_dia in estados if activo]
        if not activos:
            self.add(
                id="av_tiempo_real_off",
                title="Ningún antivirus con la protección en tiempo real activa",
                severity="critical", category=SEGURIDAD, component="system",
                detail="El Centro de seguridad de Windows tiene registrados "
                       f"{', '.join(n for n, _, _ in estados)}, pero ninguno está vigilando "
                       "ahora mismo. Sin protección en tiempo real, un archivo malicioso solo "
                       "se detecta si alguien lanza un análisis a mano, es decir, después. "
                       "Suele pasar tras desinstalar un antivirus de pago sin reactivar "
                       "Defender, o porque algo lo apagó.",
                gain=0.0, gain_note="no es una optimización: es exposición a malware",
                effort="bajo", risk="nulo",
                steps=["Abre Seguridad de Windows → Protección antivirus y contra amenazas",
                       "Activa «Protección en tiempo real»",
                       "Si sigue apagándose sola, desinstala los restos del antivirus "
                       "anterior con la herramienta de limpieza de su fabricante"])
            return

        caducados = [n for n, al_dia in activos if not al_dia]
        if caducados:
            self.add(
                id="av_desactualizado",
                title=f"Firmas de antivirus caducadas ({', '.join(caducados)})",
                severity="high", category=SEGURIDAD, component="system",
                detail="El motor está activo pero sus definiciones no están al día, así que "
                       "no reconoce nada de lo aparecido desde la última actualización. Es la "
                       "situación más engañosa de todas: el icono dice que estás protegido y "
                       "técnicamente lo estás, pero contra las amenazas del mes pasado.",
                gain=0.0, gain_note="no es una optimización: es exposición a malware",
                effort="bajo", risk="nulo",
                steps=["Abre Seguridad de Windows → Protección antivirus y contra amenazas "
                       "→ Buscar actualizaciones",
                       "Si falla, comprueba que Windows Update no esté pausado",
                       "Sin conexión desde hace tiempo, basta con conectarlo a internet"])
