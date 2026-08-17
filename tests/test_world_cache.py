from concurrent.futures import ThreadPoolExecutor

from app.engine.state import RunContext
from app.gen import services
from app.runs import service
from app.world import floors, repo


def test_area_generated_once_then_cached(game):
    row1 = services.ensure_area(1, 0, 0)
    calls = len(game.calls)
    row2 = services.ensure_area(1, 0, 0)
    assert row1["id"] == row2["id"]
    assert len(game.calls) == calls  # cache hit: no new generation


def test_concurrent_race_generates_exactly_once(game):
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: services.ensure_area(1, 2, 3), range(8)))
    assert len({r["id"] for r in rows}) == 1
    assert len([c for c in game.calls if c[0] == "area"]) == 1


def test_exit_reciprocity_enforced(game):
    services.ensure_area(1, 0, 0)
    landing = repo.area_content(repo.ready_area(1, 0, 0))
    if landing.exits.n:
        services.ensure_area(1, 0, 1)
        north = repo.area_content(repo.ready_area(1, 0, 1))
        assert north.exits.s  # must reciprocate


def test_floors_are_infinite(game):
    """No bounds, no forced-closed edges — you can walk arbitrarily far in any direction."""
    for coord in [(50, 0), (-50, 0), (0, -80), (33, -41)]:
        row = services.ensure_area(1, *coord)
        assert row["status"] == "ready"
    far = repo.area_content(repo.ready_area(1, -50, 0))
    assert far.exits.open_dirs()  # never sealed


def test_stairs_scattered_deterministically_with_ceiling(game):
    brief = floors.get_brief(1)
    assert not floors.has_stairs(brief, *floors.LANDING)
    # nothing within the minimum distance
    assert not any(floors.has_stairs(brief, x, 0) for x in range(1, brief.stairs.min_distance))
    # the ceiling guarantees a floor is always completable
    assert floors.has_stairs(brief, brief.stairs.forced_distance, 0)
    # identical for every crawler, forever
    assert floors.has_stairs(brief, 7, 3) == floors.has_stairs(brief, 7, 3)


def test_stairs_are_findable_within_the_ceiling(game):
    brief = floors.get_brief(1)
    walk = [(d, 0) for d in range(1, brief.stairs.forced_distance + 1)]
    assert any(floors.has_stairs(brief, x, y) for x, y in walk)


def test_safe_rooms_scatter_and_never_collide_with_stairs(game):
    brief = floors.get_brief(1)
    cells = [(x, y) for x in range(-12, 13) for y in range(-12, 13)]
    safe = [c for c in cells if floors.is_safe_room(brief, *c)]
    assert safe, "expected some safe rooms in a 25x25 sample"
    assert not any(floors.has_stairs(brief, *c) for c in safe)
    assert floors.LANDING not in safe


def test_enemy_and_drop_slots_cached(game):
    services.ensure_area(1, 0, 0)
    services.ensure_enemy(1, "tunnel_goblin", "Tunnel Goblin", "A Room", "Rough stone.")
    n = len([c for c in game.calls if c[0] == "enemy"])
    services.ensure_enemy(1, "tunnel_goblin", "Tunnel Goblin", "A Room", "Rough stone.")
    assert len([c for c in game.calls if c[0] == "enemy"]) == n

    services.ensure_drop_slots(1, "tunnel_goblin", "Tunnel Goblin", "stabby")
    services.ensure_drop_slots(1, "tunnel_goblin", "Tunnel Goblin", "stabby")
    assert len([c for c in game.calls if c[0] == "drop_table"]) == 1

    etid = repo.enemy_type_id(1, "tunnel_goblin")
    _, slots = repo.ready_drop_slots(etid)
    assert all(s["item_id"] is None for s in slots)  # JIT: no items until a roll lands

    first = services.resolve_drop_slot_item(1, slots[0], "Tunnel Goblin")
    again = services.resolve_drop_slot_item(1, repo.drop_slot(etid, 0), "Tunnel Goblin")
    assert first == again
    assert len([c for c in game.calls if c[0] == "item"]) == 1


def test_enemy_generation_receives_the_room_description(game):
    """Local coherence: don't fight a fire beast on a beach (design review #2)."""
    services.ensure_enemy(1, "slime", "Slime", "The Wet Gallery",
                          "Ankle-deep standing water over cut stone.")
    prompt = next(user for kind, user in game.calls if kind == "enemy")
    assert "Ankle-deep standing water" in prompt


def test_item_generation_receives_its_source(game):
    services.ensure_area(1, 0, 0)
    services.ensure_enemy(1, "tunnel_goblin", "Tunnel Goblin", "A Room", "Rough stone.")
    services.ensure_drop_slots(1, "tunnel_goblin", "Tunnel Goblin", "wears a colander")
    etid = repo.enemy_type_id(1, "tunnel_goblin")
    _, slots = repo.ready_drop_slots(etid)
    services.resolve_drop_slot_item(1, slots[0], "Tunnel Goblin", "wears a colander")
    prompt = next(user for kind, user in game.calls if kind == "item")
    assert "wears a colander" in prompt


def test_class_cached_by_normalized_concept(game):
    id1, _ = services.ensure_class("A Washed-Up PLUMBER!!")
    id2, _ = services.ensure_class("a washed up plumber")
    assert id1 == id2
    assert len([c for c in game.calls if c[0] == "class"]) == 1


def test_response_bank_generated_once_per_floor(game):
    services.ensure_response_bank(1)
    services.ensure_response_bank(1)
    assert len([c for c in game.calls if c[0] == "response_bank"]) == 1


def test_stock_lines_are_written_at_startup_not_mid_fight(game, session_id):
    """They used to be written the first time one was needed, which put a model call in
    the middle of somebody's first missed swing."""
    services.warm_response_banks(floors.all_floors())
    written = len([c for c in game.calls if c[0] == "response_bank"])
    assert written == len(floors.all_floors())

    run_id = service.create_run(session_id, "Doug", "a plumber")
    ctx = RunContext.load(run_id)
    before = len(game.calls)
    assert ctx.line("player_miss")          # the moment that used to generate
    assert len(game.calls) == before, "missing a swing must not call the model"


def test_a_floor_that_fails_to_warm_does_not_stop_the_game(game, monkeypatch):
    """The lazy path is still there, so a bad startup is logged and skipped, not fatal."""
    def boom(_floor_id):
        raise RuntimeError("provider down")

    monkeypatch.setattr(services, "ensure_response_bank", boom)
    services.warm_response_banks([1])  # must not raise
