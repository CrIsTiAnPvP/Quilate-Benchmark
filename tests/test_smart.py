"""El blob SMART crudo: los sectores que `Get-StorageReliabilityCounter` no da.

Ese contador cubre desgaste, horas, temperatura y errores no corregidos, que es
lo importante. Lo que no expone son los sectores reasignados y los pendientes,
que en un disco mecánico avisan meses antes que cualquier otra cosa y mientras
tanto Windows sigue diciendo «Healthy».

Salen de `MSStorageDriver_FailurePredictData`, que devuelve los 512 bytes tal y
como los da el disco y hay que decodificar a mano. El fixture son los blobs
reales de dos HDD SATA sanos, y las cifras contra las que se comprueban son las
que enseñaba CrystalDiskInfo sobre esos mismos discos: 7583 horas y 1396
encendidos el WD, 4732 y 1470 el Seagate. No es aritmética comprobándose a sí
misma, que es lo único que un decodificador escrito a mano no puede permitirse.

Lo que el fixture NO puede demostrar es el caso malo: los dos discos están sanos
y sus tres contadores están a cero. Que un número distinto de cero se convierta
en hallazgo se prueba con blobs construidos aquí.
"""

from __future__ import annotations

import unittest

from quilate.audit import SEGURIDAD, Auditor
from quilate.smart import (_ATRIBUTOS_SMART, _modelo_de_instancia,
                           _smart_atributos, _smart_del_disco, _smart_por_modelo)
from quilate.sysinfo import SystemInfo, _map_storage
from tests.support import load

REALES = load("smart_dos_hdd_sata")["discos"]
WD = next(d for d in REALES if "WD20EZRZ" in d["InstanceName"])
SEAGATE = next(d for d in REALES if "ST1000DM010" in d["InstanceName"])


def blob(**atributos) -> list[int]:
    """Un blob de 512 bytes con los atributos que se le pidan.

    Los huecos que no se usan quedan a cero, que es como llegan de verdad: los
    dos discos del fixture traen 17 y 23 atributos de los 30 posibles.
    """
    datos = [0] * 512
    datos[0] = 16                                  # revisión
    for hueco, (identificador, bruto) in enumerate(atributos_ordenados(atributos)):
        base = 2 + hueco * 12
        datos[base] = identificador
        datos[base + 3] = 100                      # valor normalizado
        datos[base + 4] = 100                      # peor valor
        datos[base + 5:base + 11] = list(bruto.to_bytes(6, "little"))
    return datos


def atributos_ordenados(atributos: dict) -> list[tuple[int, int]]:
    return [(int(clave.lstrip("a")), valor) for clave, valor in atributos.items()]


class DecodificarElBlobReal(unittest.TestCase):
    """Contrastado con CrystalDiskInfo sobre los mismos dos discos."""

    def test_horas_encendido_del_wd(self):
        self.assertEqual(_smart_atributos(WD["VendorSpecific"])[9], 7583)

    def test_horas_encendido_del_seagate(self):
        self.assertEqual(_smart_atributos(SEAGATE["VendorSpecific"])[9], 4732)

    def test_ciclos_de_encendido_de_los_dos(self):
        self.assertEqual(_smart_atributos(WD["VendorSpecific"])[12], 1396)
        self.assertEqual(_smart_atributos(SEAGATE["VendorSpecific"])[12], 1470)

    def test_tiempo_de_arranque_del_wd(self):
        # CrystalDiskInfo lo enseñaba en hexadecimal: 0x0FB0.
        self.assertEqual(_smart_atributos(WD["VendorSpecific"])[3], 0x0FB0)

    def test_ciclos_de_carga_del_wd(self):
        # CrystalDiskInfo enseñaba 0x041233, uno más. No es un error de
        # decodificación: es un contador que sube solo y las dos lecturas se
        # tomaron con minutos de diferencia. Los que se comparan arriba —horas,
        # encendidos— no se mueven en ese rato, y por eso valen como referencia.
        self.assertEqual(_smart_atributos(WD["VendorSpecific"])[193], 0x041232)

    def test_los_dos_discos_estan_sanos(self):
        # Cero en los tres, que es lo que decía CrystalDiskInfo. Si el
        # decodificador se desalineara, aquí saldrían números inventados.
        for disco in REALES:
            atributos = _smart_atributos(disco["VendorSpecific"])
            for identificador in _ATRIBUTOS_SMART:
                with self.subTest(disco=disco["InstanceName"][:24],
                                  atributo=identificador):
                    self.assertEqual(atributos.get(identificador, 0), 0)

    def test_no_se_lee_mas_alla_de_los_treinta_huecos(self):
        # Detrás de los 30 atributos el blob sigue con otras cosas, y el WD
        # tiene un 130 justo ahí. Pasarse de largo lo leería como el atributo
        # 130, que es un identificador SMART que existe.
        self.assertEqual(WD["VendorSpecific"][2 + 30 * 12], 130)
        self.assertNotIn(130, _smart_atributos(WD["VendorSpecific"]))

    def test_los_huecos_vacios_no_son_atributos(self):
        atributos = _smart_atributos(WD["VendorSpecific"])
        self.assertNotIn(0, atributos)
        self.assertEqual(len(atributos), 17)


class BlobsQueNoValen(unittest.TestCase):
    def test_un_blob_corto_no_se_interpreta(self):
        # Un disco que conteste a medias no puede acabar dando contadores
        # sacados de bytes que no están.
        self.assertEqual(_smart_atributos([0] * 100), {})

    def test_lo_que_no_es_una_lista_de_bytes(self):
        for crudo in (None, "512 bytes", 42, {}, [None] * 512, ["x"] * 512,
                      [999] * 512, [-1] * 512):
            with self.subTest(crudo=type(crudo).__name__):
                self.assertEqual(_smart_atributos(crudo), {})

    def test_un_blob_entero_a_cero(self):
        self.assertEqual(_smart_atributos([0] * 512), {})


class ElModeloQueLlevaDentro(unittest.TestCase):
    def test_los_dos_nombres_reales(self):
        self.assertEqual(_modelo_de_instancia(WD["InstanceName"]),
                         "WDC WD20EZRZ-00Z5HB0")
        # Este viene sin fabricante y con el modelo recortado a 16 caracteres.
        self.assertEqual(_modelo_de_instancia(SEAGATE["InstanceName"]),
                         "ST1000DM010-2EP1")

    def test_los_guiones_bajos_son_espacios(self):
        self.assertEqual(
            _modelo_de_instancia(r"SCSI\Disk&Ven_Samsung&Prod_SSD_860_EVO\5&x&0&0"),
            "SAMSUNG SSD 860 EVO")

    def test_no_se_queda_con_la_direccion_del_dispositivo(self):
        # Es la parte que en USB y NVMe puede llevar el número de serie. Se usa
        # para casar el modelo y se tira, igual que el histórico no guarda rutas.
        for disco in REALES:
            modelo = _modelo_de_instancia(disco["InstanceName"])
            with self.subTest(disco=modelo):
                self.assertNotIn("&", modelo)
                self.assertNotIn("\\", modelo)
                self.assertNotIn("5&30F44C4F", modelo.upper())

    def test_una_instancia_que_no_tiene_esa_forma(self):
        for nombre in ("", "cualquier cosa", "SCSI\\", None):
            with self.subTest(nombre=nombre):
                self.assertEqual(_modelo_de_instancia(nombre or ""), "")


class CasarCadaDiscoConElSuyo(unittest.TestCase):
    """No hay forma fiable de sacar el número de disco, así que se casa el modelo.

    Se repite mucho que el `_N` del final del `InstanceName` es el índice del
    disco físico. Es falso, y está comprobado en el equipo de referencia: sus
    dos discos SATA son el 0 y el 1, y los dos terminan en `_0`.
    """

    def test_el_sufijo_no_es_el_numero_de_disco(self):
        self.assertTrue(WD["InstanceName"].endswith("_0"))
        self.assertTrue(SEAGATE["InstanceName"].endswith("_0"))

    def test_el_nombre_recortado_encaja_igual(self):
        # `Get-PhysicalDisk` dice ST1000DM010-2EP102; WMI, ST1000DM010-2EP1.
        # Los seis atributos salen del mismo blob de 512 bytes: este disco de
        # verdad los publica todos y todos a cero, que es lo que debe salir de
        # un disco sano.
        smart = _smart_por_modelo(REALES)
        self.assertEqual(_smart_del_disco("ST1000DM010-2EP102", smart),
                         {"reallocated": 0, "pending": 0, "uncorrectable": 0,
                          "reported_uncorrectable": 0, "command_timeout": 0,
                          "crc_errors": 0})

    def test_el_nombre_con_fabricante_delante_tambien(self):
        smart = _smart_por_modelo(REALES)
        self.assertTrue(_smart_del_disco("WDC WD20EZRZ-00Z5HB0", smart))

    def test_un_disco_que_no_esta_en_el_blob(self):
        # El NVMe del equipo: no publica la clase, y no por eso hereda los
        # contadores de otro.
        smart = _smart_por_modelo(REALES)
        self.assertEqual(_smart_del_disco("KINGSTON SNV2S1000G", smart), {})

    def test_dos_discos_del_mismo_modelo_se_descartan(self):
        # Adjudicarle a uno los sectores del otro sería peor que callarse.
        gemelos = [{"InstanceName": r"SCSI\Disk&Ven_&Prod_ST1000DM010-2EP1\5&a&0&0_0",
                    "VendorSpecific": blob(a5=7)},
                   {"InstanceName": r"SCSI\Disk&Ven_&Prod_ST1000DM010-2EP1\5&a&0&1_0",
                    "VendorSpecific": blob(a5=0)}]
        self.assertEqual(_smart_por_modelo(gemelos), {})

    def test_un_disco_sin_blob_legible_no_entra(self):
        filas = [{"InstanceName": WD["InstanceName"], "VendorSpecific": [0] * 10}]
        self.assertEqual(_smart_por_modelo(filas), {})


class LlegarHastaElInventario(unittest.TestCase):
    def _inventario(self, smart):
        vacio = {"logical": [], "partitions": [], "reliability": [], "smart": smart,
                 "physical": [{"DeviceId": 0, "FriendlyName": "ST1000DM010-2EP102",
                               "MediaType": 3, "BusType": 11, "Size": 1000},
                              {"DeviceId": 1, "FriendlyName": "KINGSTON SNV2S1000G",
                               "MediaType": 4, "BusType": 17, "Size": 1000}]}
        si = SystemInfo()
        _map_storage(si, vacio)
        return {d["name"]: d for d in si.physical_disks}

    def test_los_contadores_llegan_al_disco_que_toca(self):
        discos = self._inventario(REALES)
        self.assertEqual(discos["ST1000DM010-2EP102"]["pending"], 0)
        self.assertNotIn("pending", discos["KINGSTON SNV2S1000G"])

    def test_sin_privilegios_no_hay_claves_de_sectores(self):
        # Sin administrador la consulta falla entera y llega vacía. Eso no puede
        # convertirse en «cero sectores defectuosos».
        discos = self._inventario([])
        for disco in discos.values():
            with self.subTest(disco=disco["name"]):
                self.assertNotIn("reallocated", disco)


class ErroresDeEnlace(unittest.TestCase):
    """199 y 188 acusan al cable, no al disco.

    Es la parte donde más fácil es equivocar la conclusión: los errores de
    transmisión y los comandos que expiran casi nunca significan que el disco
    esté estropeado. Lo normal es un cable SATA mal encajado o de mala calidad.
    Mandar a alguien a comprar un disco por esto sería hacerle tirar el dinero
    sin arreglarle el problema, y por eso van con identificador y categoría
    propios en vez de sumarse a los sectores.
    """

    def _auditar(self, **campos):
        si = SystemInfo()
        si.physical_disks = [dict({"name": "ST1000DM010", "media": "HDD",
                                   "bus": "SATA"}, **campos)]
        a = Auditor(si, None)
        a._check_disco_enlace()
        return a

    def test_un_disco_sin_errores_de_enlace_no_es_un_hallazgo(self):
        self.assertEqual(self._auditar(crc_errors=0, command_timeout=0).findings, [])

    def test_los_errores_crc_avisan(self):
        a = self._auditar(crc_errors=17)
        self.assertEqual([f.id for f in a.findings], ["disco_cable"])
        f = a.findings[0]
        self.assertEqual((f.severity, f.category, f.gain), ("medium", "almacenamiento", 0.0))
        self.assertIn("17 de transmisión", f.title)

    def test_los_comandos_expirados_tambien(self):
        a = self._auditar(command_timeout=4)
        self.assertEqual([f.id for f in a.findings], ["disco_cable"])
        self.assertIn("4 comandos expirados", a.findings[0].title)

    def test_no_es_seguridad_sino_almacenamiento(self):
        # El informe lo pide explícitamente: este no va al bloque de riesgos.
        a = self._auditar(crc_errors=1)
        self.assertNotEqual(a.findings[0].category, SEGURIDAD)

    def test_el_primer_paso_es_el_cable_y_no_comprar_un_disco(self):
        pasos = " ".join(self._auditar(crc_errors=5).findings[0].steps).lower()
        self.assertIn("cable", pasos)
        self.assertNotIn("sustituye el disco", pasos)

    def test_sin_los_contadores_no_se_inventa_nada(self):
        # Un NVMe no publica estos atributos: llega sin las claves y eso no
        # puede leerse como «cero errores» ni como un hallazgo.
        self.assertEqual(self._auditar().findings, [])


class ElHallazgoDeSectores(unittest.TestCase):
    def _auditar(self, **campos):
        si = SystemInfo()
        si.physical_disks = [dict({"name": "ST1000DM010", "media": "HDD",
                                   "bus": "SATA"}, **campos)]
        a = Auditor(si, None)
        a._check_disk_wear()
        return a

    def test_un_disco_limpio_no_es_un_hallazgo(self):
        a = self._auditar(reallocated=0, pending=0, uncorrectable=0)
        self.assertEqual(a.findings, [])

    def test_sectores_reasignados_avisan(self):
        a = self._auditar(reallocated=8, pending=0, uncorrectable=0)
        self.assertEqual([f.id for f in a.findings], ["disk_sectores"])
        self.assertEqual(a.findings[0].severity, "medium")
        self.assertIn("8 reasignados", a.findings[0].title)

    def test_los_no_corregidos_reportados_pesan_como_los_pendientes(self):
        # El 187 es, junto con 5, 197 y 198, el grupo que mejor predice un
        # fallo próximo. No es un error de enlace: son datos que el disco no
        # pudo corregir y tuvo que reportar.
        a = self._auditar(reported_uncorrectable=2)
        self.assertEqual([f.id for f in a.findings], ["disk_sectores"])
        self.assertEqual(a.findings[0].severity, "high")
        self.assertIn("2 no corregidos", a.findings[0].title)

    def test_los_pendientes_pesan_mas(self):
        # Un sector pendiente ya no se lee y todavía no se ha sustituido: si
        # ahí había un archivo, ese archivo ya no está entero.
        a = self._auditar(reallocated=0, pending=3, uncorrectable=0)
        self.assertEqual(a.findings[0].severity, "high")
        self.assertIn("3 pendientes", a.findings[0].title)

    def test_los_irrecuperables_tambien(self):
        a = self._auditar(reallocated=0, pending=0, uncorrectable=2)
        self.assertEqual(a.findings[0].severity, "high")

    def test_no_promete_velocidad(self):
        a = self._auditar(reallocated=8)
        self.assertEqual(a.findings[0].gain, 0.0)
        self.assertIn("no es una optimización", a.findings[0].gain_note)

    def test_dice_que_lo_que_importa_es_si_sube(self):
        # Una cuenta estable durante meses puede convivir; sustituir el disco
        # por ocho sectores que llevan tres años ahí sería tirar el dinero.
        a = self._auditar(reallocated=8)
        self.assertIn("es si sube", " ".join(a.findings[0].steps))

    def test_sin_el_dato_no_se_opina(self):
        # Un NVMe no publica estos contadores. Ausencia no es cero.
        a = self._auditar(wear=10)
        self.assertEqual([f.id for f in a.findings], [])

    def test_un_disco_solo_con_sectores_cuenta_como_medido(self):
        # Si el contador de fiabilidad no contestó pero el blob sí, el disco
        # está medido: antes se descartaba entero por no traer horas ni grados.
        a = self._auditar(reallocated=4)
        self.assertEqual([f.id for f in a.findings], ["disk_sectores"])


if __name__ == "__main__":
    unittest.main()
