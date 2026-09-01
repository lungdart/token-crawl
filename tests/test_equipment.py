"""Slot capacity is whatever SLOT_INSTANCES lists — there is no second table saying otherwise."""
from app.engine import loot
from app.engine.state import RunContext
from app.models.entities import SLOT_INSTANCES, Item
from app.runs import service
from app.world import repo

RINGS = ["Ring of One", "Ring of Two", "Ring of Three"]


def _ring(name: str) -> int:
    return repo.insert_item(
        None, "test_" + name.lower().replace(" ", "_"),
        Item(name=name, flavor="A plain band.", rarity="common", slots=["accessory"]),
    )


def test_a_third_accessory_displaces_one_instead_of_finding_a_third_slot(game, session_id):
    """The engine counts accessory_1 and accessory_2 and nothing else. A hand-written
    capacity table could claim 3 and no third ring would ever stay on."""
    run_id = service.create_run(session_id, "Doug", "a plumber")
    ctx = RunContext.load(run_id)
    for name in RINGS:
        ctx.add_item(_ring(name))
    for name in RINGS:
        loot.do_equip(RunContext.load(run_id), f"@{name}")

    worn = {e["item"].name: e["slots"] for e in RunContext.load(run_id).inventory()
            if e["equipped"] and e["item"].name in RINGS}
    assert len(worn) == 2, f"three rings went on but only two fit: {worn}"
    assert sorted(s for slots in worn.values() for s in slots) == sorted(SLOT_INSTANCES["accessory"])
