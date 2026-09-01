"""World-cache content shapes: what the LLM authors, validated before storage."""
from typing import Literal

from pydantic import BaseModel, Field

from app.models.effects import DiceExpr, Effect

Rarity = Literal["junk", "common", "uncommon", "rare"]

# Where an item is worn. An item lists the slots it occupies; "hand" may appear twice for
# a two-handed weapon. The ENGINE decides which physical side a hand goes in — generation
# picking r/l would be arbitrary and produce two swords both claiming the right hand.
SlotName = Literal["hand", "head", "body", "legs", "feet", "accessory"]

# Physical slot instances. Each is capacity 1, so this list IS the capacity per logical
# slot — the engine reads it and nothing else, so there is no second count to disagree.
SLOT_INSTANCES: dict[str, list[str]] = {
    "hand": ["r_hand", "l_hand"],
    "head": ["head"], "body": ["body"], "legs": ["legs"], "feet": ["feet"],
    "accessory": ["accessory_1", "accessory_2"],
}


class AreaEntity(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]{2,40}$", description="Stable snake_case key unique within the area.")
    kind: Literal["enemy", "feature", "item"]
    name: str = Field(max_length=60)
    brief: str = Field(max_length=200, description="One-line description shown in the area text.")


class Exits(BaseModel):
    n: bool = False
    s: bool = False
    e: bool = False
    w: bool = False

    def open_dirs(self) -> list[str]:
        return [d for d in ("n", "s", "e", "w") if getattr(self, d)]


class AreaContent(BaseModel):
    """A room. It has no name — at infinite scale a name is noise, and coordinates are
    what a crawler can actually share with another crawler."""

    description: str = Field(max_length=1500, description="Second-person prose shown on entering.")
    exits: Exits
    entities: list[AreaEntity] = Field(max_length=8)


class EnemyAbility(BaseModel):
    name: str = Field(max_length=40)
    flavor: str = Field(max_length=150)
    chance: float = Field(ge=0.0, le=1.0, description="Chance the enemy uses this instead of a basic attack.")
    effects: list[Effect] = Field(max_length=3)


class EnemyStatBlock(BaseModel):
    name: str = Field(max_length=60)
    flavor: str = Field(max_length=300)
    level: int = Field(
        default=1, ge=1, le=200,
        description="What level this thing is pitched at. Its numbers should suit that level.",
    )
    hp: int = Field(ge=1, le=100000)
    attack: int = Field(ge=0, le=1000)
    defense: int = Field(ge=0, le=1000)
    speed: int = Field(ge=0, le=1000)
    xp: int = Field(ge=1, le=100000)
    gold: DiceExpr = "0"
    attack_dice: DiceExpr = "1d4"
    abilities: list[EnemyAbility] = Field(default_factory=list, max_length=2)


class Item(BaseModel):
    name: str = Field(max_length=60)
    flavor: str = Field(max_length=300)
    rarity: Rarity
    slots: list[SlotName] = Field(
        default_factory=list, max_length=6,
        description="Slots this occupies when equipped. Empty = not equippable (e.g. a potion). "
                    "List 'hand' twice for a two-handed weapon.",
    )
    use_effects: list[Effect] = Field(
        default_factory=list, max_length=4,
        description="What happens when the item is USED from inventory (a potion, a key, a bomb). "
                    "Empty = nothing happens on use.",
    )
    equip_effects: list[Effect] = Field(
        default_factory=list, max_length=4,
        description="Passive effects while equipped. Only meaningful if slots is non-empty.",
    )
    consumed_on_use: bool = Field(default=False, description="True for potions and one-shot items.")
    value_gold: int = Field(default=0, ge=0, le=100000)
    attack_dice: DiceExpr | None = Field(default=None, description="Weapons only: damage dice when wielded.")

    @property
    def equippable(self) -> bool:
        return bool(self.slots)

    @property
    def usable(self) -> bool:
        return bool(self.use_effects)


class DropSlot(BaseModel):
    weight: int = Field(ge=1, le=100)
    qty_dice: DiceExpr = "1"
    rarity: Rarity
    hint: str = Field(max_length=120, description="Creative brief for the item generated when this slot is first rolled.")


class DropTable(BaseModel):
    nothing_weight: int = Field(ge=0, le=100)
    slots: list[DropSlot] = Field(min_length=1, max_length=6)


class ShopStockSlot(BaseModel):
    rarity: Rarity
    hint: str = Field(max_length=120)
    price: int = Field(ge=1, le=100000)


class SafeRoom(BaseModel):
    """A safe room is shop + inn in one: no monsters, free full heal on entry, stock to buy."""
    keeper_name: str = Field(max_length=60)
    keeper_flavor: str = Field(max_length=400)
    greeting: str = Field(max_length=300)
    rest_line: str = Field(max_length=300, description="Shown when the crawler is healed on entry.")
    stock: list[ShopStockSlot] = Field(min_length=2, max_length=10)
