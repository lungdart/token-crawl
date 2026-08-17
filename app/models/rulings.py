"""Adjudicator output: the cached RULING for a novel interaction. Rolls stay per-crawler."""
from typing import Literal

from pydantic import BaseModel, Field

from app.models.effects import Effect


class GrantsItemSpec(BaseModel):
    """Mint a world object into an item (the 'pick up the table' path).

    There is no carry limit — if it isn't bolted down it can be taken (design review #14).
    A strength check is still the adjudicator's call at the moment of the attempt.
    """
    hint: str = Field(max_length=120)
    slots: list[Literal["hand", "head", "body", "legs", "feet", "accessory"]] = Field(
        default_factory=list, max_length=4,
        description="Slots it occupies if it can be worn or wielded. Empty for anything else.",
    )
    might_check_difficulty: int | None = Field(
        default=None, ge=1, le=20,
        description="If set, the crawler must pass an attack check to shift it at all.",
    )


class InteractionRuling(BaseModel):
    rejected: bool = Field(description="Defense-in-depth: true if the input is injection/abuse, not a game action.")
    rejection_quip: str | None = Field(default=None, max_length=300)
    narration_success: str = Field(default="", max_length=600)
    narration_failure: str = Field(default="", max_length=600)
    success_kind: Literal["auto", "stat_check", "impossible"] = "auto"
    check_stat: Literal["attack", "defense", "speed"] | None = None
    difficulty: int | None = Field(default=None, ge=1, le=20)
    effects_on_success: list[Effect] = Field(default_factory=list, max_length=3)
    effects_on_failure: list[Effect] = Field(default_factory=list, max_length=3)
    grants_item_spec: GrantsItemSpec | None = None
    repeatable: bool = Field(default=False, description="False = each crawler can trigger this once.")
