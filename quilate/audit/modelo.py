"""El vocabulario de la auditoría: qué es un hallazgo y cómo se declara.

Aquí no hay ninguna comprobación. Es lo que todas ellas comparten: las dos
excepciones con las que una comprobación dice que no va a opinar, el conjunto
cerrado de severidades, esfuerzos y riesgos, y el `Finding` que se acaba
pintando en las cuatro salidas.

No depende de nada del resto del paquete, y esa es la razón de que esté
separado: se puede importar desde cualquier sitio sin arrastrar los dos mil
renglones de comprobaciones detrás.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..console import C


class SinDato(Exception):
    """La comprobación no pudo leer lo que necesitaba para opinar.

    Existe porque devolver un mensaje neutro cuando falta el dato hace que el
    informe dé por bueno algo que nadie ha llegado a mirar. Es el mismo error
    de fondo que leer la velocidad nominal de la RAM en vez de la real, pero
    aplicado al propio veredicto: sin dato no hay veredicto, y decirlo es la
    única respuesta honesta.
    """


class NoAplica(Exception):
    """La comprobación no tiene sentido en este equipo.

    TRIM en un disco mecánico o la desfragmentación en un SSD no son datos que
    falten: son preguntas que no procede hacer. No cuentan como pendientes.
    """


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# Los identificadores de hallazgo acaban en un `id=` del HTML y en un enlace que
# apunta a él, sin pasar por `_e()`. Restringirlos aquí convierte esa convención
# en una garantía: ver `Auditor.add`.
_ID_VALIDO = re.compile(r"^[a-z0-9_]+$")

# Los otros dos campos de conjunto cerrado. Se declaran igual que la severidad y
# por el mismo motivo: acaban interpolados en el `.ps1` que se ejecuta como
# Administrador, dentro de unas comillas dobles donde una comilla parte la cadena.
# `gain_note` no puede entrar aquí —es prosa libre— y se escapa en su destino.
_ESFUERZOS = ("bajo", "medio", "alto")
_RIESGOS = ("nulo", "bajo", "medio", "alto")

SEVERITY_TEXT = {"critical": "CRÍTICO", "high": "ALTO", "medium": "MEDIO",
                 "low": "BAJO", "info": "INFO"}
SEVERITY_COLOR = {"critical": "RED", "high": "RED", "medium": "YELLOW",
                  "low": "CYAN", "info": "GREY"}


# La categoría que no promete velocidad. Va aparte en las tres salidas.
SEGURIDAD = "seguridad"


def sev_label(severity: str) -> str:
    """Se resuelve en tiempo de ejecución: si los colores están desactivados
    (--no-color o salida redirigida) no se cuelan códigos ANSI en el informe."""
    color = getattr(C, SEVERITY_COLOR.get(severity, "GREY"), "")
    return f"{color}{SEVERITY_TEXT.get(severity, severity.upper())}{C.RESET}"


@dataclass
class Finding:
    id: str
    title: str
    severity: str
    category: str            # arranque | fluidez | almacenamiento | térmico | memoria | cpu | dispositivos | seguridad
    component: str           # cpu_single | cpu_multi | memory | disk | system
    detail: str
    gain: float              # mejora estimada (fracción, 0.10 = 10%)
    gain_note: str
    effort: str              # bajo | medio | alto
    risk: str                # nulo | bajo | medio | alto
    steps: list[str] = field(default_factory=list)


def security_findings(findings: list[Finding]) -> list[Finding]:
    """Los hallazgos de seguridad, del más grave al menos.

    Van en un bloque propio y no en el plan de acción a propósito. El plan
    ordena por retorno estimado dividido por esfuerzo y lo dice por escrito en
    su encabezado; estos hallazgos no dan retorno —cifrar el disco no acelera
    nada— así que meterlos ahí obligaría o a mentir sobre el criterio de orden,
    o a enseñar un «+0%» en una columna de ganancia, que se lee como un error.

    Son dos cosas que se miden con reglas distintas: una da rendimiento, la otra
    evita un disgusto. Separarlas es lo único que permite ordenar cada una por
    lo que de verdad importa en ella, que aquí es la severidad y nada más.
    """
    return sorted([f for f in findings if f.category == SEGURIDAD],
                  key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.title))
