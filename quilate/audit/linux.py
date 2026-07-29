"""Las tres comprobaciones que solo tienen sentido en Linux.

El gobernador de frecuencia, el `swappiness` y el temporizador de TRIM. Son
pocas y cortas a proposito: Quilate se disena contra Windows, y en Linux se
limita a lo que se puede leer de `/sys` y `/proc` sin suponer distribucion.
"""

from __future__ import annotations

from pathlib import Path

from ..platform_utils import run_cmd
from .modelo import NoAplica, SinDato


class ChecksLinux:
    """Mixin de `Auditor`. No se instancia sola: usa `self.add()`, que lo pone
    el `__init__` del paquete."""

    # --------------------------------------------------------- solo Linux ----
    def check_linux_governor(self) -> str:
        path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        if not path.exists():
            raise NoAplica("este kernel no expone gobernadores de frecuencia")
        gov = path.read_text().strip()
        if gov in ("powersave", "conservative"):
            self.add(
                id="linux_governor", title=f"Gobernador de CPU en «{gov}»",
                severity="medium", category="cpu", component="cpu_multi",
                detail="Limita la escalada de frecuencia y añade latencia en cargas a ráfagas.",
                gain=0.10, gain_note="respuesta de CPU", effort="bajo", risk="nulo",
                steps=["`sudo cpupower frequency-set -g performance`",
                       "Para hacerlo persistente, configura tuned o un servicio systemd"])
        return gov


    def check_linux_swappiness(self) -> str:
        path = Path("/proc/sys/vm/swappiness")
        if not path.exists():
            raise SinDato("no se ha podido leer /proc/sys/vm/swappiness")
        val = int(path.read_text().strip())
        if val >= 60 and self.si.ram_total > 8 * 1024**3:
            self.add(
                id="linux_swappiness", title=f"vm.swappiness = {val} con RAM abundante",
                severity="low", category="memoria", component="system",
                detail="Un valor alto hace que el kernel envíe páginas a swap antes de lo necesario.",
                gain=0.05, gain_note="latencia bajo carga", effort="bajo", risk="nulo",
                steps=["`sudo sysctl vm.swappiness=10`",
                       "Persistente: añade `vm.swappiness=10` a /etc/sysctl.d/99-tuning.conf"])
        return str(val)


    def check_linux_trim(self) -> str:
        out = run_cmd(["systemctl", "is-enabled", "fstrim.timer"], timeout=10)
        if not out:
            # `systemctl` devuelve código distinto de cero cuando la unidad no
            # existe o está deshabilitada, y run_cmd no da salida en ese caso:
            # sin esto, «no hay TRIM periódico» y «no hay systemd» eran lo mismo.
            # Se sigue sin poder separarlos, pero ahora al menos se dice cuál de
            # los dos fallos ha ocurrido en vez de callarlo.
            raise SinDato(f"systemctl no ha informado del estado de fstrim.timer"
                          + (f": {out.error}" if out.error else ""))
        if "enabled" not in out:
            self.add(
                id="linux_trim", title="fstrim.timer no está activado",
                severity="medium", category="almacenamiento", component="disk",
                detail="Sin TRIM periódico, el SSD degrada su rendimiento de escritura.",
                gain=0.12, gain_note="escritura sostenida", effort="bajo", risk="nulo",
                steps=["`sudo systemctl enable --now fstrim.timer`"])
        return out
