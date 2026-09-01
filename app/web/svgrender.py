"""Server-rendered SVG helpers. Shared home for the minimap now and, later, the
pixel-table sprite renderer (palette + grid -> SVG rects) for generated art."""
from __future__ import annotations

from xml.sax.saxutils import escape as xml_escape

from app import db
from app.models.entities import DELTA

CELL = 26
PAD = 6
TICK = 2  # how far an exit tick reaches either side of the wall it pierces


def minimap_svg(run: dict) -> str:
    """Explored-areas minimap for THIS crawler: rects per visited cell, exit ticks,
    marker on the current cell, skulls where this crawler has seen death echoes."""
    rows = db.get().execute(
        """SELECT a.id, a.x, a.y, a.content_json, a.is_safe_room, a.has_stairs_down,
                  (a.id = ?) AS here,
                  EXISTS(SELECT 1 FROM runs d WHERE d.death_area_id = a.id AND d.id != ?) AS echo
           FROM run_area_state ras JOIN areas a ON a.id = ras.area_id
           WHERE ras.run_id = ?""",
        (run["area_id"], run["id"], run["id"]),
    ).fetchall()
    if not rows:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40'></svg>"

    import json
    min_x = min(r["x"] for r in rows)
    max_x = max(r["x"] for r in rows)
    min_y = min(r["y"] for r in rows)
    max_y = max(r["y"] for r in rows)
    w = (max_x - min_x + 1) * CELL + PAD * 2
    h = (max_y - min_y + 1) * CELL + PAD * 2

    def px(x: int) -> int:
        return PAD + (x - min_x) * CELL

    def py(y: int) -> int:
        return PAD + (max_y - y) * CELL  # north is up

    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' class='minimap'>"]
    for r in rows:
        x0, y0 = px(r["x"]), py(r["y"])
        cls = "cell here" if r["here"] else "cell"
        parts.append(f"<rect x='{x0 + 2}' y='{y0 + 2}' width='{CELL - 4}' height='{CELL - 4}' rx='3' class='{cls}'/>")
        exits = json.loads(r["content_json"])["exits"] if r["content_json"] else {}
        cx, cy = x0 + CELL // 2, y0 + CELL // 2
        for d, (dx, dy) in DELTA.items():
            if not exits.get(d):
                continue
            dy = -dy  # north is up, so the grid's +y is the screen's -y
            lx1, ly1 = cx + dx * (CELL // 2 - TICK), cy + dy * (CELL // 2 - TICK)
            lx2, ly2 = cx + dx * (CELL // 2 + TICK), cy + dy * (CELL // 2 + TICK)
            parts.append(f"<line x1='{lx1}' y1='{ly1}' x2='{lx2}' y2='{ly2}' class='exit'/>")
        glyph = None
        if r["echo"]:
            glyph = "☠"  # skull
        elif r["is_safe_room"]:
            glyph = "$"
        elif r["has_stairs_down"]:
            glyph = "▼"
        if glyph:
            # The map is emitted with `| safe`, so anything textual must be escaped here.
            # Today `glyph` is a hardcoded literal, but escaping keeps that a non-issue if
            # room names or other model-authored text are ever put on the map.
            parts.append(f"<text x='{cx}' y='{cy + 4}' text-anchor='middle' "
                         f"class='glyph'>{xml_escape(glyph)}</text>")
    parts.append("</svg>")
    return "".join(parts)
