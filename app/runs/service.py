"""Run lifecycle: character creation -> run creation at the landing, leaderboard, re-roll."""
from __future__ import annotations

import json
import secrets

from app import db, logs
from app.engine import movement
from app.engine.state import RunContext
from app.gen import services
from app.models import scale
from app.world import floors, repo


log = logs.get(__name__)


def create_run(session_id: str, name: str, concept: str) -> int:
    """Character creation: class (cached by concept), starting kit, spawn at the floor-1 landing.

    The AI sets the stat line and any class resource; code only supplies the baseline it
    balanced against (design review #7).
    """
    class_id, cls = services.ensure_class(concept, floor_id=1)
    landing = services.ensure_area(1, *floors.LANDING)

    stats = {
        "attack": cls.attack, "defense": cls.defense, "speed": cls.speed,
        "statuses": [], "cooldowns": {},
    }
    if cls.resource:
        r = cls.resource
        stats["resource"] = {
            "name": r.name, "max": r.max_value,
            "current": r.max_value if r.starts_full else 0,
            "per_turn": r.per_turn, "refills_in_safe_room": r.refills_in_safe_room,
        }

    with db.tx() as conn:
        cur = conn.execute(
            """INSERT INTO runs (session_id, name, class_id, floor_id, area_id, hp, max_hp,
                                 stats_json, gold, rng_seed)
               VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)""",
            (session_id, name[:40] or "Crawler", class_id, landing["id"],
             cls.max_hp, cls.max_hp, json.dumps(stats), scale.STARTING_GOLD,
             secrets.randbits(31)),
        )
        run_id = cur.lastrowid
        conn.execute("UPDATE sessions SET active_run_id=? WHERE id=?", (run_id, session_id))

    ctx = RunContext.load(run_id)
    ctx.say(f"{ctx.run['name']}. {cls.name}.", "system")
    ctx.say(cls.flavor, "system")
    for ability in cls.starting_abilities:
        ctx.say(f"You know {ability.name} — {ability.flavor}", "system")
    if cls.resource:
        r = ctx.resource
        ctx.say(f"{r['name']}: {r['current']}/{r['max']}.", "system")

    for spec in cls.starting_items:
        try:
            item_id = services.generate_item(
                1, spec.hint, "common", "starting kit",
                name_key=f"start_{class_id}_{'_'.join(spec.slots) or 'carried'}_{spec.hint[:24]}",
                context=f"carried by a {cls.name}: {cls.flavor}",
            )
            ctx.add_item(item_id)
            item = repo.get_item(item_id)
            entry = next((e for e in ctx.inventory() if e["item_id"] == item_id), None)
            if entry and item.equippable:
                slots = ctx.assign_slots(item)
                if slots:
                    ctx.set_equipped(entry["inv_id"], slots)
            ctx.say(f"You carry {item.name}.", "loot")
        except Exception:
            log.exception("starting item generation failed for class %s (%r)", class_id, spec.hint)
            ctx.say(logs.FAULT + " One of your starting items could not be made, "
                                 "so you are beginning without it.", "system")

    ctx.save_area_state(ctx.area_state())  # the landing counts as explored
    movement.ensure_area_enemies(ctx)
    movement.ensure_area_art(ctx)
    movement.describe_area(ctx)
    ctx.persist()
    return run_id


def active_run(session_id: str) -> int | None:
    row = db.get().execute(
        """SELECT r.id FROM sessions s JOIN runs r ON r.id = s.active_run_id
           WHERE s.id=? AND r.status='alive'""",
        (session_id,),
    ).fetchone()
    return row["id"] if row else None


def _board_rows(limit: int):
    """Leaderboard order: floor -> rooms explored -> xp -> kills (design review #7a).

    Rooms explored rewards thorough play over lucky stair placement — with stairs scattered,
    two crawlers can both reach floor 3 having seen 8 rooms or 60.
    """
    return db.get().execute(
        """SELECT r.id, r.name, c.class_json, r.status, r.floor_id, r.level, r.kills, r.xp,
                  r.death_cause,
                  (SELECT COUNT(*) FROM run_area_state ras WHERE ras.run_id = r.id) AS rooms
           FROM runs r JOIN classes c ON c.id = r.class_id
           WHERE r.status = 'dead'
           ORDER BY r.floor_id DESC, rooms DESC, r.xp DESC, r.kills DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()


def leaderboard(limit: int = 20) -> list[dict]:
    out = []
    for r in _board_rows(limit):
        cls = json.loads(r["class_json"]) if r["class_json"] else {}
        out.append({
            "name": r["name"], "class_name": cls.get("name", "?"), "status": r["status"],
            "floor": r["floor_id"], "rooms": r["rooms"], "level": r["level"],
            "kills": r["kills"], "xp": r["xp"], "death_cause": r["death_cause"],
        })
    return out


def rank_of(run_id: int) -> int | None:
    row = db.get().execute(
        """WITH board AS (
             SELECT r.id, ROW_NUMBER() OVER (
               ORDER BY r.floor_id DESC,
                        (SELECT COUNT(*) FROM run_area_state ras WHERE ras.run_id = r.id) DESC,
                        r.xp DESC, r.kills DESC) AS rn
             FROM runs r WHERE r.status = 'dead')
           SELECT rn FROM board WHERE id=?""",
        (run_id,),
    ).fetchone()
    return row["rn"] if row else None


def recent_events(run_id: int, limit: int = 200) -> list[dict]:
    rows = db.get().execute(
        "SELECT kind, text FROM run_events WHERE run_id=? ORDER BY id DESC LIMIT ?",
        (run_id, limit),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]
