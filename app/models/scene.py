"""Room art as data: a small palette and a grid of indices into it.

An image model draws the room at full colour and full size; code shrinks it to 64x48 and
picks the sixteen colours that best describe *that picture*, dithering as it maps. The
result is stored and served exactly like every other piece of generated content — no
binary assets, no image hosting, no second cache to keep in step.

Sixteen was chosen by looking, not by taste: at 64 colours and at 256 the pictures are
measurably nearly identical (a mean per-pixel difference of 15, where about 24 is the
smallest difference that reads as another colour), because the source is flat-shaded art
at postage-stamp size. Sixteen has the most contrast between neighbouring areas, which is
what makes it read as pixel art rather than a shrunken photograph.

The palette is per room, not shared. A floor-wide palette was tried and looked worse.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

WIDTH = 64
HEIGHT = 48
COLORS = 16


class RoomArt(BaseModel):
    """A picture of a room: its own palette, and two hex characters per pixel."""

    palette: list[str] = Field(min_length=2, max_length=COLORS)
    rows: list[str] = Field(min_length=HEIGHT, max_length=HEIGHT)

    @field_validator("palette")
    @classmethod
    def _colours(cls, palette: list[str]) -> list[str]:
        for c in palette:
            if len(c) != 7 or c[0] != "#" or any(ch not in "0123456789abcdef" for ch in c[1:].lower()):
                raise ValueError(f"not a colour: {c!r}")
        return [c.lower() for c in palette]

    @field_validator("rows")
    @classmethod
    def _grid(cls, rows: list[str]) -> list[str]:
        for i, row in enumerate(rows):
            if len(row) != WIDTH * 2:
                raise ValueError(f"row {i} is {len(row)} characters, expected {WIDTH * 2}")
        return [r.lower() for r in rows]

    def index_at(self, x: int, y: int) -> int:
        return int(self.rows[y][x * 2:x * 2 + 2], 16)
