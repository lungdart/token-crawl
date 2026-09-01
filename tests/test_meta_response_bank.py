"""The bank holds exactly the categories the engine asks for.

Every category costs a model call on the first entry to every floor, so one nothing calls
for is paid for on each floor and read by nobody. The reverse is quieter still: `line()`
looks its category up with `getattr`, so a call site naming one the bank has no field for
gets the plain fallback forever, correctly worded and never themed. Neither shows up in a
run — so the two lists are compared here instead.
"""
import re
from pathlib import Path

from app.engine.state import _PLAIN_FALLBACK
from app.models.responses import ResponseBank

APP = Path(__file__).resolve().parents[1] / "app"

# ctx.say_line("player_miss", "combat") / ctx.line("rejected") — how a category is named.
CALL = re.compile(r"""\b(?:say_)?line\(\s*["'](\w+)["']""")


def _requested() -> dict[str, list[str]]:
    """Every category asked for in `app/`, and where it was asked for."""
    found: dict[str, list[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        for lineno, text in enumerate(path.read_text().splitlines(), 1):
            for category in CALL.findall(text):
                found.setdefault(category, []).append(f"{path.relative_to(APP.parent)}:{lineno}")
    return found


def test_the_bank_holds_exactly_the_categories_the_engine_asks_for():
    asked = _requested()
    fields = set(ResponseBank.model_fields)

    unreachable = sorted(fields - set(asked))
    assert not unreachable, (
        "ResponseBank pays to generate these on every floor and nothing ever asks for "
        "them:\n" + "\n".join(unreachable)
    )

    unbanked = sorted(
        f"{category} ({', '.join(asked[category])})" for category in set(asked) - fields
    )
    assert not unbanked, (
        "these call sites ask for a category ResponseBank has no field for, so they read "
        "the plain fallback on every floor:\n" + "\n".join(unbanked)
    )


def test_every_category_has_a_plain_fallback():
    """`line()` falls back to these when a floor's bank cannot be generated. A category
    missing one renders as the generic fault text instead of what actually happened."""
    assert set(_PLAIN_FALLBACK) == set(ResponseBank.model_fields)
