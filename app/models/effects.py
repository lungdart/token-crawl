"""The typed effect system: the only 'language' the LLM authors mechanics in.

Every LLM-generated ability, item power, or ruling outcome is a list[Effect].
Nothing here is executable; app/engine/effects_engine.py is the sole interpreter.

The vocabulary is deliberately closed (design review #6). The model cannot invent a
new verb, and prompts tell it so — narration must never claim an outcome these
verbs can't produce.
"""
from __future__ import annotations

import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

DICE_RE = re.compile(r"^(\d{1,2})d(\d{1,3})([+-]\d{1,3})?$|^(\d{1,3})$")

DiceExpr = Annotated[
    str,
    Field(
        pattern=r"^\d{1,2}d\d{1,3}([+-]\d{1,3})?$|^\d{1,3}$",
        description="Dice notation like '2d6+1', or a plain integer like '3'.",
    ),
]

DamageType = Annotated[str, Field(max_length=24, description="Flavor damage type, e.g. 'physical', 'fire', 'sarcasm'.")]
StatName = Literal["attack", "defense", "speed", "max_hp"]


class Damage(BaseModel):
    type: Literal["damage"]
    amount: DiceExpr
    damage_type: DamageType = "physical"


class Heal(BaseModel):
    type: Literal["heal"]
    amount: DiceExpr


class DamageOverTime(BaseModel):
    type: Literal["damage_over_time"]
    per_turn: DiceExpr
    turns: int = Field(ge=1, le=10)
    damage_type: DamageType = "physical"


class StatModifier(BaseModel):
    type: Literal["stat_modifier"]
    stat: StatName
    amount: int = Field(ge=-10, le=10)
    turns: int | None = Field(default=None, ge=1, le=20, description="None = permanent while equipped/applied.")


class ResourceChange(BaseModel):
    """Add to or drain the class's own resource (design review #9). The engine tracks a
    named number and never needs to know what the name means."""
    type: Literal["resource_change"]
    amount: int = Field(ge=-999, le=999, description="Positive fills the class resource, negative drains it.")


# Effects a trigger may fire. Excludes OnHitTrigger itself: a trigger that fires another
# trigger is pathological, and a self-referential union becomes an unresolvable `$ref`
# loop once strict structured output forces every property to be required.
SimpleEffect = Annotated[
    Union[Damage, Heal, DamageOverTime, StatModifier, ResourceChange],
    Field(discriminator="type"),
]


class OnHitTrigger(BaseModel):
    type: Literal["on_hit_trigger"]
    chance: float = Field(ge=0.0, le=1.0)
    effect: SimpleEffect


Effect = Annotated[
    Union[Damage, Heal, DamageOverTime, StatModifier, ResourceChange, OnHitTrigger],
    Field(discriminator="type"),
]

EFFECT_VERBS = ("damage", "heal", "damage_over_time", "stat_modifier", "resource_change", "on_hit_trigger")


def dice_max(expr: str) -> int:
    """Maximum possible roll of a DiceExpr."""
    m = DICE_RE.match(expr)
    if not m:
        raise ValueError(f"bad dice expr: {expr!r}")
    if m.group(4) is not None:
        return int(m.group(4))
    n, sides = int(m.group(1)), int(m.group(2))
    mod = int(m.group(3) or 0)
    return n * sides + mod
