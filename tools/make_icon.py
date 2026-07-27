# -*- coding: utf-8 -*-
"""Convierte quilate.png en el icono multirresolucion quilate.ico.

Windows no escala un unico tamano: escoge la resolucion mas cercana segun donde
aparezca el icono (16 px en la barra de titulo, 32 en el escritorio, 256 en la
vista de iconos grandes). Un .ico con un solo tamano se ve borroso en el resto,
asi que se generan todas de una vez.

Uso:  python tools/make_icon.py [origen.png] [destino.ico]
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("[!] Falta Pillow.  Instalalo con:  pip install pillow")
    sys.exit(1)

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "quilate.png"
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "quilate.ico"

    if not source.exists():
        print(f"[!] No existe {source}")
        return 1

    image = Image.open(source).convert("RGBA")
    if image.width != image.height:
        # Un .ico no cuadrado sale deformado: se rellena hasta el lado mayor.
        side = max(image.size)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        image = canvas

    image.save(target, format="ICO", sizes=SIZES)
    print(f"{target.name}  <-  {source.name}  "
          f"({image.width}x{image.height} → {len(SIZES)} resoluciones, "
          f"{target.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
