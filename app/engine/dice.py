import random

from app.models.effects import DICE_RE


def roll(expr: str, rng: random.Random) -> int:
    """Roll dice notation '2d6+1' or a plain integer '3'.

    Accepts exactly what the DiceExpr schema accepts — same pattern, one source.
    """
    m = DICE_RE.match(expr)
    if not m:
        raise ValueError(f"bad dice expr: {expr!r}")
    if m.group("flat") is not None:
        return int(m.group("flat"))
    n, sides, mod = int(m.group("count")), int(m.group("sides")), int(m.group("mod") or 0)
    return sum(rng.randint(1, sides) for _ in range(n)) + mod
