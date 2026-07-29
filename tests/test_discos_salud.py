"""Desgaste y errores de disco, más allá del binario sano/no sano.

`HealthStatus` sigue diciendo «Healthy» en un SSD al 95% de vida consumida, y
también en uno que ya ha perdido datos por errores no corregidos.
"""

import pytest

from quilate.sysinfo import SystemInfo


def disco(nombre="Disco", **kw):
    base = {"number": 0, "name": nombre, "media": "SSD", "bus": "NVMe",
            "size": 1024**4, "health": "Healthy", "rpm": None,
            "wear": None, "temperature": None, "power_on_hours": None,
            "read_errors": None, "write_errors": None}
    base.update(kw)
    return base


@pytest.fixture
def auditar_discos(auditor):
    """Audita el desgaste de los discos que se le den y devuelve `(auditor, resumen)`.

    Las cuatro líneas de montar un `SystemInfo`, colgarle los discos, construir
    el auditor y llamar a `_check_disk_wear` estaban repetidas en ocho tests, y
    el `resumen` que devuelve la comprobación se perdía en la mitad de ellos.
    """
    def auditar(*discos):
        si = SystemInfo()
        si.physical_disks = list(discos)
        a = auditor(si)
        return a, a._check_disk_wear()
    return auditar


@pytest.fixture
def auditar_uno(auditar_discos):
    """El caso corriente: un solo disco, descrito por sus campos."""
    def auditar(**kw):
        return auditar_discos(disco(**kw))
    return auditar


def hallazgos(a, id_):
    return [f for f in a.findings if f.id == id_]


def test_sin_contadores_la_ausencia_no_es_un_disco_nuevo(auditar_uno):
    # Sin privilegios los contadores no se leen. Callar es correcto; decir
    # que está sano, no.
    a, resumen = auditar_uno()
    assert "requiere administrador" in resumen
    assert a.findings == []


def test_disco_nuevo_no_genera_hallazgo(auditar_uno):
    a, _ = auditar_uno(wear=0, power_on_hours=120)
    assert a.findings == []


@pytest.mark.parametrize("desgaste, severidad", [
    (65, "medium"),
    (85, "high"),
    (95, "critical"),
])
def test_la_severidad_del_desgaste_va_por_tramos(auditar_uno, desgaste, severidad):
    a, _ = auditar_uno(wear=desgaste)
    hallazgo = next(f for f in a.findings if f.id == "disk_wear")
    assert hallazgo.severity == severidad
    assert str(desgaste) in hallazgo.title


def test_el_desgaste_no_se_vende_como_optimizacion(auditar_uno):
    # Cambiar un disco gastado no acelera nada: es vida restante.
    a, _ = auditar_uno(wear=95)
    hallazgo = next(f for f in a.findings if f.id == "disk_wear")
    assert hallazgo.gain == 0.0


def test_en_un_hdd_el_desgaste_no_significa_nada(auditar_uno):
    # Es un contador de ciclos de escritura de celdas: en un disco mecánico
    # no aplica, y aun así el sistema devuelve un valor.
    a, _ = auditar_uno(media="HDD", wear=95)
    assert hallazgos(a, "disk_wear") == []


def test_se_reporta_el_disco_mas_gastado_de_varios(auditar_discos):
    a, _ = auditar_discos(disco("Uno", number=0, wear=62),
                          disco("Dos", number=1, wear=91))
    hallazgo = next(f for f in a.findings if f.id == "disk_wear")
    assert "Dos" in hallazgo.title
    assert hallazgo.severity == "critical"


def test_los_errores_no_corregidos_son_criticos(auditar_uno):
    a, _ = auditar_uno(wear=10, read_errors=3, write_errors=1)
    hallazgo = next(f for f in a.findings if f.id == "disk_errors")
    assert hallazgo.severity == "critical"
    assert "4 errores" in hallazgo.title


def test_cero_errores_no_dispara_hallazgo(auditar_uno):
    a, _ = auditar_uno(wear=10, read_errors=0, write_errors=0)
    assert hallazgos(a, "disk_errors") == []


# Las tres particiones que decide `disk_hot`, y no tres discos calientes con
# cifras distintas: por encima del umbral en un bus que cuenta, por encima del
# umbral en uno que se ignora a propósito, y por debajo del umbral.
@pytest.mark.parametrize("campos, esperado", [
    ({"temperature": 72},               True),
    ({"temperature": 80, "bus": "USB"}, False),
    ({"temperature": 42},               False),
], ids=["nvme-caliente", "caja-usb-se-ignora", "temperatura-normal"])
def test_solo_se_avisa_de_disco_caliente_donde_la_cifra_es_fiable(
        auditar_uno, campos, esperado):
    # Las cajas USB suelen reportar temperaturas sin sentido.
    a, _ = auditar_uno(**campos)
    assert bool(hallazgos(a, "disk_hot")) is esperado


def test_un_disco_puede_acumular_varios_problemas(auditar_uno):
    a, resumen = auditar_uno(wear=93, temperature=71, read_errors=2)
    assert {f.id for f in a.findings} == {"disk_wear", "disk_hot", "disk_errors"}
    assert "desgaste máx 93%" in resumen
    assert "errores" in resumen
