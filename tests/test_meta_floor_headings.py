"""A generated floor is only asked for headings the seed floor demonstrates.

Floor 1 is hand-written and is the exemplar every floor below it is written from. Asking
for a heading it does not have invites back whatever that heading used to hold: design
review 13 deleted the *Layout* section because it was written for a 7x7 grid, and in
unbounded space there is no west half.

The seed heading is allowed to be longer than the requested one — floor 1's *Loot Flavor*
demonstrates *Loot* — but a requested heading with nothing behind it is the drift.
"""
import re
from pathlib import Path

import frontmatter

from app.gen.llm import _generic_fixture
from app.models.floor_brief import GeneratedFloor

SEED_FLOOR = Path(__file__).resolve().parents[1] / "floors" / "floor-01.md"

REQUESTED_RE = re.compile(r"under the headings:\s*([^.]+)\.")


def _headings(body: str) -> list[str]:
    return [line[3:].strip() for line in body.splitlines() if line.startswith("## ")]


def _seed_headings() -> list[str]:
    found = _headings(frontmatter.load(SEED_FLOOR).content)
    assert found, f"{SEED_FLOOR.name} has no ## headings to demonstrate anything"
    return found


def _requested_headings() -> list[str]:
    description = GeneratedFloor.model_fields["theme"].description
    listed = REQUESTED_RE.search(description)
    assert listed, f"the theme description no longer lists its headings:\n{description}"
    return [heading.strip() for heading in listed.group(1).split(",")]


def _undemonstrated(headings: list[str], seed: list[str]) -> list[str]:
    return [h for h in headings
            if not any(s == h or s.startswith(f"{h} ") for s in seed)]


def test_requested_headings_are_ones_the_seed_floor_demonstrates():
    seed = _seed_headings()
    requested = _requested_headings()
    assert requested, "the theme description asks for no headings at all"

    missing = _undemonstrated(requested, seed)
    assert not missing, (
        f"GeneratedFloor.theme asks for {missing}, which floor 1 never writes — it has "
        f"{seed}. Floor 2 down is written from floor 1, so a heading with no exemplar is "
        "filled in from whatever the model imagines it means."
    )


def test_offline_floor_fixture_writes_only_demonstrated_headings():
    """The offline fixture is what a floor looks like with no API key. If it writes a
    section the seed floor dropped, the deleted shape is still in the repo as an example."""
    seed = _seed_headings()
    fixture = _generic_fixture("floor_plan", "")
    assert fixture, "there is no offline floor_plan fixture"

    written = _headings(fixture["theme"])
    extra = _undemonstrated(written, seed)
    assert not extra, (
        f"the offline floor_plan fixture writes {extra}, which floor 1 does not: {seed}"
    )
