"""The scale table has to deliver the design, not just hold numbers.

The design: a character beats an equal-level enemy reliably barring terrible luck, and gets
through 5 to 10 of them before dying is a real possibility. These check every row against
that, so a hand-edited row that breaks it fails here rather than in play.
"""
import pytest

from app.models import scale
from app.models.character import xp_to_reach


def test_every_row_survives_five_to_ten_fights():
    lo, hi = scale.FIGHTS_BEFORE_DEATH
    for r in scale.TABLE:
        n = scale.fights_survived(r.level)
        assert lo <= n <= hi, f"level {r.level}: {n:.1f} fights, wanted {lo}-{hi}"


def test_extrapolated_levels_hold_the_same_shape():
    """Past the table the curve is computed. It must not drift out of the band."""
    lo, hi = scale.FIGHTS_BEFORE_DEATH
    for level in (scale.TOP + 1, 25, 40, 100):
        n = scale.fights_survived(level)
        assert lo <= n <= hi, f"level {level}: {n:.1f} fights, wanted {lo}-{hi}"


def test_a_single_fight_is_never_close():
    """'Reliably, barring terrible luck' — an equal-level enemy should not be able to kill
    a full-health character even hitting every swing for the whole fight."""
    for r in scale.TABLE:
        e = scale.enemy(r.level)
        rounds_to_kill = e.hp / r.damage_per_hit          # every swing lands
        worst_case_damage = rounds_to_kill * e.damage_per_hit
        assert worst_case_damage < r.max_hp * 0.6, f"level {r.level} fight is too close"


def test_the_curve_never_goes_backwards():
    """A lumpy hand-edited row shows up here."""
    for a, b in zip(scale.TABLE, scale.TABLE[1:]):
        for field in ("max_hp", "attack", "defense", "speed", "damage_per_hit", "kill_xp"):
            assert getattr(b, field) >= getattr(a, field), f"{field} drops at level {b.level}"


def test_levelling_takes_about_eight_kills():
    """The xp column and the xp curve have to agree, or levelling drifts away from the
    pace the fights are tuned for."""
    for level in range(1, 13):
        needed = xp_to_reach(level + 1) - xp_to_reach(level)
        kills = needed / scale.enemy(level).xp
        assert 6 <= kills <= 10, f"level {level}->{level + 1} takes {kills:.1f} kills"


def test_levelling_never_stops():
    """No cap: a grinder can always climb. It gets slower, it never stops."""
    for level in (10, 25, 50, 100):
        needed = xp_to_reach(level + 1) - xp_to_reach(level)
        assert needed > 0
        kills = needed / scale.enemy(level).xp
        assert kills < 60, f"level {level}->{level + 1} takes {kills:.0f} kills — unreachable"


def test_table_is_continuous_at_its_edge():
    """No seam where the hand-written rows stop and the computed ones start."""
    last = scale.TABLE[-1]
    nxt = scale.row(scale.TOP + 1)
    step = last.max_hp - scale.TABLE[-2].max_hp
    assert 0 < nxt.max_hp - last.max_hp <= step * 1.5


@pytest.mark.parametrize("level", [0, -5])
def test_levels_below_one_clamp_rather_than_crash(level):
    assert scale.row(level).level == 1


def test_enemies_are_weaker_than_characters_of_the_same_level():
    for r in scale.TABLE:
        e = scale.enemy(r.level)
        assert e.hp < r.max_hp
        assert e.damage_per_hit < r.damage_per_hit


# --- money -------------------------------------------------------------------

def test_gear_always_costs_about_eight_kills():
    """The anchor: a common piece of gear is one level's fighting, at every depth. If this
    drifts, money stops meaning the same thing as you go down."""
    for level in (1, 5, 10, 20, 30, 60):
        kills = scale.kills_to_afford(level, "common")
        assert 7 <= kills <= 9, f"level {level}: {kills:.1f} kills for common gear"


def test_consumables_are_affordable_on_the_way_past():
    """A potion is a couple of kills, so the shop is somewhere you stop routinely."""
    for level in (1, 5, 10, 20):
        kills = scale.kills_to_afford(level, "common", consumable=True)
        assert 1 <= kills <= 3, f"level {level}: {kills:.1f} kills for a potion"


def test_rare_gear_is_something_you_save_for():
    for level in (1, 10, 20):
        assert scale.kills_to_afford(level, "rare") > 30


def test_prices_rise_with_depth():
    """The bug this whole change fixes: a floor 40 sword used to cost what a floor 1 one did."""
    for rarity in ("junk", "common", "uncommon", "rare"):
        prices = [scale.price(lvl, rarity) for lvl in range(1, 25)]
        assert prices == sorted(prices)
        assert prices[-1] > prices[0] * 10


def test_starting_gold_buys_a_potion_but_not_gear():
    assert scale.STARTING_GOLD >= scale.price(1, "common", consumable=True) * 0.8
    assert scale.STARTING_GOLD < scale.price(1, "common")


def test_selling_at_half_never_pays_for_itself():
    """Sell is half price, so buying and reselling has to lose money, or it is a gold press."""
    for level in (1, 10, 20):
        for rarity in ("junk", "common", "uncommon", "rare"):
            assert scale.price(level, rarity) // 2 < scale.price(level, rarity)
