# -*- coding: utf-8 -*-
"""Convierte quilate.png en el icono multirresolucion quilate.ico.

Windows no escala un unico tamano: escoge la resolucion mas cercana segun donde
aparezca el icono (16 px en la barra de titulo, 32 en el escritorio, 256 en la
vista de iconos grandes). Un .ico con un solo tamano se ve borroso en el resto,
asi que se generan todas de una vez.

El fondo negro del logo se convierte en transparencia, para que el icono no se
vea como un recuadro negro sobre escritorios claros. Con --keep-background se
mantiene el fondo tal cual.

Uso:  python tools/make_icon.py [origen.png] [destino.ico] [--keep-background]
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

# Umbrales de luminosidad para separar el logo del fondo negro.
FLOOR = 10    # por debajo de esto es fondo: transparente del todo
KNEE = 150    # por encima de esto es logo: opaco del todo


def drop_black_background(image: Image.Image) -> Image.Image:
    """Convierte el fondo negro del logo en transparencia.

    El PNG de origen es un logo dorado con halo sobre negro, es decir, color ya
    multiplicado por su propia opacidad. Se aprovecha eso: la luminosidad de cada
    pixel se usa como canal alfa (fondo negro -> 0, trazo -> 255, halo -> valores
    intermedios, que es lo que mantiene el degradado suave).

    Despues hay que deshacer esa multiplicacion (color / alfa). Sin ese paso el
    halo conserva el ennegrecimiento del fondo original y, sobre un escritorio
    claro, se veria como un cerco gris sucio alrededor de la Q.
    """
    image = image.convert("RGBA")
    px = image.load()
    span = KNEE - FLOOR
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, _a = px[x, y]
            level = max(r, g, b)
            if level <= FLOOR:
                px[x, y] = (0, 0, 0, 0)
                continue
            alpha = 255 if level >= KNEE else int((level - FLOOR) * 255 / span)
            if alpha >= 16:      # por debajo, dividir solo amplificaria el ruido
                factor = 255 / alpha
                r, g, b = (min(255, int(c * factor)) for c in (r, g, b))
            px[x, y] = (r, g, b, alpha)
    return image


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "quilate.png"
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "quilate.ico"

    if not source.exists():
        print(f"[!] No existe {source}")
        return 1

    image = Image.open(source).convert("RGBA")
    if "--keep-background" not in sys.argv:
        image = drop_black_background(image)
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
