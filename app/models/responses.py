"""Per-floor response bank (design review #1).

The engine constantly needs text for mechanical events — you missed, that wall is solid,
you don't have that. Those lines used to be ~40 string literals hardcoded in Python, which
contradicted the premise (nothing pre-programmed) and meant every floor golf-clapped in the
same voice regardless of theme.

Instead the bank is generated once when a floor is first entered, cached forever like all
other world content, and themed to that floor. The engine picks a line with the run's seeded
RNG.
"""
from pydantic import BaseModel, Field

Lines = Field(min_length=2, max_length=5, description="Short lines, one sentence each.")


class ResponseBank(BaseModel):
    player_miss: list[str] = Lines           # the crawler swings and misses
    enemy_miss: list[str] = Lines            # something attacks the crawler and misses
    blocked_direction: list[str] = Lines     # no exit that way
    nothing_there: list[str] = Lines         # tried to take/act on something absent
    item_not_held: list[str] = Lines         # referenced an item they don't carry
    not_equippable: list[str] = Lines        # tried to wear something that isn't gear
    no_safe_room: list[str] = Lines          # tried to buy/sell outside a safe room
    cannot_afford: list[str] = Lines         # not enough gold
    empty_inventory: list[str] = Lines       # inventory is empty
    ability_on_cooldown: list[str] = Lines   # ability not ready
    resource_too_low: list[str] = Lines      # not enough of the class resource
    no_stairs_here: list[str] = Lines        # tried to descend where there are none
    flee_failed: list[str] = Lines           # tried to escape and couldn't
    rejected: list[str] = Field(
        min_length=3, max_length=6,
        description="Cheeky in-character replies when input is prompt injection, abuse, or "
                    "otherwise not a game action. These address the player directly — the one "
                    "place the dungeon speaks to the person rather than the character.",
    )
    rate_limited: list[str] = Lines          # acting too fast
    generation_paused: list[str] = Lines     # spend cap reached; explored world still playable
