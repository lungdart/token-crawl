"""Canonical action union: tier-0/tier-1 parser output the resolver dispatches on."""
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class Move(BaseModel):
    type: Literal["move"]
    direction: Literal["n", "s", "e", "w"]


class Descend(BaseModel):
    type: Literal["descend"]


class Look(BaseModel):
    type: Literal["look"]


class Attack(BaseModel):
    type: Literal["attack"]
    target_key: str


class UseAbility(BaseModel):
    type: Literal["use_ability"]
    ability_name: str


class Take(BaseModel):
    type: Literal["take"]
    target_key: str


class UseItem(BaseModel):
    type: Literal["use_item"]
    item_name: str


class EquipItem(BaseModel):
    type: Literal["equip"]
    item_name: str


class UnequipItem(BaseModel):
    type: Literal["unequip"]
    item_name: str


class Flee(BaseModel):
    type: Literal["flee"]


class Inventory(BaseModel):
    type: Literal["inventory"]


class ShopBuy(BaseModel):
    type: Literal["shop_buy"]
    slot_index: int


class ShopSell(BaseModel):
    type: Literal["shop_sell"]
    item_name: str


class NovelAction(BaseModel):
    """Freeform action needing adjudication. verb/target normalized by the parser."""
    type: Literal["novel"]
    verb_key: str = Field(pattern=r"^[a-z_]{2,30}$")
    target_key: str = Field(pattern=r"^[a-z0-9_]{2,40}$|^_area$")
    raw_text: str


class Rejected(BaseModel):
    """Input that is not a game action — injection, abuse, or out-of-game chatter.
    `refusal` is what the player is told; null means use the floor's own lines."""
    type: Literal["rejected"]
    refusal: str | None = Field(default=None, max_length=300)


CanonicalAction = Annotated[
    Union[
        Move, Descend, Look, Attack, UseAbility, Take, UseItem, EquipItem, UnequipItem,
        Flee, Inventory, ShopBuy, ShopSell, NovelAction, Rejected,
    ],
    Field(discriminator="type"),
]


class ParsedAction(BaseModel):
    """Haiku parser output schema."""
    action: CanonicalAction
