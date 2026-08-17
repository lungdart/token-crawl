import random

import pytest

from app.engine import dice
from app.models.effects import dice_max


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
