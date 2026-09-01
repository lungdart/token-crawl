"""The floor plan.

Floor 1 is hand-written — the seed that sets the tone everything below inherits. Floors
under it are written by the AI on the first descent into them and cached like the rest of
the world, so there is no bottom.

Floors are also infinite sideways: no grid, no bounds, no edges. Stairs and safe rooms are
scattered by a seeded per-coordinate roll instead of being placed in fixed cells, since with
unbounded space there is no candidate set to choose from.
"""
from pydantic import BaseModel, Field, model_validator


class StairsSpec(BaseModel):
    """Stairs are distributed pseudo-randomly at interval. Code holds the dice (models don't
    honor stated probabilities reliably); the AI decides what they look like."""

    min_distance: int = Field(default=4, ge=1, le=100, description="No stairs closer than this to the landing.")
    base_chance: float = Field(default=0.04, ge=0.0, le=1.0, description="Per-room chance once past min_distance.")
    ramp_per_room: float = Field(
        default=0.01, ge=0.0, le=1.0,
        description="Added to the chance for each room beyond min_distance, so deeper pushes pay off.",
    )
    forced_distance: int = Field(
        default=18, ge=2, le=500,
        description="Ceiling: at this distance from the landing, stairs are guaranteed. Stops a bad seed "
                    "producing an endless floor.",
    )


class SafeRoomSpec(BaseModel):
    """Safe rooms are shop + inn in one: no monsters, free full heal, stock to buy."""

    chance: float = Field(default=0.07, ge=0.0, le=1.0, description="Per-room chance, scattered like stairs.")
    min_distance: int = Field(default=2, ge=1, le=50)
    stock_slots: int = Field(default=6, ge=2, le=10)


class LevelRange(BaseModel):
    """What this floor is pitched at. Every number on the floor — enemy stats, gear, gold —
    is derived from these levels via the scale table, so this is the floor's whole balance
    setting. Widening it makes the floor more uneven, not just harder."""

    min: int = Field(ge=1, le=200)
    max: int = Field(ge=1, le=200)

    @model_validator(mode="after")
    def _ordered(self):
        if self.max < self.min:
            raise ValueError(f"levels.max ({self.max}) is below levels.min ({self.min})")
        return self


class GeneratedFloor(BaseModel):
    """What the AI writes for a floor below the first.

    Only the content. The numbers — how deep it is, what levels it is pitched at, how
    stairs and safe rooms scatter — are decided by code from the depth, so difficulty
    climbs steadily instead of being invented afresh each floor.
    """

    slug: str = Field(
        pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=40,
        description="Short lowercase handle for this floor, e.g. 'flooded-vaults'.",
    )
    title: str = Field(
        max_length=80,
        description="Display name, e.g. 'Floor 4: The Flooded Vaults'.",
    )
    theme: str = Field(
        max_length=3000,
        description="The floor plan itself, in markdown, under the headings: Theme, "
                    "Denizens, Set Pieces, Loot. Describe the PLACE — what it is "
                    "like to be there, what lives in it, what a crawler finds. Every room, "
                    "creature and item on this floor is written later from this, so it must "
                    "be specific enough to build a floor from and open enough to fill many "
                    "rooms with. Do not write rooms, creatures or items here.",
    )


class FloorBrief(BaseModel):
    """Parsed floor plan. (Class name kept for continuity; the file is a floor plan.)"""

    floor: int
    slug: str
    title: str
    stairs: StairsSpec = StairsSpec()
    safe_rooms: SafeRoomSpec = SafeRoomSpec()
    levels: LevelRange | None = None
    target_enemy_density: float = Field(default=0.5, ge=0.0, le=1.0)
    body_md: str = ""  # theme and flavour, handed to the model verbatim

    @model_validator(mode="after")
    def _levels_from_depth(self):
        """Difficulty comes from how deep the floor is. A hand-written plan may state its
        own range, but it does not have to, and generated ones never do."""
        if self.levels is None:
            from app.models.scale import floor_levels

            lo, hi = floor_levels(self.floor)
            self.levels = LevelRange(min=lo, max=hi)
        return self
