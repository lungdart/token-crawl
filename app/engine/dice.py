import random
import re

_DICE = re.compile(r"^(\d{1,2})d(\d{1,3})([+-]\d{1,3})?$")


def roll(expr: str, rng: random.Random) -> int:
    """Roll dice notation '2d6+1' or a plain integer '3'."""
    expr = expr.strip()
    if expr.lstrip("-").isdigit():
        return int(expr)
    m = _DICE.match(expr)
    if not m:
        raise ValueError(f"bad dice expr: {expr!r}")
    n, sides, mod = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    return sum(rng.randint(1, sides) for _ in range(n)) + mod
