"""Movement, area entry, and area description (incl. death echoes, safe rooms, JIT gen)."""
from __future__ import annotations

from app import db, logs
from app.engine.state import RunContext
from app.gen import services

log = logs.get(__name__)

_DELTA = {"n": (0, 1), "s": (0, -1), "e": (1, 0), "w": (-1, 0)}
_DIRWORD = {"n": "north", "s": "south", "e": "east", "w": "west"}
OPPOSITE = {"n": "s", "s": "n", "e": "w", "w": "e"}


def death_echoes(ctx: RunContext) -> list[str]:
    rows = db.get().execute(
        """SELECT name, death_cause FROM runs
           WHERE death_area_id=? AND id != ? ORDER BY died_at DESC LIMIT 3""",
        (ctx.run["area_id"], ctx.run["id"]),
    ).fetchall()
    return [
        f"The remains of {r['name']} are here. Cause of death: {r['death_cause'] or 'unrecorded'}."
        for r in rows
    ]


def coords(row) -> str:
    """How a room is named now: where it is. Two crawlers comparing notes are talking
    about the same place."""
    return f"Floor {row['floor_id']} \u00b7 {row['x']}, {row['y']}"


def describe_area(ctx: RunContext) -> None:
    """Arriving somewhere.

    The room's description and what is in it are NOT logged: they belong to the room, not
    to the sequence of things that happened, and the page shows them in their own panel
    fed straight from the cache. Only events go in the log — what a crawler did, what
    answered, who died here before.
    """
    row = ctx.area_row()
    for echo in death_echoes(ctx):
        ctx.say(echo, "death")
    if row["is_safe_room"]:
        _describe_safe_room(ctx, row)


def _describe_safe_room(ctx: RunContext, row) -> None:
    """Safe rooms are shop + inn: nothing hostile, free full recovery, stock to buy."""
    try:
        room = services.ensure_safe_room(ctx.run["floor_id"], row["id"], coords(row))
    except Exception:
        log.exception("safe room generation failed for area %s", row["id"])
        ctx.say(logs.FAULT + " The shop here didn't load; you can still rest.", "system")
        heal_and_restore(ctx)
        return
    ctx.say(f"{room.keeper_name}. {room.keeper_flavor}", "system")
    ctx.say(room.greeting)
    heal_and_restore(ctx, room.rest_line)
    ctx.say("Trade here with 'buy <number>' or 'sell <item>'.", "system")


def heal_and_restore(ctx: RunContext, rest_line: str = "") -> None:
    """Safe rooms restore fully, free, on entry (design review #8)."""
    healed = ctx.run["max_hp"] - ctx.run["hp"]
    ctx.run["hp"] = ctx.run["max_hp"]
    ctx.run["stats"]["statuses"] = []
    ctx.run["stats"]["cooldowns"] = {}
    before = (ctx.resource or {}).get("current")
    ctx.refill_resource()
    if rest_line:
        ctx.say(rest_line)
    if healed > 0:
        ctx.say(f"You recover {healed} HP.", "system")
    res = ctx.resource
    if res and before is not None and res["current"] > before:
        ctx.say(f"{res['name']} restored to {res['current']}.", "system")


def ensure_area_enemies(ctx: RunContext) -> None:
    """First sight of an un-statted enemy type triggers stat-block generation. The area's
    own description goes along so the creature belongs to the room it lives in."""
    content = ctx.area()
    for e in ctx.visible_entities():
        if e.kind == "enemy":
            services.ensure_enemy(ctx.run["floor_id"], e.key, e.name,
                                  coords(ctx.area_row()), content.description)


def ensure_area_art(ctx: RunContext) -> None:
    """First sight of a room draws it. A failure here costs the picture, not the room —
    the description still arrives and the crawler plays on."""
    row = ctx.area_row()
    try:
        services.ensure_room_art(
            ctx.run["floor_id"], row["id"], ctx.area().description,
            has_stairs=bool(row["has_stairs_down"]), is_safe_room=bool(row["is_safe_room"]))
    except Exception:
        log.exception("room art failed for area %s", row["id"])


def enter_area(ctx: RunContext, x: int, y: int) -> None:
    row = services.ensure_area(ctx.run["floor_id"], x, y)
    ctx.run["area_id"] = row["id"]
    ctx.run["combat"] = None  # enemy instances reset when you leave
    # Cleared here and set by do_move, so arriving any other way (stairs, a new run)
    # leaves no way back to retreat to.
    ctx.run["stats"]["came_from"] = None
    ctx.save_area_state(ctx.area_state())  # marks visited
    ensure_area_enemies(ctx)
    ensure_area_art(ctx)
    describe_area(ctx)


def hostiles_here(ctx: RunContext) -> bool:
    return any(e.kind == "enemy" for e in ctx.visible_entities())


def do_move(ctx: RunContext, direction: str, *, retreating: bool = False) -> None:
    # Arriving still never provokes anything — you get to look at what is in the room and
    # decide. But you cannot simply stroll past it: leaving means retreating, and
    # retreating can fail. Checked before the exit, because the reason you are not going
    # is the thing in the room, not the wall. A retreat has already won its roll, so it
    # is the one move that passes.
    if hostiles_here(ctx) and not retreating:
        ctx.say_line("blocked_by_enemies", "combat")
        return
    content = ctx.area()
    if not getattr(content.exits, direction):
        ctx.say_line("blocked_direction")
        return
    row = ctx.area_row()
    x, y = row["x"] + _DELTA[direction][0], row["y"] + _DELTA[direction][1]
    ctx.say(f"You go {_DIRWORD[direction]}.")
    enter_area(ctx, x, y)
    # Remembered so retreating goes back the way you came rather than deeper in.
    ctx.run["stats"]["came_from"] = OPPOSITE[direction]


def do_descend(ctx: RunContext) -> None:
    """The stairs always lead somewhere. There is no bottom and no way to finish — a run
    ends when the crawler dies. The floor below is written on the first descent into it."""
    if hostiles_here(ctx):
        ctx.say_line("blocked_by_enemies", "combat")
        return
    row = ctx.area_row()
    if not row["has_stairs_down"]:
        ctx.say_line("no_stairs_here")
        return
    from app.world import floors

    next_floor = ctx.run["floor_id"] + 1
    try:
        brief = floors.get_brief(next_floor)
    except Exception:
        log.exception("could not reach floor %s from run %s", next_floor, ctx.run["id"])
        ctx.say(logs.FAULT + " The way down could not be reached. You are still on this "
                             "floor; try the stairs again.", "system")
        return
    ctx.run["floor_id"] = next_floor
    ctx.say("You descend.", "system")
    ctx.say(brief.title, "system")
    enter_area(ctx, *floors.LANDING)
