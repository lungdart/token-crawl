"""The four directions are one table, and the table has to agree with itself.

Movement, generation, the exit list and the minimap all need the same handful of facts
about n/s/e/w. They used to each keep a copy, and the copy inside
`repo.neighbor_summaries` is the one that decides which neighbouring exit `ensure_area`
forces open: a flipped sign there makes a passage that only works one way, and the room
is cached that way forever. So there is one definition, next to `Exits`, and this checks
it — both that the tables cover exactly the exits a room can have, and that stepping one
way then back lands where it started.
"""
import inspect
import re

from app.engine import movement
from app.models.entities import DELTA, DIRWORD, OPPOSITE, Exits
from app.web import routes, svgrender
from app.world import repo

# A dict literal keyed by a direction letter — how each of the copies was written.
DIR_KEY = re.compile(r"""["'][nsew]["']\s*:""")


def test_the_tables_cover_exactly_the_exits_a_room_has():
    assert set(DELTA) == set(Exits.model_fields)
    assert set(OPPOSITE) == set(Exits.model_fields)
    assert set(DIRWORD) == set(Exits.model_fields)


def test_opposite_undoes_the_step():
    for d, (dx, dy) in DELTA.items():
        assert OPPOSITE[OPPOSITE[d]] == d, f"{d} is not its own opposite's opposite"
        assert DELTA[OPPOSITE[d]] == (-dx, -dy), (
            f"going {d} is {(dx, dy)} but coming back {OPPOSITE[d]} is {DELTA[OPPOSITE[d]]}")


def test_direction_words_name_their_direction():
    for d, word in DIRWORD.items():
        assert word.startswith(d), f"{d} is spelled {word!r}"


def test_nothing_else_keeps_its_own_copy():
    """Every consumer imports the table. A local one would be free to drift from it."""
    copies = [
        f"{module.__name__}: {hit.group(0)!r}"
        for module in (movement, repo, routes, svgrender)
        for hit in DIR_KEY.finditer(inspect.getsource(module))
    ]
    assert not copies, (
        "these modules write out a direction table of their own instead of importing "
        "app.models.entities:\n" + "\n".join(copies)
    )
