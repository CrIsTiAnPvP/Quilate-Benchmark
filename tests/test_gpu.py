"""Elección de la gráfica principal y auditoría de su driver.

Windows lista también la iGPU del procesador aunque el monitor vaya por la
tarjeta dedicada, y no siempre la deja en primer lugar. Auditar el adaptador con
el driver más antiguo —lo que se hacía— acababa colgando el hallazgo de la
integrada, que lleva años sin actualizarse porque no se usa.
"""

import pytest

from quilate import audit
from quilate.sysinfo import SystemInfo, gpu_label, primary_gpu, _is_integrated


# --- Detección de integradas -------------------------------------------------
# El criterio es que las dedicadas nombran el modelo y las iGPU se quedan en el
# genérico del fabricante. Arc y RX Vega son los casos que más se parecen a un
# nombre de integrada, y por eso están aquí.

@pytest.mark.parametrize("nombre", [
    "AMD Radeon(TM) Graphics",
    "Intel(R) UHD Graphics 770",
    "Intel(R) Iris(R) Xe Graphics",
    "AMD Radeon(TM) Vega 8 Graphics",
    "Microsoft Basic Display Adapter",
])
def test_los_nombres_genericos_son_integradas(nombre):
    assert _is_integrated(nombre)


@pytest.mark.parametrize("nombre", [
    "NVIDIA GeForce RTX 5060 Ti",
    "AMD Radeon RX 7800 XT",
    "Intel(R) Arc(TM) A770 Graphics",
    "AMD Radeon RX Vega 64",
    "NVIDIA GeForce RTX 3060",
])
def test_las_dedicadas_llevan_modelo(nombre):
    assert not _is_integrated(nombre)


# --- Dos adaptadores ---------------------------------------------------------

@pytest.fixture
def dos_adaptadores(captura):
    """Caso reconstruido: RTX 5060 Ti al día + Radeon integrada de 412 días."""
    return captura("gpu_dos_adaptadores_reconstruido")


@pytest.mark.parametrize("invertido", [False, True],
                         ids=["orden-del-fixture", "orden-invertido"])
def test_elige_la_dedicada_en_cualquier_orden(dos_adaptadores, invertido):
    gpus = dos_adaptadores["gpus"]
    orden = list(reversed(gpus)) if invertido else gpus
    assert primary_gpu(orden)["name"] == dos_adaptadores["esperado"]["principal"]


def test_no_culpa_a_la_integrada_del_driver_viejo(dos_adaptadores, auditor):
    si = SystemInfo()
    si.gpus = dos_adaptadores["gpus"]
    a = auditor(si)
    resumen = a.check_gpu_drivers()
    assert resumen == dos_adaptadores["esperado"]["resumen"]
    assert [f for f in a.findings if f.id == "gpu_driver"] == []


def test_la_integrada_sale_etiquetada(dos_adaptadores):
    etiquetas = [gpu_label(g) for g in dos_adaptadores["gpus"]]
    assert etiquetas[0] == "NVIDIA GeForce RTX 5060 Ti"
    assert etiquetas[1] == "AMD Radeon(TM) Graphics (integrada, sin pantalla conectada)"


# --- Una sola dedicada -------------------------------------------------------

def test_captura_real_de_una_sola_dedicada(captura):
    fx = captura("gpu_una_dedicada")
    controladores = fx["video_controllers"]
    assert len(controladores) == 1
    gpu = {"name": controladores[0]["Name"],
           "integrated": _is_integrated(controladores[0]["Name"]),
           "active": bool(controladores[0]["CurrentHorizontalResolution"])}
    assert primary_gpu([gpu])["name"] == fx["esperado"]["principal"]
    assert gpu["integrated"] == fx["esperado"]["integrada"]
    assert gpu_label(gpu) == fx["esperado"]["principal"]


# --- Portátil solo con integrada ---------------------------------------------

def test_sin_dedicada_se_audita_la_integrada(auditor):
    """Sin dedicada, la integrada SÍ es la principal y su driver sí se audita."""
    si = SystemInfo()
    si.gpus = [{"name": "AMD Radeon(TM) Graphics", "integrated": True, "active": True,
                "driver_date": "2025-06-10", "driver_age_days": 412,
                "vram": 536870912}]
    a = auditor(si)
    resumen = a.check_gpu_drivers()
    assert "412" in resumen
    hallazgo = next(f for f in a.findings if f.id == "gpu_driver")
    assert "AMD Radeon(TM) Graphics" in hallazgo.title


# --- Casos límite ------------------------------------------------------------

def test_sin_graficas(auditor):
    assert primary_gpu([]) is None
    with pytest.raises(audit.SinDato):
        auditor().check_gpu_drivers()


def test_sin_fecha_de_driver(auditor):
    si = SystemInfo()
    si.gpus = [{"name": "X", "driver_age_days": None}]
    with pytest.raises(audit.SinDato) as ctx:
        auditor(si).check_gpu_drivers()
    assert "X" in str(ctx.value)


def test_campos_ausentes_no_revientan():
    # Un JSON de una versión anterior no trae `active` ni `integrated`.
    antiguo = [{"name": "NVIDIA GeForce RTX 3060", "vram": 12884901888},
               {"name": "AMD Radeon(TM) Graphics", "vram": 536870912}]
    assert primary_gpu(antiguo)["name"] == "NVIDIA GeForce RTX 3060"
