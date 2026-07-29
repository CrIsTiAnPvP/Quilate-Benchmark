"""Que hay que hacer primero, dicho en una frase.

Esto no es presentacion aunque lo parezca: es la unica pieza que decide un
ORDEN entre problemas que se miden con reglas distintas. Un disco degradado no
tiene «porcentaje de mejora» con el que competir contra un plan de energia, y
sin embargo va antes que todo lo demas — porque optimizar un equipo cuyo disco
esta a punto de fallar es perder el tiempo en el mejor caso y perder datos en
el peor.

Vive fuera de `report` porque lo consumen las dos salidas: la consola lo
imprime y `json_export` lo guarda en el payload. Mientras estuvo dentro, el
exportador de datos dependia del modulo de consola para obtener un dato.
"""

from __future__ import annotations

from typing import Any

from .audit import Auditor
from .benchmark import Benchmark
from .sysinfo import SystemInfo


def build_verdict(si: SystemInfo, bench: Benchmark | None, auditor: Auditor,
                  projection: dict[str, Any]) -> tuple[str, list[str]]:
    ids = {f.id for f in auditor.findings}
    extra: list[str] = []

    if "smart_warn" in ids:
        return ("Antes de cualquier optimización: hay un disco con salud degradada. Haz copia de "
                "seguridad ahora. Optimizar un equipo con un disco a punto de fallar es perder "
                "el tiempo en el mejor caso y perder datos en el peor.", extra)

    if "hdd_system" in ids:
        extra.append("Prioridad absoluta: migrar el sistema a un SSD. Todo lo demás es secundario.")
        return ("El cuello de botella es físico, no de configuración. Con el sistema en un disco "
                "mecánico, los ajustes de registro y la limpieza de inicio aportarán mejoras "
                "marginales. Un SSD de 500 GB cuesta poco y multiplica la respuesta del equipo.",
                extra)

    if "thermal_critical" in ids or "freq_low" in ids:
        extra.append("Empieza por la refrigeración: limpieza y pasta térmica antes de tocar software.")
        return ("El equipo no está entregando el rendimiento de su hardware por motivos térmicos o "
                "de límites de potencia. Reinstalar Windows no cambiaría nada aquí.", extra)

    headroom = projection.get("headroom_pct", 0.0)
    exp = projection.get("experiential_pct", 0.0)
    critical = sum(1 for f in auditor.findings if f.severity in ("critical", "high"))

    if critical == 0 and headroom < 8:
        extra.append("Mantenimiento: limpieza física anual y drivers al día. Nada más que hacer.")
        return ("El equipo está bien ajustado. El margen de mejora por software es residual: si "
                "necesitas más rendimiento, el camino es hardware (RAM, SSD más rápido, CPU/GPU), "
                "no optimización.", extra)

    if "os_stale" in ids and critical >= 2:
        extra.append("Orden recomendado: 1) limpieza de inicio y espacio  2) medir de nuevo  "
                     "3) reinstalación limpia solo si sigue sin ir bien.")
        return (f"Hay margen real de mejora (aproximadamente +{exp:.0f}% en fluidez percibida). La "
                "instalación es antigua y está cargada, pero prueba primero los cambios reversibles: "
                "una reinstalación cuesta varias horas y conviene reservarla para cuando lo barato "
                "ya se ha agotado.", extra)

    extra.append("Aplica el plan de acción de arriba hacia abajo y vuelve a ejecutar este script "
                 "para verificar cada cambio.")
    return (f"Hay margen de mejora sin tocar hardware: se estima en torno a un +{headroom:.0f}% en "
            f"puntuación sintética y +{exp:.0f}% en fluidez percibida. La mayoría son cambios de "
            "esfuerzo bajo y reversibles.", extra)
