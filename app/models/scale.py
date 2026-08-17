"""The scale table: what a balanced character has at each level.

This is the one place numbers are anchored. Everything else the AI writes — enemies,
weapons, armour, abilities — is described as a LEVEL and derived from the row for that
level. Tuning the game means editing a row here, not five ranges in every floor file.

A row is a character with no class and no race, wearing gear appropriate to their level:

    max_hp, attack, defense, speed   their own numbers
    damage_per_hit                   what they actually deal per hit once geared
    kill_xp                          the experience an enemy of that level is worth
    kill_gold                        the gold an enemy of that level drops

`damage_per_hit` is deliberately NOT part of attack. `attack` decides whether a swing
lands; the weapon decides how much it hurts. Without this column an item generator has
nothing to aim at and a sword could add 1 or 50 without contradicting anything.

The table is hand-written for levels 1-20 — the levels anyone will ever tune. Past 20 it
continues at the rate of the last few rows, so there is no seam and no second set of
numbers to keep in step. Floors go down forever; the table cannot, and nothing lives past
level 20 until levelling goes past 10 anyway.
"""
from __future__ import annotations

from typing import NamedTuple


class Row(NamedTuple):
    level: int
    max_hp: int
    attack: int
    defense: int
    speed: int
    damage_per_hit: int
    kill_xp: int
    kill_gold: int


TABLE: tuple[Row, ...] = (
    Row(1, 22, 5, 3, 4, 7, 6, 5),
    Row(2, 27, 6, 4, 4, 9, 9, 7),
    Row(3, 33, 7, 4, 5, 11, 13, 9),
    Row(4, 39, 8, 5, 5, 13, 18, 12),
    Row(5, 46, 9, 6, 6, 16, 24, 15),
    Row(6, 53, 10, 6, 6, 19, 31, 19),
    Row(7, 61, 11, 7, 7, 22, 40, 24),
    Row(8, 69, 12, 8, 7, 25, 50, 30),
    Row(9, 78, 13, 8, 8, 29, 60, 37),
    Row(10, 87, 14, 9, 8, 33, 72, 45),
    Row(11, 97, 15, 10, 9, 37, 85, 55),
    Row(12, 107, 16, 10, 9, 41, 100, 66),
    Row(13, 118, 17, 11, 10, 46, 117, 79),
    Row(14, 129, 18, 12, 10, 51, 136, 94),
    Row(15, 141, 19, 12, 11, 56, 157, 111),
    Row(16, 153, 20, 13, 11, 61, 180, 130),
    Row(17, 166, 21, 14, 12, 67, 206, 152),
    Row(18, 179, 22, 14, 12, 73, 234, 177),
    Row(19, 193, 23, 15, 13, 79, 265, 205),
    Row(20, 207, 24, 16, 13, 85, 299, 237),
)

TOP = TABLE[-1].level
_TAIL = 4  # rows averaged to work out the rate the table is climbing at


def row(level: int) -> Row:
    """The balanced character at this level. Past the table, keep climbing at the rate the
    last few rows were climbing at."""
    level = max(1, int(level))
    if level <= TOP:
        return TABLE[level - 1]
    last, earlier = TABLE[-1], TABLE[-1 - _TAIL]
    steps = level - TOP
    out = [level]
    for i in range(1, len(Row._fields)):
        rate = (last[i] - earlier[i]) / _TAIL
        out.append(max(1, round(last[i] + rate * steps)))
    return Row(*out)


# --- enemies -----------------------------------------------------------------
#
# Enemies are NOT on equal footing with characters, by design: a crawler should beat an
# equal-level enemy reliably, barring terrible luck, and get through 5 to 10 of them before
# dying is a real possibility. Attack, defense and speed match the character row so the odds
# of landing a hit stay level; what makes an enemy losable is that it has less life and hits
# softer. The shares below are what produce "5 to 10 fights", and
# tests/test_scale.py checks every row of the table still lands in that band.

ENEMY_HP_SHARE = 0.40
ENEMY_DAMAGE_SHARE = 0.35
FIGHTS_BEFORE_DEATH = (5, 10)


class EnemyRow(NamedTuple):
    level: int
    hp: int
    attack: int
    defense: int
    speed: int
    damage_per_hit: int
    xp: int
    gold: int


def enemy(level: int) -> EnemyRow:
    """The ordinary enemy of a given level — the thing the AI writes variations on."""
    r = row(level)
    return EnemyRow(
        level=r.level,
        hp=max(1, round(r.max_hp * ENEMY_HP_SHARE)),
        attack=r.attack,
        defense=r.defense,
        speed=r.speed,
        damage_per_hit=max(1, round(r.damage_per_hit * ENEMY_DAMAGE_SHARE)),
        xp=r.kill_xp,
        gold=r.kill_gold,
    )


def fights_survived(level: int) -> float:
    """How many equal-level enemies a balanced character kills before the damage adds up to
    their life. The number the enemy shares exist to produce."""
    c, e = row(level), enemy(level)
    hit = 0.6  # equal attack and defense; see engine/combat.py
    rounds_to_kill = e.hp / (hit * c.damage_per_hit)
    damage_taken_per_fight = rounds_to_kill * hit * e.damage_per_hit
    return c.max_hp / damage_taken_per_fight


# --- money -------------------------------------------------------------------
#
# One anchor: eight kills' worth of gold buys a common piece of GEAR of your level. Eight
# is also what a level costs in kills, so gear is about a once-a-level purchase at every
# depth — it keeps pace by itself because both sides come off the same column.
#
# Consumables sit well below that, a kill or two, so the shop is somewhere you stop at
# most times you pass it rather than somewhere you save up for. Rare gear is many levels
# of saving: something you find, or decide to go without other things for.

GEAR_KILLS = 8
CONSUMABLE_SHARE = 0.2   # of the gear price at the same level and rarity

# What a crawler starts with: enough for one ordinary consumable, not enough for gear.
STARTING_GOLD = 10

RARITY_MULTIPLIER = {"junk": 0.15, "common": 1.0, "uncommon": 2.5, "rare": 6.0}


def price(level: int, rarity: str = "common", *, consumable: bool = False) -> int:
    """What a thing of this level and rarity is worth in gold."""
    base = row(level).kill_gold * GEAR_KILLS * RARITY_MULTIPLIER.get(rarity, 1.0)
    if consumable:
        base *= CONSUMABLE_SHARE
    return max(1, round(base))


def kills_to_afford(level: int, rarity: str = "common", *, consumable: bool = False) -> float:
    return price(level, rarity, consumable=consumable) / row(level).kill_gold


# --- floors ------------------------------------------------------------------
#
# How deep a floor is decides how hard it is, and nothing else does. The bands overlap
# heavily at the top so the first few floors forgive pushing on, then widen so arriving
# under-levelled means fighting things several levels above you — which the numbers punish
# sharply (two levels under roughly halves how many fights you survive; three under leaves
# you two or three).
#
# The effect: a thorough crawler gains two or three levels a floor and keeps pace to about
# floor three, falls behind by floor four, and hits a wall around floor five at roughly
# level ten. Nothing stops them — grinding a floor longer closes the gap. There is no cap,
# only a cost.

_FLOOR_BOUNDS = (1, 3, 5, 8, 11, 15, 20, 26, 33, 42, 52)
_FLOOR_GROWTH = 1.25  # how the bands keep widening past the written ones


def _bound(n: int) -> int:
    """The level a floor starts at. n is 1-based."""
    if n <= len(_FLOOR_BOUNDS):
        return _FLOOR_BOUNDS[n - 1]
    value = float(_FLOOR_BOUNDS[-1])
    for _ in range(n - len(_FLOOR_BOUNDS)):
        value *= _FLOOR_GROWTH
    return round(value)


def floor_levels(depth: int) -> tuple[int, int]:
    """The levels a floor at this depth is pitched at."""
    depth = max(1, int(depth))
    return _bound(depth), _bound(depth + 1)


# --- prompt text -------------------------------------------------------------

def character_reference(level: int, actual: dict | None = None) -> str:
    """What a balanced character looks like at this level, and — if we know it — what this
    one actually looks like, so drift gets corrected instead of compounding."""
    r = row(level)
    text = (
        f"A balanced character with no class and no race at level {r.level} has "
        f"{r.max_hp} hp, {r.attack} attack, {r.defense} defense, {r.speed} speed, and deals "
        f"about {r.damage_per_hit} damage per hit once their gear is accounted for.\n"
        "That is a reference point, not a limit. A fragile glass cannon trades life away for "
        "damage, a bruiser does the reverse; total power should land near the reference unless "
        "the concept earns otherwise. You have final say."
    )
    if actual:
        have = ", ".join(f"{k} {v}" for k, v in actual.items())
        text += (
            f"\nThis character currently has: {have}. Compare that against the reference above. "
            "If they have drifted ahead of it, give less this level; if they have fallen behind, "
            "give more. Judge where they ARE, not just the size of the step."
        )
    return text


def enemy_reference(low: int, high: int) -> str:
    lo, hi = enemy(low), enemy(high)
    return (
        f"Enemies on this floor are level {low} to {high}. Say which level each one is, then "
        f"give it numbers befitting that level.\n"
        f"An ordinary level {lo.level} enemy has {lo.hp} hp, {lo.attack} attack, {lo.defense} "
        f"defense, {lo.speed} speed, deals about {lo.damage_per_hit} damage a hit, and is worth "
        f"{lo.xp} xp and about {lo.gold} gold"
        + (f"; an ordinary level {hi.level} one has {hi.hp} hp, {hi.attack} attack, {hi.defense} "
           f"defense, {hi.speed} speed, about {hi.damage_per_hit} damage a hit, {hi.xp} xp and "
           f"about {hi.gold} gold"
           if high != low else "")
        + ".\n"
        "Enemies are deliberately weaker than a character of the same level: a crawler should "
        "beat one reliably and get through several before dying becomes a real possibility. "
        "Weaker things sit under those numbers, dangerous ones over. Exceed them on purpose when "
        "the moment earns it — a rare horror is the fun, not a fault."
    )


def item_reference(level: int) -> str:
    r = row(level)
    return (
        f"Gear here suits level {level}, where a properly equipped character deals about "
        f"{r.damage_per_hit} damage per hit and has around {r.attack} attack and {r.defense} "
        f"defense. A weapon of this level should be most of that damage on its own; a lesser "
        "one less, a prize more. Bonuses to a stat should be small next to those numbers — a "
        "few points, not a doubling.\n"
        + gold_reference(level)
    )


def gold_reference(level: int) -> str:
    """What money is worth here. Gold only means something if prices move with the level."""
    return (
        f"Gold at level {level}: an ordinary kill drops about {row(level).kill_gold}. "
        f"Worn or wielded gear is worth roughly {price(level, 'junk')} for junk, "
        f"{price(level, 'common')} common, {price(level, 'uncommon')} uncommon, "
        f"{price(level, 'rare')} rare. Things that are used up — potions, bombs, a single-use "
        f"charm — are worth far less, around {price(level, 'common', consumable=True)} for an "
        f"ordinary one, so a crawler can buy them often. Set value_gold from what the thing "
        f"actually is, near those numbers."
    )


def floor_reference(low: int, high: int) -> str:
    return enemy_reference(low, high) + "\n\n" + item_reference(high)
