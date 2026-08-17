"""XP and level-ups.

The XP curve is global and identical for every crawler (design review #7a) so XP stays
comparable on the leaderboard. What a class *gains* per level is AI-decided and cached per
class+level, so every crawler of a class levels the same way.
"""
from __future__ import annotations

from app import logs
from app.engine.state import RunContext
from app.gen import services
from app.models.character import xp_to_reach

log = logs.get(__name__)


def grant_xp(ctx: RunContext, amount: int) -> None:
    ctx.run["xp"] += amount
    ctx.say(f"+{amount} XP.", "system")
    while True:  # no cap: a grinder can always keep climbing
        next_level = ctx.run["level"] + 1
        if ctx.run["xp"] < xp_to_reach(next_level):
            break
        if not level_up(ctx, next_level):
            break


def level_up(ctx: RunContext, new_level: int) -> bool:
    """Returns True if the level was granted. The level number is only written once the
    gains exist — otherwise a failed generation would bank the level and lose its stats
    permanently, since the crawler is then already that level and never retries."""
    try:
        gains = services.ensure_level_up(ctx.run["class_id"], new_level, ctx.run["floor_id"])
    except Exception:
        log.exception("level %s generation failed for class %s",
                      new_level, ctx.run["class_id"])
        ctx.say(logs.FAULT + f" Your level-{new_level} rewards couldn't be worked out; "
                             "you keep the XP and it will be retried.", "system")
        return False

    ctx.run["level"] = new_level

    ctx.run["max_hp"] += gains.max_hp
    ctx.run["hp"] = min(ctx.run["hp"] + gains.max_hp, ctx.run["max_hp"])
    stats = ctx.run["stats"]
    stats["attack"] += gains.attack
    stats["defense"] += gains.defense
    stats["speed"] += gains.speed
    res = ctx.resource
    if res and gains.resource_max:
        res["max"] += gains.resource_max
        res["current"] = min(res["max"], res["current"] + gains.resource_max)

    ctx.say(f"Level {new_level}. {gains.announcement}", "system")
    if gains.new_ability:
        ctx.say(f"You learn {gains.new_ability.name} — {gains.new_ability.flavor}", "system")
