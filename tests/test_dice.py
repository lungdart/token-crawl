import random

import pytest
from pydantic import TypeAdapter, ValidationError

from app.engine import dice
from app.models.effects import DICE_RE, DiceExpr, dice_max


def test_plain_integer():
    assert dice.roll("3", random.Random(1)) == 3


def test_dice_range():
    rng = random.Random(42)
    for _ in range(100):
        v = dice.roll("2d6+1", rng)
        assert 3 <= v <= 13


def test_bad_expr():
    with pytest.raises(ValueError):
        dice.roll("banana", random.Random(1))


def test_dice_max():
    assert dice_max("2d6+1") == 13
    assert dice_max("5") == 5


# Every string the grammar has an opinion about, at its edges: what it takes, and the
# near-misses one character outside it.
GRAMMAR = {
    "1d4": True,
    "12d100": True,
    "2d6+1": True,
    "2d6-3": True,
    "0": True,
    "999": True,
    "1d": False,
    "d6": False,
    "2d6+": False,
    "-5": False,
    "1000": False,
    "100d6": False,
    "2d6+1000": False,
    "2d6+1d4": False,
    " 2d6 ": False,
    "": False,
    "banana": False,
}

_DICE_EXPR = TypeAdapter(DiceExpr)


def _accepts(call) -> bool:
    try:
        call()
    except (ValueError, ValidationError):
        return False
    return True


def test_one_grammar_for_schema_and_roller():
    """Decision 4 makes dice parsing an integrity boundary. A string the schema lets
    through but the roller rejects — or the reverse — is a mid-combat throw, so all four
    consumers of the grammar have to agree on every string, not just the common ones."""
    rng = random.Random(7)
    disagree = []
    for expr, valid in GRAMMAR.items():
        verdicts = {
            "DiceExpr": _accepts(lambda: _DICE_EXPR.validate_python(expr)),
            "DICE_RE": DICE_RE.match(expr) is not None,
            "dice.roll": _accepts(lambda: dice.roll(expr, rng)),
            "dice_max": _accepts(lambda: dice_max(expr)),
        }
        odd = {who: v for who, v in verdicts.items() if v is not valid}
        if odd:
            want = "accept" if valid else "reject"
            disagree.append(f"{expr!r}: every consumer should {want} it, but {odd}")

    assert not disagree, "the dice grammar is not one grammar:\n" + "\n".join(disagree)
