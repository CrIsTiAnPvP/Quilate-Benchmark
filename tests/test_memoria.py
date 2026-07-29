"""Velocidad de RAM: la que corre, no la que el módulo dice soportar.

En SMBIOS, Speed es la velocidad máxima del módulo y ConfiguredClockSpeed la
real. Sin XMP/EXPO activado difieren, y leer la primera es dar por bueno un
rendimiento que el equipo no tiene.
"""

import pytest

from quilate import audit
from quilate.sysinfo import SystemInfo

AMD_ZEN3 = "AMD Ryzen 5 5600X 6-Core Processor"
AMD_ZEN4 = "AMD Ryzen 7 7800X3D 8-Core Processor"
AMD_ZEN2 = "AMD Ryzen 5 3600 6-Core Processor"
INTEL_13 = "Intel(R) Core(TM) i7-13700K"


def sticks(configurada, nominal, n=2):
    return [{"capacity": 8 * 1024**3, "speed": configurada, "rated_speed": nominal}] * n


COMO_LOS_MODULOS = object()   # `rated` toma el valor de `nominal`


@pytest.fixture
def auditar_ram(auditor):
    """Audita los canales de RAM de un equipo descrito por CPU y velocidades.

    Devuelve `(auditor, resumen)`. Antes era `PerfilSinActivar._auditar`, pero
    las otras dos clases montaban el mismo `SystemInfo` a mano. `rated` se pasa
    aparte de `nominal` para el caso en que los módulos declaran una nominal
    pero el sistema no la reporta a nivel de equipo.

    `cpu` va vacío por defecto, que es lo que deja `SystemInfo()`: los casos
    límite nunca llegan a mirar el fabricante —no disparan `ram_slow`— y
    ponerles uno sería darles una entrada que el test original no tenía.
    """
    def auditar(cpu="", configurada=3200, nominal=3200, n=2,
                total=16 * 1024**3, rated=COMO_LOS_MODULOS):
        si = SystemInfo()
        si.cpu_name = cpu
        si.ram_speed_mhz = configurada
        si.ram_speed_rated_mhz = nominal if rated is COMO_LOS_MODULOS else rated
        si.ram_sticks = sticks(configurada, nominal, n=n)
        si.ram_total = total
        a = auditor(si)
        return a, a.check_ram_channels()
    return auditar


def ram_lenta(a):
    return [f for f in a.findings if f.id == "ram_slow"]


def test_la_captura_de_ddr4_3200_con_xmp_no_se_marca_como_lenta(captura, auditar_ram):
    fx = captura("memoria_ddr4_xmp_activo")
    modulos = fx["physical_memory"]
    configurada = max(m["ConfiguredClockSpeed"] for m in modulos)
    nominal = max(m["Speed"] for m in modulos)
    esperado = fx["esperado"]
    assert configurada == esperado["ram_speed_mhz"]
    assert nominal == esperado["ram_speed_rated_mhz"]

    a, _ = auditar_ram(cpu=AMD_ZEN3, configurada=configurada, nominal=nominal)
    assert ram_lenta(a) == []


# --- Perfil sin activar ------------------------------------------------------
# Casos SINTÉTICOS, no capturados: no se dispone de un equipo sin XMP.

def test_ddr5_sin_expo(auditar_ram):
    # El umbral fijo anterior (2400) nunca lo veía: la base JEDEC de DDR5
    # son 4800, muy por encima de ese corte.
    a, resumen = auditar_ram(cpu=AMD_ZEN4, configurada=4800, nominal=6000)
    hallazgo = next(f for f in a.findings if f.id == "ram_slow")
    assert "4800" in hallazgo.title
    assert "6000" in hallazgo.title
    assert "4800" in resumen


def test_ddr4_sin_xmp(auditar_ram):
    a, _ = auditar_ram(cpu=AMD_ZEN2, configurada=2133, nominal=3200)
    assert ram_lenta(a)


def test_intel_tambien_se_detecta_con_menos_ganancia(auditar_ram):
    # Antes solo se miraba en AMD. En Intel el impacto es menor, no nulo.
    amd, _ = auditar_ram(cpu=AMD_ZEN4, configurada=4800, nominal=6000)
    intel, _ = auditar_ram(cpu=INTEL_13, configurada=4800, nominal=6000)
    ganancia_amd = next(f.gain for f in amd.findings if f.id == "ram_slow")
    ganancia_intel = next(f.gain for f in intel.findings if f.id == "ram_slow")
    assert ganancia_amd > ganancia_intel
    assert ganancia_intel > 0


def test_diferencia_minima_no_dispara(auditar_ram):
    # Los SMBIOS redondean; un 5% de margen evita el falso positivo.
    a, _ = auditar_ram(cpu=INTEL_13, configurada=3200, nominal=3300)
    assert ram_lenta(a) == []


# --- Casos límite ------------------------------------------------------------

def test_un_solo_modulo_es_single_channel(auditar_ram):
    _, resumen = auditar_ram(configurada=3200, nominal=3200, n=1,
                             total=8 * 1024**3)
    assert "single channel" in resumen


def test_sin_datos_de_modulos(auditor):
    # Sin módulos enumerados no se puede decir «correcto»: no hay dato.
    with pytest.raises(audit.SinDato):
        auditor().check_ram_channels()


def test_sin_velocidad_nominal(auditar_ram):
    a, _ = auditar_ram(configurada=3200, nominal=0, rated=None)
    assert ram_lenta(a) == []
