"""El resumen que sale del equipo: qué lleva, qué no puede llevar y cuándo sale.

Este fichero cubre el módulo que rompe la promesa que Quilate mantenía desde su
primera versión, así que se prueba con más desconfianza de la habitual. Hay tres
grupos y el tercero es el que importa:

- Que el identificador se genere, se conserve y rote a los 90 días.
- Que el envío no pueda estropear una ejecución: que falle callando, que no
  reintente en 24 h y que el aviso vaya antes del primer envío.
- Que la lista negra no se cuele en el payload. Ese es el que no puede fallar
  nunca, y por eso no se comprueba campo a campo sino al revés: se le mete al
  constructor un informe lleno de datos personales y se registra que ninguno
  aparece en el JSON resultante, ni como valor ni como fragmento.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from quilate import telemetria
from quilate.const import TELEMETRIA_ESQUEMA
from quilate.telemetria import (ROTACION, _storage_kind, construir, enviar,
                                esperar, install_id, marcar_avisado, programar,
                                ya_avisado)

AHORA = datetime(2026, 8, 17, 12, 0, 0)


def ejecucion(**extra) -> dict:
    """Un informe como el que produce `export.build_payload`, con lo justo.

    Lleva a propósito los campos que el histórico ya sabe resumir y, en
    `system`, los que nunca pueden salir: el nombre del equipo, las rutas de los
    volúmenes y los números de serie de los discos están ahí porque
    `collect_system_info` los recoge de verdad.
    """
    payload = {
        "meta": {"generated_at": "2026-08-17T10:00:00", "version": "2.8.0",
                 "quick": False},
        "system": {
            "hostname": "PC-DE-CRISTIAN",
            "os_name": "Windows 11", "os_build": "26100",
            "cpu_name": "Intel Core i5-12400",
            "ram_total": 32 * 1024**3, "ram_speed_mhz": 3200, "ram_channels": 2,
            "gpus": [{"name": "NVIDIA RTX 3060", "active": True}],
            "system_drive": "C:\\", "system_drive_media": "SSD",
            "system_disk_number": 0,
            "physical_disks": [{"number": 0, "name": "Samsung 980 PRO",
                                "bus": "NVMe", "serial": "S5GXNX0T123456"}],
            "disks": [{"device": "C:\\", "mount": "C:\\"}],
        },
        "scores": {"overall": 72.4,
                   "components": {"cpu_single": 78.2, "disk": 61.0}},
        "findings": [{"id": "power_plan", "title": "Plan de energía «Equilibrado» en C:\\Users\\cristian"},
                     {"id": "ram_slow", "title": "RAM a 2133 MT/s"}],
        "metrics": {"cpu_temp_load": {"value": 88.0}},
        "boot": {"seconds": 31.4},
        "dispersion": {"disk_read": {"spread_pct": 4.1}},
        "ambient_load": {"antes": {"cpu_pct": 12.0}},
    }
    payload.update(extra)
    return payload


class _ConEstado(unittest.TestCase):
    """Cada prueba con su propio fichero de estado, en un temporal."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.estado = Path(self._tmp.name) / "instalacion.json"

    def escribir(self, **campos) -> None:
        self.estado.write_text(json.dumps(campos), encoding="utf-8")

    def leer(self) -> dict:
        return json.loads(self.estado.read_text(encoding="utf-8"))


# --- Identificador de instalación -------------------------------------------

class IdentificadorDeInstalacion(_ConEstado):
    def test_lo_genera_en_el_primer_arranque(self):
        ident = install_id(self.estado, AHORA)
        self.assertRegex(ident, r"^[0-9a-f-]{36}$")
        self.assertEqual(self.leer()["install_id"], ident)
        self.assertEqual(self.leer()["created_at"], AHORA.isoformat(timespec="seconds"))

    def test_lo_conserva_entre_ejecuciones(self):
        primero = install_id(self.estado, AHORA)
        segundo = install_id(self.estado, AHORA + timedelta(days=89))
        self.assertEqual(primero, segundo)

    def test_rota_a_los_noventa_dias(self):
        primero = install_id(self.estado, AHORA)
        segundo = install_id(self.estado, AHORA + ROTACION + timedelta(seconds=1))
        self.assertNotEqual(primero, segundo)
        # Y la fecha se renueva con él: si no, el siguiente arranque rotaría otra
        # vez y el identificador duraría una ejecución en vez de noventa días.
        self.assertNotEqual(self.leer()["created_at"], AHORA.isoformat(timespec="seconds"))

    def test_no_deriva_de_nada_del_equipo(self):
        # Dos instalaciones distintas del mismo equipo, a la misma hora, no
        # pueden coincidir: es lo que permite decir en PRIVACY.md que copiar el
        # .exe a otro ordenador no arrastra el identificador.
        otro = Path(self._tmp.name) / "otra.json"
        self.assertNotEqual(install_id(self.estado, AHORA), install_id(otro, AHORA))

    def test_una_fecha_ilegible_fuerza_la_rotacion(self):
        # Sin saber cuándo se creó no se puede saber si ha caducado, y dar por
        # bueno un identificador de antigüedad desconocida es justo lo que la
        # rotación existe para impedir.
        self.escribir(install_id="a" * 8 + "-aaaa-aaaa-aaaa-" + "a" * 12,
                      created_at="el martes")
        self.assertNotEqual(install_id(self.estado, AHORA), "a" * 8 + "-aaaa-aaaa-aaaa-" + "a" * 12)

    def test_una_fecha_en_el_futuro_tambien_rota(self):
        # Un reloj que se adelantó una vez congelaría la rotación durante años.
        anterior = install_id(self.estado, AHORA + timedelta(days=400))
        self.assertNotEqual(install_id(self.estado, AHORA), anterior)

    def test_no_manda_lo_que_alguien_escriba_a_mano(self):
        # PRIVACY.md invita al usuario a tocar este fichero. Lo que escriba ahí
        # no puede salir del equipo como texto libre.
        self.escribir(install_id="soy cristian, correo@ejemplo.com",
                      created_at=AHORA.isoformat(timespec="seconds"))
        self.assertRegex(install_id(self.estado, AHORA), r"^[0-9a-f-]{36}$")

    def test_un_fichero_corrupto_no_revienta(self):
        self.estado.write_text("{ esto no es json", encoding="utf-8")
        self.assertRegex(install_id(self.estado, AHORA), r"^[0-9a-f-]{36}$")

    def test_sin_poder_escribir_sigue_devolviendo_uno(self):
        # Un disco lleno o una carpeta sin permiso no pueden tumbar la ejecución.
        with mock.patch.object(Path, "write_text", side_effect=OSError):
            self.assertRegex(install_id(self.estado, AHORA), r"^[0-9a-f-]{36}$")


# --- La lista negra ----------------------------------------------------------

# Lo que nunca puede salir del equipo, con el valor exacto que `ejecucion()` mete
# en el informe. Se comprueba por fragmento y no por igualdad para que tampoco
# valga que se cuele dentro de una cadena más larga.
PROHIBIDO = (
    "PC-DE-CRISTIAN",            # nombre del equipo
    "cristian",                  # nombre de usuario, dentro de una ruta
    "C:\\",                      # rutas y letras de unidad
    "S5GXNX0T123456",            # número de serie del disco
    "Samsung 980 PRO",           # ejemplar concreto de disco, no categoría
    "Plan de energía",           # títulos de hallazgo: llevan texto interpolado
    "RAM a 2133",
)


class ListaNegra(unittest.TestCase):
    def payload_serializado(self, informe: dict) -> str:
        return json.dumps(construir(informe, "9f2c0000-0000-4000-8000-000000000000"),
                          ensure_ascii=False)

    def test_nada_de_la_lista_negra_llega_al_payload(self):
        crudo = self.payload_serializado(ejecucion())
        for prohibido in PROHIBIDO:
            with self.subTest(prohibido=prohibido):
                self.assertNotIn(prohibido, crudo)

    def test_no_se_cuela_un_campo_nuevo_del_informe(self):
        # La prueba de que esto es una lista blanca y no un filtro. El día que
        # alguien añada un campo al informe —y se añaden— no puede acabar aquí
        # por el hecho de existir.
        informe = ejecucion()
        informe["system"]["motherboard_uuid"] = "4C4C4544-0043-4D10-8043-B7C04F4E5432"
        informe["top_processes"] = [{"name": "juego.exe", "rss": 1}]
        informe["storage_scan"] = {"files": ["D:\\vídeos\\boda.mp4"]}
        crudo = self.payload_serializado(informe)
        self.assertNotIn("4C4C4544", crudo)
        self.assertNotIn("juego.exe", crudo)
        self.assertNotIn("boda.mp4", crudo)

    def test_solo_salen_las_claves_de_la_lista_cerrada(self):
        # El complemento de la prueba anterior: no basta con que no salgan los
        # datos malos conocidos, tiene que ser imposible que salga nada más.
        permitidas = {
            "schema", "app_version", "install_id", "os", "cpu_model", "gpu_model",
            "ram_gb", "ram_mts", "ram_channels", "storage_kind", "scores",
            "overall", "boot_seconds", "cpu_temp_peak", "max_spread_pct",
            "busy_pct", "finding_ids", "quick",
        }
        cuerpo = construir(ejecucion(), "9f2c0000-0000-4000-8000-000000000000")
        self.assertLessEqual(set(cuerpo), permitidas)

    def test_de_los_hallazgos_solo_el_identificador(self):
        cuerpo = construir(ejecucion(), "id")
        self.assertEqual(cuerpo["finding_ids"], ["power_plan", "ram_slow"])

    def test_descarta_los_identificadores_que_no_son_del_enum(self):
        # El enum lo garantiza `audit/modelo.py`, pero el informe puede venir de
        # otra versión o editado a mano. Un identificador con espacios o con una
        # ruta dentro es texto libre, y el texto libre no sale.
        informe = ejecucion(findings=[
            {"id": "power_plan"},
            {"id": "C:\\Users\\cristian\\algo"},
            {"id": "hallazgo con espacios"},
            {"id": 42},
            "esto no es ni un diccionario",
        ])
        self.assertEqual(construir(informe, "id")["finding_ids"], ["power_plan"])

    def test_el_disco_sale_como_categoria_no_como_modelo(self):
        self.assertEqual(construir(ejecucion(), "id")["storage_kind"], "nvme")

    def test_no_manda_la_marca_de_tiempo_de_la_ejecucion(self):
        # El histórico la necesita para ordenar la serie; aquí es más resolución
        # de la que ninguna pregunta necesita.
        self.assertNotIn("at", construir(ejecucion(), "id"))


# --- El payload --------------------------------------------------------------

class Payload(unittest.TestCase):
    def test_lleva_lo_que_promete_privacy(self):
        cuerpo = construir(ejecucion(), "9f2c0000-0000-4000-8000-000000000000")
        self.assertEqual(cuerpo["schema"], TELEMETRIA_ESQUEMA)
        self.assertEqual(cuerpo["app_version"], "2.8.0")
        self.assertEqual(cuerpo["install_id"], "9f2c0000-0000-4000-8000-000000000000")
        self.assertEqual(cuerpo["os"], "Windows 11 26100")
        self.assertEqual(cuerpo["cpu_model"], "Intel Core i5-12400")
        self.assertEqual(cuerpo["gpu_model"], "NVIDIA RTX 3060")
        self.assertEqual(cuerpo["ram_gb"], 32)
        self.assertEqual(cuerpo["ram_mts"], 3200)
        self.assertEqual(cuerpo["ram_channels"], 2)
        self.assertEqual(cuerpo["overall"], 72.4)
        self.assertEqual(cuerpo["scores"], {"cpu_single": 78.2, "disk": 61.0})
        self.assertEqual(cuerpo["boot_seconds"], 31.4)
        self.assertEqual(cuerpo["cpu_temp_peak"], 88.0)
        self.assertEqual(cuerpo["max_spread_pct"], 4.1)
        self.assertEqual(cuerpo["busy_pct"], 12.0)
        self.assertIs(cuerpo["quick"], False)

    def test_un_informe_vacio_no_revienta(self):
        cuerpo = construir({}, "id")
        self.assertEqual(cuerpo["finding_ids"], [])
        self.assertNotIn("cpu_model", cuerpo)

    def test_los_campos_sin_valor_no_se_mandan(self):
        informe = ejecucion()
        informe["system"]["ram_speed_mhz"] = None
        informe["system"]["gpus"] = []
        self.assertNotIn("ram_mts", construir(informe, "id"))
        self.assertNotIn("gpu_model", construir(informe, "id"))

    def test_prefiere_la_gpu_activa(self):
        informe = ejecucion()
        informe["system"]["gpus"] = [{"name": "Intel UHD 730", "active": False},
                                     {"name": "NVIDIA RTX 3060", "active": True}]
        self.assertEqual(construir(informe, "id")["gpu_model"], "NVIDIA RTX 3060")

    def test_es_serializable_a_json(self):
        json.dumps(construir(ejecucion(), "id"))


class TipoDeDisco(unittest.TestCase):
    def test_nvme(self):
        self.assertEqual(_storage_kind({
            "system_drive_media": "SSD", "system_disk_number": 0,
            "physical_disks": [{"number": 0, "bus": "NVMe"}]}), "nvme")

    def test_sata(self):
        self.assertEqual(_storage_kind({
            "system_drive_media": "SSD", "system_disk_number": 0,
            "physical_disks": [{"number": 0, "bus": "SATA"}]}), "sata_ssd")

    def test_mecanico(self):
        self.assertEqual(_storage_kind({"system_drive_media": "HDD"}), "hdd")

    def test_sin_saberlo_no_se_inventa(self):
        # «Desconocido» y «Mixto (...)» son las dos respuestas que da el
        # inventario cuando no puede resolver el disco del sistema. Ninguna vale
        # como categoría, y mandarlas ensuciaría los recuentos del servidor.
        self.assertIsNone(_storage_kind({"system_drive_media": "Desconocido"}))
        self.assertIsNone(_storage_kind({"system_drive_media": "Mixto (SSD, HDD)"}))
        self.assertIsNone(_storage_kind({}))


# --- El envío ----------------------------------------------------------------

class Envio(_ConEstado):
    def setUp(self):
        super().setUp()
        # Avisado ya: el caso por defecto de todo equipo salvo el primer arranque.
        self.escribir(install_id="9f2c0000-0000-4000-8000-000000000000",
                      created_at=AHORA.isoformat(timespec="seconds"),
                      notified_at=AHORA.isoformat(timespec="seconds"))

    def programar(self, ahora=AHORA):
        hilo = programar(ejecucion(), self.estado, "https://ejemplo.invalid/v1/run", ahora)
        if hilo:
            hilo.join(timeout=5)
        return hilo

    def test_por_defecto_envia(self):
        with mock.patch.object(telemetria, "enviar", return_value=True) as envio:
            self.assertIsNotNone(self.programar())
        envio.assert_called_once()
        cuerpo = envio.call_args.args[0]
        self.assertEqual(cuerpo["install_id"], "9f2c0000-0000-4000-8000-000000000000")

    def test_un_fallo_no_llega_a_la_consola(self):
        # La regla que gobierna el módulo entero: esto no puede estropear una
        # ejecución. Ni una traza, ni un aviso, ni un código de salida distinto.
        with mock.patch.object(telemetria, "enviar", side_effect=RuntimeError("boom")):
            self.programar()
        self.assertIn("failed_at", self.leer())

    def test_anota_el_fallo_y_no_reintenta_en_24h(self):
        with mock.patch.object(telemetria, "enviar", return_value=False) as envio:
            self.programar()
        self.assertEqual(envio.call_count, 1)
        self.assertEqual(self.leer()["failed_at"], AHORA.isoformat(timespec="seconds"))

        # Un equipo sin conexión no puede pagar tres segundos de timeout en cada
        # arranque para siempre.
        with mock.patch.object(telemetria, "enviar") as envio:
            self.assertIsNone(self.programar(AHORA + timedelta(hours=23)))
        envio.assert_not_called()

    def test_pasadas_24h_vuelve_a_intentarlo(self):
        with mock.patch.object(telemetria, "enviar", return_value=False):
            self.programar()
        with mock.patch.object(telemetria, "enviar", return_value=True) as envio:
            self.assertIsNotNone(self.programar(AHORA + timedelta(hours=25)))
        envio.assert_called_once()

    def test_un_envio_bueno_borra_el_fallo_anterior(self):
        with mock.patch.object(telemetria, "enviar", return_value=False):
            self.programar()
        with mock.patch.object(telemetria, "enviar", return_value=True):
            self.programar(AHORA + timedelta(hours=25))
        self.assertNotIn("failed_at", self.leer())

    def test_no_hay_cola_en_disco(self):
        # Un envío fallido se pierde. Guardarlo para luego convertiría este
        # fichero en un historial acumulado de todo lo que se ha querido enviar.
        with mock.patch.object(telemetria, "enviar", return_value=False):
            self.programar()
        guardado = json.dumps(self.leer())
        self.assertNotIn("Intel Core i5-12400", guardado)
        self.assertNotIn("72.4", guardado)
        self.assertLessEqual(set(self.leer()), {"install_id", "created_at",
                                                "notified_at", "failed_at"})

    def test_el_hilo_es_demonio(self):
        # Si el usuario cierra la ventana durante el timeout, el proceso tiene
        # que poder morir: su trabajo ya estaba hecho.
        with mock.patch.object(telemetria, "enviar", return_value=True):
            hilo = programar(ejecucion(), self.estado, "https://ejemplo.invalid/", AHORA)
        self.assertTrue(hilo.daemon)
        hilo.join(timeout=5)

    def test_enviar_traga_cualquier_fallo_de_red(self):
        for fallo in (OSError("timeout"), ValueError("url rara")):
            with self.subTest(fallo=fallo):
                with mock.patch("urllib.request.urlopen", side_effect=fallo):
                    self.assertIs(enviar({}, "https://ejemplo.invalid/"), False)

    def test_la_url_marcador_de_posicion_no_revienta(self):
        # Mientras el endpoint real no exista, `TELEMETRIA_URL` no resuelve. Eso
        # tiene que dar exactamente lo mismo que cualquier otro fallo de red.
        with mock.patch.object(telemetria, "enviar", wraps=telemetria.enviar) as envio:
            hilo = programar(ejecucion(), self.estado, ahora=AHORA)
            if hilo:
                hilo.join(timeout=10)
        envio.assert_called_once()


class SobrevivirAlCierre(_ConEstado):
    """Que el envío llegue a ocurrir, y no lo mate el cierre del proceso.

    El hilo es demonio, y el intérprete mata los hilos demonio sin esperarlos.
    Sin un `join` acotado el POST solo se completaba cuando algo mantenía vivo el
    proceso por casualidad —el menú final, que se para a leer una tecla— y se
    perdía en toda ejecución con la salida redirigida, en tarea programada, o
    simplemente con `--json`. Con él se perdía también el `failed_at`, así que
    ni siquiera el backoff de 24 h llegaba a anotarse.
    """

    def setUp(self):
        super().setUp()
        self.escribir(install_id="9f2c0000-0000-4000-8000-000000000000",
                      created_at=AHORA.isoformat(timespec="seconds"),
                      notified_at=AHORA.isoformat(timespec="seconds"))

    def test_esperar_deja_terminar_al_envio(self):
        empezado, terminado = threading.Event(), threading.Event()

        def lento(*_args, **_kwargs):
            empezado.set()
            time.sleep(0.3)          # el orden de magnitud de una petición real
            terminado.set()
            return True

        with mock.patch.object(telemetria, "enviar", side_effect=lento):
            hilo = programar(ejecucion(), self.estado, "https://ejemplo.invalid/", AHORA)
            self.assertTrue(empezado.wait(2))
            self.assertFalse(terminado.is_set())   # todavía a medias
            esperar(hilo)
            self.assertTrue(terminado.is_set())    # y `esperar` no volvió antes

    def test_esperar_anota_el_fallo_antes_de_cerrar(self):
        # La consecuencia que de verdad se notaba: sin esperar, el `failed_at` no
        # llegaba al disco y el equipo sin conexión reintentaba en cada arranque.
        with mock.patch.object(telemetria, "enviar", return_value=False):
            esperar(programar(ejecucion(), self.estado, "https://ejemplo.invalid/", AHORA))
        self.assertEqual(self.leer()["failed_at"], AHORA.isoformat(timespec="seconds"))

    def test_esperar_no_cuelga_si_el_envio_se_atasca(self):
        # El tope existe para que un servidor que acepta la conexión y luego
        # calla no deje el programa sin cerrarse.
        with mock.patch.object(telemetria, "enviar",
                               side_effect=lambda *a, **k: time.sleep(30)):
            hilo = programar(ejecucion(), self.estado, "https://ejemplo.invalid/", AHORA)
            t = time.perf_counter()
            esperar(hilo, timeout=0.2)
            self.assertLess(time.perf_counter() - t, 2.0)

    def test_esperar_sin_hilo_no_revienta(self):
        # Es lo que recibe cuando no tocaba enviar: primera ejecución, o backoff.
        esperar(None)

    def test_el_hilo_sigue_siendo_demonio(self):
        # El `join` acotado no puede convertirlo en un hilo que impida cerrar.
        with mock.patch.object(telemetria, "enviar", return_value=True):
            hilo = programar(ejecucion(), self.estado, "https://ejemplo.invalid/", AHORA)
        self.assertTrue(hilo.daemon)
        esperar(hilo)


class AvisoDePrimeraEjecucion(_ConEstado):
    def test_sin_aviso_no_se_envia_nada(self):
        # La regla de secuencia: se avisa antes, no a la vez. Un equipo que
        # todavía no ha visto el aviso no abre ninguna conexión.
        self.escribir(install_id="9f2c0000-0000-4000-8000-000000000000",
                      created_at=AHORA.isoformat(timespec="seconds"))
        with mock.patch.object(telemetria, "enviar") as envio:
            self.assertIsNone(programar(ejecucion(), self.estado, ahora=AHORA))
        envio.assert_not_called()

    def test_un_equipo_nuevo_tampoco_envia(self):
        with mock.patch.object(telemetria, "enviar") as envio:
            self.assertIsNone(programar(ejecucion(), self.estado, ahora=AHORA))
        envio.assert_not_called()
        self.assertFalse(ya_avisado(self.estado))

    def test_tras_avisar_la_siguiente_ejecucion_si_envia(self):
        marcar_avisado(self.estado, AHORA)
        self.assertTrue(ya_avisado(self.estado))
        with mock.patch.object(telemetria, "enviar", return_value=True) as envio:
            hilo = programar(ejecucion(), self.estado, "https://ejemplo.invalid/",
                             AHORA + timedelta(minutes=1))
            hilo.join(timeout=5)
        envio.assert_called_once()

    def test_el_aviso_no_se_repite(self):
        marcar_avisado(self.estado, AHORA)
        primero = self.leer()["notified_at"]
        self.assertTrue(ya_avisado(self.estado))
        self.assertEqual(self.leer()["notified_at"], primero)

    def test_marcar_avisado_no_pierde_el_identificador(self):
        ident = install_id(self.estado, AHORA)
        marcar_avisado(self.estado, AHORA)
        self.assertEqual(self.leer()["install_id"], ident)


if __name__ == "__main__":
    unittest.main()
