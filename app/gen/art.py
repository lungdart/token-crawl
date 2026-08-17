"""Turning a generated picture into a room's art.

The model draws at full colour and full size. Everything that makes it look like the game
happens here, in code, deterministically:

  shrink       to 64x48
  stretch      contrast to the full range — a dungeon render otherwise sits in one dark
               corner of colour space and collapses onto three or four colours
  quantize     to sixteen colours chosen from this picture, dithered

Dithering is Floyd-Steinberg: the error at each pixel is pushed into its neighbours, so
where the palette cannot hit a colour, two of them alternate and the eye reads a third.
Without it the sixteen colours land as flat blocks and the room looks like four.
"""
from __future__ import annotations

import io
from typing import TYPE_CHECKING

from PIL import Image, ImageOps

from app.models.scene import COLORS, HEIGHT, WIDTH

if TYPE_CHECKING:  # pragma: no cover
    from app.models.scene import RoomArt


def to_room_art(png: bytes) -> dict:
    """Full-size image bytes -> the palette and grid stored for a room."""
    img = Image.open(io.BytesIO(png)).convert("RGB").resize((WIDTH, HEIGHT), Image.LANCZOS)
    img = ImageOps.autocontrast(img, cutoff=1)
    paletted = img.convert("P", palette=Image.Palette.ADAPTIVE, colors=COLORS,
                           dither=Image.Dither.FLOYDSTEINBERG)

    flat = paletted.getpalette()[: COLORS * 3]
    palette = ["#%02x%02x%02x" % tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]
    data = list(paletted.get_flattened_data()
                if hasattr(paletted, 'get_flattened_data') else paletted.getdata())
    rows = ["".join(f"{data[y * WIDTH + x] % COLORS:02x}" for x in range(WIDTH))
            for y in range(HEIGHT)]
    return {"palette": palette, "rows": rows}


def to_png(art: "RoomArt") -> bytes:
    """The stored grid back out as a real image.

    The art reaches the browser as a PNG from its own URL rather than as data embedded in
    the page. Nothing about it touches the HTML document, so there is no escaping question
    to get wrong; the browser caches it like any image; and it needs no JavaScript.
    """
    img = Image.new("P", (WIDTH, HEIGHT))
    flat = []
    for colour in art.palette:
        flat.extend(int(colour[i:i + 2], 16) for i in (1, 3, 5))
    img.putpalette(flat + [0] * (768 - len(flat)))
    img.putdata([art.index_at(x, y) for y in range(HEIGHT) for x in range(WIDTH)])
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
