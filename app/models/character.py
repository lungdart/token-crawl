"""Class, ability, and level-up shapes.

The AI sets the numbers. What it balances them against is the scale table in
app/models/scale.py. Code owns only the XP curve, which stays global so XP remains
comparable on the leaderboard.
"""
from typing import Literal

from pydantic import BaseModel, Field

from app.models.effects import Effect

# A level costs this many kills, at every depth, forever. It is the same eight that buys a
# common piece of gear, so levelling and shopping keep pace with each other.
#
# The curve is DERIVED from the scale table rather than written out beside it. A second
# hand-written curve would have to be kept in step with the first by hand, and would not
# be: an experience cost that compounds against an enemy value that does not makes levels
# quietly unreachable a few dozen levels down. Deriving it means eight kills a level holds
# by construction, however deep anyone gets.
#
# There is no level cap. Grinding always pays.
KILLS_PER_LEVEL = 8

_xp_cache: dict[int, int] = {0: 0, 1: 0}


def xp_to_reach(level: int) -> int:
    """Total experience needed to be this level. Global and identical for every crawler,
    so experience stays comparable on the leaderboard."""
    from app.models.scale import enemy

    if level < 1:
        return 0
    known = max(k for k in _xp_cache if k <= level)
    total = _xp_cache[known]
    for lvl in range(known, level):
        total += KILLS_PER_LEVEL * enemy(lvl).xp
        _xp_cache[lvl + 1] = total
    return total


class ClassResource(BaseModel):
    """The class's own spendable pool. Themed entirely by its name — the engine tracks a
    number and never needs to know what 'Rage' means (design review #9)."""
    name: str = Field(max_length=24, description="e.g. MP, Rage, Charge, Sanity, Grease Reserves.")
    max_value: int = Field(ge=1, le=999)
    starts_full: bool = Field(description="True for pools like MP; False for bars that build up like Rage.")
    per_turn: int = Field(default=0, ge=-99, le=99, description="Drift each combat turn: regen (+) or decay (-).")
    refills_in_safe_room: bool = True


class Ability(BaseModel):
    name: str = Field(max_length=50)
    flavor: str = Field(max_length=250)
    cooldown: int = Field(default=0, ge=0, le=6, description="Turns between uses. 0 = usable every turn.")
    resource_cost: int = Field(default=0, ge=0, le=999, description="Spent from the class resource. 0 = free.")
    hp_cost: int = Field(default=0, ge=0, le=999, description="Paid from the caster's own HP. 0 = free.")
    effects: list[Effect] = Field(min_length=1, max_length=2)


class StartingItemSpec(BaseModel):
    """Creative brief for a starting item, minted via item generation."""
    hint: str = Field(max_length=120)
    slots: list[Literal["hand", "head", "body", "legs", "feet", "accessory"]] = Field(
        default_factory=list, max_length=4,
    )


class CrawlerClass(BaseModel):
    name: str = Field(max_length=60, description="The class name the dungeon assigns, e.g. 'Discount Deity'.")
    flavor: str = Field(max_length=500)
    max_hp: int = Field(ge=1, le=9999)
    attack: int = Field(ge=0, le=999)
    defense: int = Field(ge=0, le=999)
    speed: int = Field(ge=0, le=999)
    resource: ClassResource | None = Field(
        default=None, description="The class's spendable pool, or null for a cooldown-only class.",
    )
    starting_items: list[StartingItemSpec] = Field(min_length=1, max_length=2)
    starting_abilities: list[Ability] = Field(min_length=1, max_length=2)


class LevelUp(BaseModel):
    """What this class gains at a given level. Generated once per class+level and cached."""
    max_hp: int = Field(ge=0, le=999)
    attack: int = Field(ge=0, le=99)
    defense: int = Field(ge=0, le=99)
    speed: int = Field(ge=0, le=99)
    resource_max: int = Field(default=0, ge=0, le=999, description="Increase to the class resource's max, if any.")
    new_ability: Ability | None = Field(default=None, description="An ability learned at this level, or null.")
    announcement: str = Field(max_length=300, description="Prose shown to the crawler on levelling.")
