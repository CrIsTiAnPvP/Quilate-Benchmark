"""Rastreo de archivos grandes para recuperar espacio.

Recorre el disco de sistema con un presupuesto de tiempo fijo y clasifica lo que
encuentra. La clasificacion importa mas que el tamano bruto: 40 GB de juegos no
son un problema, pero 40 GB de volcados de memoria e instaladores viejos si.

Solo lee metadatos (os.scandir + stat). No abre, no mueve y no borra nada.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Categorias por extension. El orden no importa: la ruta manda sobre la extension.
EXT_CATEGORIES: dict[str, str] = {
    **{e: "imagen de disco" for e in (".iso", ".img", ".vhd", ".vhdx", ".wim", ".esd")},
    **{e: "maquina virtual" for e in (".vdi", ".vmdk", ".qcow2", ".vbox-prev", ".avhdx")},
    **{e: "volcado de memoria" for e in (".dmp", ".mdmp", ".hdmp")},
    **{e: "instalador" for e in (".msi", ".msix", ".appx", ".exe")},
    **{e: "comprimido" for e in (".zip", ".rar", ".7z", ".tar", ".gz", ".xz", ".cab")},
    **{e: "video" for e in (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".ts", ".m4v")},
    **{e: "copia de seguridad" for e in (".bak", ".old", ".backup", ".fbw", ".tib", ".spf")},
    **{e: "temporal" for e in (".tmp", ".temp", ".part", ".crdownload", ".partial")},
    **{e: "registro" for e in (".log", ".etl", ".evtx", ".blf")},
}

# Categorias cuyo espacio se puede recuperar sin perder nada de valor.
RECLAIMABLE = {"temporal", "volcado de memoria", "registro", "cache", "papelera"}

# Categorias que casi siempre sobran, pero que hay que mirar antes de borrar.
REVIEWABLE = {"copia de seguridad", "instalador", "comprimido", "descarga"}

# Carpetas que no aportan y solo gastan presupuesto de tiempo (o enganan):
# WinSxS son enlaces duros —su tamano aparente no es espacio real—, y las de
# sistema no se tocan nunca.
SKIP_DIRS = {
    "winsxs", "servicing", "assembly", "driverstore", "system volume information",
    "$recycle.bin", "$windows.~ws", "$windows.~bt", "config.msi", ".git",
}

# Carpetas que marcan categoria por ruta, con prioridad sobre la extension.
PATH_CATEGORIES: list[tuple[str, str]] = [
    (os.sep + "temp" + os.sep, "temporal"),
    (os.sep + "tmp" + os.sep, "temporal"),
    (os.sep + "cache" + os.sep, "cache"),
    (os.sep + "cache2" + os.sep, "cache"),
    (os.sep + "caches" + os.sep, "cache"),
    (os.sep + "crashdumps" + os.sep, "volcado de memoria"),
    (os.sep + "minidump" + os.sep, "volcado de memoria"),
    ("backup", "copia de seguridad"),
    (os.sep + "copias" + os.sep, "copia de seguridad"),
    (os.sep + "descargas" + os.sep, "descarga"),
    (os.sep + "downloads" + os.sep, "descarga"),
    (os.sep + "papelera" + os.sep, "papelera"),
]

SPECIAL_FILES = {
    "pagefile.sys": "Archivo de paginación (lo gestiona Windows)",
    "swapfile.sys": "Archivo de intercambio de apps (lo gestiona Windows)",
    "hiberfil.sys": "Hibernación e inicio rápido",
}

# Estos nunca entran en la lista de archivos grandes: son enormes (el de
# paginacion puede pasar de 18 GB) y encabezarian el ranking, pero no se pueden
# borrar ni tiene sentido plantearselo. Se informan aparte, con su explicacion.
SKIP_FILES = set(SPECIAL_FILES) | {"dumpstack.log", "dumpstack.log.tmp"}

# Cuantos ficheros se guardan de cada categoria para poder desglosarla.
PER_CATEGORY_TOP = 15


@dataclass
class ScanResult:
    roots: list[str] = field(default_factory=list)
    min_size: int = 0
    files: list[dict] = field(default_factory=list)
    by_category: dict[str, dict] = field(default_factory=dict)
    special: list[dict] = field(default_factory=list)
    total_large: int = 0        # bytes en ficheros por encima del umbral
    reclaimable: int = 0        # bytes en categorías seguras de limpiar
    scanned_files: int = 0
    scanned_dirs: int = 0
    skipped_dirs: int = 0
    elapsed: float = 0.0
    truncated: bool = False     # se agotó el presupuesto de tiempo
    available: bool = True


def _categorize(path: str, name: str) -> str:
    lowered = path.lower()
    for fragment, category in PATH_CATEGORIES:
        if fragment in lowered:
            if category == "descarga":
                # Una descarga se clasifica por lo que es; solo si no encaja en
                # nada conocido se queda como «descarga».
                break
            return category
    ext = os.path.splitext(name)[1].lower()
    if ext in EXT_CATEGORIES:
        return EXT_CATEGORIES[ext]
    if any(f in lowered for f, c in PATH_CATEGORIES if c == "descarga"):
        return "descarga"
    return "otros"


def scan_large_files(roots: list[str], min_size: int = 128 * 1024**2,
                     time_budget: float = 25.0, top: int = 25) -> ScanResult:
    """Recorre `roots` y devuelve los ficheros por encima de `min_size`."""
    result = ScanResult(roots=list(roots), min_size=min_size)
    started = time.perf_counter()
    visited: set[str] = set()
    found: list[dict] = []
    now = datetime.now()
    checks = 0

    for root in roots:
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                real = os.path.realpath(current)
            except OSError:
                continue
            if real.lower() in visited:
                continue
            visited.add(real.lower())

            checks += 1
            if checks % 64 == 0 and time.perf_counter() - started > time_budget:
                result.truncated = True
                stack.clear()
                break

            try:
                with os.scandir(current) as it:
                    entries = list(it)
            except (PermissionError, OSError):
                result.skipped_dirs += 1
                continue
            result.scanned_dirs += 1

            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.lower() in SKIP_DIRS:
                            result.skipped_dirs += 1
                            continue
                        stack.append(entry.path)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except (PermissionError, OSError):
                    continue

                result.scanned_files += 1
                if stat.st_size < min_size or entry.name.lower() in SKIP_FILES:
                    continue
                category = _categorize(entry.path, entry.name)
                age_days = max(0, (now - datetime.fromtimestamp(stat.st_mtime)).days)
                found.append({
                    "path": entry.path,
                    "name": entry.name,
                    "size": stat.st_size,
                    "category": category,
                    "age_days": age_days,
                })
        if result.truncated:
            break

    found.sort(key=lambda f: -f["size"])
    for f in found:
        bucket = result.by_category.setdefault(f["category"],
                                               {"count": 0, "size": 0, "files": []})
        bucket["count"] += 1
        bucket["size"] += f["size"]
        # Cada categoría guarda sus mayores para poder desplegarla y ver qué hay
        # dentro sin tener que buscar en la lista general.
        if len(bucket["files"]) < PER_CATEGORY_TOP:
            bucket["files"].append(f)
        result.total_large += f["size"]
        if f["category"] in RECLAIMABLE:
            result.reclaimable += f["size"]

    result.files = found[:top]
    result.by_category = dict(sorted(result.by_category.items(), key=lambda x: -x[1]["size"]))
    result.special = _special_files(roots)
    result.elapsed = time.perf_counter() - started
    return result


def _special_files(roots: list[str]) -> list[dict]:
    """pagefile.sys y compañía: ocupan mucho, no se borran a mano y conviene
    explicarlos para que nadie intente «recuperar» ese espacio por su cuenta."""
    out: list[dict] = []
    drives = {os.path.splitdrive(os.path.abspath(r))[0] + os.sep for r in roots}
    for drive in sorted(drives):
        for name, note in SPECIAL_FILES.items():
            path = Path(drive) / name
            try:
                size = path.stat().st_size
            except (OSError, PermissionError):
                continue
            if size > 0:
                out.append({"path": str(path), "name": name, "size": size, "note": note})
        old = Path(drive) / "Windows.old"
        if old.is_dir():
            out.append({
                "path": str(old), "name": "Windows.old", "size": 0,
                "note": "Instalación anterior de Windows: suele ocupar 10-30 GB y se "
                        "borra desde Liberador de espacio → Instalaciones anteriores",
            })
    return out


def candidate_bytes(scan: ScanResult) -> tuple[int, int]:
    """(basura segura de borrar, candidatos que conviene revisar antes).

    Un único sitio para este cálculo: si la consola, el hallazgo y el HTML lo
    hicieran cada uno por su cuenta acabarían dando cifras distintas.
    """
    review = sum(v["size"] for k, v in scan.by_category.items() if k in REVIEWABLE)
    return scan.reclaimable, review


def default_roots(system_drive: str) -> list[str]:
    """Sitios donde de verdad se acumula: primero el perfil, luego el resto del
    disco de sistema. Se ordenan así porque el presupuesto de tiempo puede
    agotarse antes de terminar y conviene haber mirado lo importante."""
    roots = []
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    if profile and os.path.isdir(profile):
        roots.append(profile)
    if system_drive and os.path.isdir(system_drive):
        roots.append(system_drive)
    return roots or [os.path.expanduser("~")]
