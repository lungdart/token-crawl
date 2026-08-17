"""Load floors/*.md into validated FloorBrief models and upsert the floors table.

Floors are infinite. The landing is the origin (0,0) and coordinates run unbounded in
every direction. Stairs and safe rooms are scattered by a *seeded per-coordinate roll* —
code holds the dice because models don't honor stated probabilities reliably, and because
the roll must be identical for every crawler who ever reaches that coordinate.
"""
import hashlib
import json
from pathlib import Path

import frontmatter

from app import db
from app.config import settings
from app.models.floor_brief import FloorBrief

_briefs: dict[int, FloorBrief] = {}

LANDING = (0, 0)


def load_floors(floors_dir: str | None = None) -> dict[int, FloorBrief]:
    """Parse and validate every floor plan. Fails loudly at boot on a bad file."""
    global _briefs
    _briefs = {}
    directory = Path(floors_dir or settings.floors_dir)
    conn = db.get()
    for path in sorted(directory.glob("*.md")):
        post = frontmatter.load(path)
        brief = FloorBrief(**post.metadata, body_md=post.content)
        _briefs[brief.floor] = brief
        conn.execute(
            """INSERT INTO floors (id, slug, title, brief_md, config_json)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 slug=excluded.slug, title=excluded.title,
                 brief_md=excluded.brief_md, config_json=excluded.config_json,
                 status='ready'""",
            (brief.floor, brief.slug, brief.title, post.content,
             brief.model_dump_json(exclude={"body_md"})),
        )
    conn.commit()
    if not _briefs:
        raise RuntimeError(f"no floor plan .md files found in {directory}")
    return _briefs


def get_brief(floor_id: int) -> FloorBrief:
    """The plan for a floor.

    Floor 1 comes from floors/*.md and is loaded at startup. Anything below is written by
    the AI the first time a crawler descends into it, then cached — so this may generate,
    exactly like walking into an unvisited room does.
    """
    brief = _briefs.get(floor_id)
    if brief is not None:
        return brief
    if floor_id < 1:
        raise KeyError(floor_id)

    from app.gen import services

    row = db.get().execute(
        "SELECT * FROM floors WHERE id=? AND status='ready'", (floor_id,)).fetchone()
    if row is None:
        row = services.ensure_floor(floor_id)

    brief = FloorBrief(**json.loads(row["config_json"]), body_md=row["brief_md"])
    _briefs[floor_id] = brief
    # Same as at startup: write this floor's stock lines now, not mid-fight.
    services.warm_response_banks([floor_id])
    return brief


def all_floors() -> list[int]:
    """The floors written so far. Not a limit — there is always another one below."""
    return sorted(_briefs)


def distance_from_landing(x: int, y: int) -> int:
    return abs(x - LANDING[0]) + abs(y - LANDING[1])


def _roll(floor_id: int, x: int, y: int, purpose: str) -> float:
    """Deterministic 0..1 for this coordinate and purpose. Same for every crawler, forever."""
    digest = hashlib.sha256(f"{floor_id}:{x}:{y}:{purpose}".encode()).digest()
    return int.from_bytes(digest[:6], "big") / float(1 << 48)


def has_stairs(brief: FloorBrief, x: int, y: int) -> bool:
    """Stairs down are scattered pseudo-randomly, with the chance ramping the further out
    you push, and a hard ceiling so a bad seed can't produce an endless floor."""
    if (x, y) == LANDING:
        return False
    spec = brief.stairs
    dist = distance_from_landing(x, y)
    if dist < spec.min_distance:
        return False
    if dist >= spec.forced_distance:
        return True
    chance = spec.base_chance + spec.ramp_per_room * (dist - spec.min_distance)
    return _roll(brief.floor, x, y, "stairs") < min(chance, 1.0)


def is_safe_room(brief: FloorBrief, x: int, y: int) -> bool:
    """Safe rooms (shop + inn, no monsters) are scattered the same way. Stairs win ties —
    a room is never both."""
    if (x, y) == LANDING:
        return False
    spec = brief.safe_rooms
    if distance_from_landing(x, y) < spec.min_distance:
        return False
    if has_stairs(brief, x, y):
        return False
    return _roll(brief.floor, x, y, "safe_room") < spec.chance


def concept_key(text: str) -> str:
    """Normalize a character concept for cache keying."""
    norm = " ".join("".join(c.lower() if c.isalnum() or c.isspace() else " " for c in text).split())
    return norm[:120] or "wanderer"
