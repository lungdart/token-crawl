"""Scripted end-to-end run over the fixture world. Zero API calls."""
from app import db
from app.engine import resolver
from app.engine.state import RunContext
from app.runs import service
from app.world import floors, repo


def _act(run_id, text):
    return resolver.handle_action(run_id, text, session_id="testsession", ip="1.2.3.4")


def _walk_to(run_id, target):
    """Step toward a coordinate, one axis at a time."""
    for _ in range(60):
        run = RunContext.load(run_id).run
        if run["status"] != "alive":
            return
        row = repo.get_area_by_id(run["area_id"])
        if (row["x"], row["y"]) == target:
            return
        dx, dy = target[0] - row["x"], target[1] - row["y"]
        d = ("e" if dx > 0 else "w") if dx else ("n" if dy > 0 else "s")
        _act(run_id, {"n": "north", "s": "south", "e": "east", "w": "west"}[d])


def test_full_loop(game, session_id):
    run_id = service.create_run(session_id, "Doug", "a washed-up plumber with anger issues")
    ctx = RunContext.load(run_id)
    assert ctx.run["status"] == "alive"
    assert ctx.run["hp"] == ctx.run["max_hp"]
    assert ctx.inventory(), "starting kit should exist"

    ctx = _act(run_id, "look")
    assert any("—" in t for _, t in ctx.events)

    # the landing is enemy-free by design; step off it to find something to fight
    d = RunContext.load(run_id).area().exits.open_dirs()[0]
    _act(run_id, {"n": "north", "s": "south", "e": "east", "w": "west"}[d])

    for _ in range(40):
        _act(run_id, "attack goblin")
        run = RunContext.load(run_id).run
        if run["status"] != "alive" or run["kills"] >= 1:
            break
    run = RunContext.load(run_id).run
    assert run["kills"] >= 1 or run["status"] == "dead"

    if run["status"] == "alive":
        ctx = _act(run_id, "inventory")
        assert any("Gold" in t for _, t in ctx.events)


def test_entering_a_room_never_provokes_enemies(game, session_id):
    """A room is a prompt (design review #5) — arriving is never lethal on its own."""
    run_id = service.create_run(session_id, "Cautious", "a careful surveyor")
    before = RunContext.load(run_id).run["hp"]
    d = RunContext.load(run_id).area().exits.open_dirs()[0]
    ctx = _act(run_id, {"n": "north", "s": "south", "e": "east", "w": "west"}[d])

    after = RunContext.load(run_id)
    assert after.run["hp"] == before, "arriving must not cost HP"
    assert any(e.kind == "enemy" for e in after.visible_entities()), "fixture room has an enemy"
    assert not any("hits you" in t for _, t in ctx.events)


def test_attacking_provokes_the_response(game, session_id):
    run_id = service.create_run(session_id, "Bold", "an impatient brawler")
    d = RunContext.load(run_id).area().exits.open_dirs()[0]
    _act(run_id, {"n": "north", "s": "south", "e": "east", "w": "west"}[d])
    ctx = _act(run_id, "attack goblin")
    kinds = [k for k, _ in ctx.events]
    assert "combat" in kinds  # the enemy answered


def test_safe_room_heals_for_free(game, session_id):
    brief = floors.get_brief(1)
    cell = next(((x, y) for x in range(-12, 13) for y in range(-12, 13)
                 if floors.is_safe_room(brief, x, y)), None)
    assert cell, "expected a safe room somewhere nearby"

    run_id = service.create_run(session_id, "Weary", "a tired courier")
    from app.gen import services as gen
    row = gen.ensure_area(1, *cell)
    with db.tx() as conn:
        conn.execute("UPDATE runs SET hp=1, area_id=? WHERE id=?", (row["id"], run_id))

    ctx = RunContext.load(run_id)
    from app.engine import movement
    movement.describe_area(ctx)
    ctx.persist()

    run = RunContext.load(run_id).run
    assert run["hp"] == run["max_hp"], "safe rooms restore fully on entry"
    if run["stats"].get("resource"):
        assert run["stats"]["resource"]["current"] == run["stats"]["resource"]["max"]


def test_safe_room_has_no_enemies(game, session_id):
    brief = floors.get_brief(1)
    cell = next(((x, y) for x in range(-12, 13) for y in range(-12, 13)
                 if floors.is_safe_room(brief, x, y)), None)
    from app.gen import services as gen
    row = gen.ensure_area(1, *cell)
    content = repo.area_content(row)
    assert not [e for e in content.entities if e.kind == "enemy"]


def test_buy_and_sell_in_a_safe_room(game, session_id):
    brief = floors.get_brief(1)
    cell = next(((x, y) for x in range(-12, 13) for y in range(-12, 13)
                 if floors.is_safe_room(brief, x, y)), None)
    run_id = service.create_run(session_id, "Shopper", "a compulsive haggler")
    from app.gen import services as gen
    row = gen.ensure_area(1, *cell)
    with db.tx() as conn:
        conn.execute("UPDATE runs SET area_id=?, gold=500 WHERE id=?", (row["id"], run_id))

    _act(run_id, "look")
    stock = repo.safe_room_stock(row["id"])
    assert stock and all(s["item_id"] is None for s in stock)  # JIT stock

    ctx = _act(run_id, "buy 1")
    assert repo.safe_room_stock(row["id"])[0]["item_id"] is not None  # revealed on purchase
    assert RunContext.load(run_id).run["gold"] < 500

    inv = [e for e in RunContext.load(run_id).inventory() if not e["equipped"]]
    assert inv
    ctx = _act(run_id, f"sell {inv[0]['item'].name}")
    assert any("sell" in t.lower() for _, t in ctx.events)


def test_second_crawler_gets_identical_world(game, session_id):
    run1 = service.create_run(session_id, "Alpha", "a librarian of violence")
    calls = len(game.calls)
    with db.tx() as conn:
        conn.execute("INSERT INTO sessions (id) VALUES ('s2')")
    run2 = service.create_run("s2", "Beta", "a librarian of violence")
    r1, r2 = RunContext.load(run1).run, RunContext.load(run2).run
    assert r1["class_id"] == r2["class_id"]
    assert r1["area_id"] == r2["area_id"]
    assert not [c for c in game.calls[calls:] if c[0] in ("class", "area")]


def test_death_echo_visible_to_next_crawler(game, session_id):
    run1 = service.create_run(session_id, "Victim", "doomed accountant")
    with db.tx() as conn:
        conn.execute(
            "UPDATE runs SET status='dead', death_area_id=area_id, death_cause='testing' WHERE id=?",
            (run1,),
        )
        conn.execute("INSERT INTO sessions (id) VALUES ('s3')")
    run2 = service.create_run("s3", "Witness", "doomed accountant")
    events = service.recent_events(run2)
    assert any("Victim" in e["text"] and "testing" in e["text"] for e in events)


def test_the_stairs_always_lead_somewhere(game, session_id):
    """There is no bottom and no way to finish. The floor below is written on the first
    descent into it, so taking the stairs works whether or not anyone has been there."""
    run_id = service.create_run(session_id, "Speedy", "a stair enthusiast")
    brief = floors.get_brief(1)
    cell = (brief.stairs.forced_distance, 0)
    from app.gen import services as gen
    row = gen.ensure_area(1, *cell)
    assert row["has_stairs_down"]
    with db.tx() as conn:
        conn.execute("UPDATE runs SET area_id=? WHERE id=?", (row["id"], run_id))

    _act(run_id, "descend")
    run = RunContext.load(run_id).run
    assert run["status"] == "alive", "descending must not end a run"
    assert run["floor_id"] == 2


def test_a_floor_is_written_once_and_is_the_same_for_everyone(game, session_id):
    """Floor 2 is generated by the first crawler to reach it and cached like the rest of
    the world, so every crawler descends into the same place."""
    from app.gen import services as gen

    first = floors.get_brief(2)
    written = len([c for c in game.calls if c[0] == "floor_plan"])
    assert written == 1

    floors._briefs.pop(2)                      # a second crawler, nothing in memory
    second = floors.get_brief(2)
    assert len([c for c in game.calls if c[0] == "floor_plan"]) == 1
    assert (second.title, second.body_md) == (first.title, first.body_md)


def test_floor_difficulty_comes_from_its_depth(game, session_id):
    """The AI writes the place; code decides what it is pitched at."""
    from app.models import scale

    for depth in (1, 2, 3):
        assert floors.get_brief(depth).levels.min == scale.floor_levels(depth)[0]


def test_leaderboard_orders_by_floor_then_rooms(game, session_id):
    run_id = service.create_run(session_id, "Board", "a record keeper")
    with db.tx() as conn:
        conn.execute("UPDATE runs SET status='dead', death_cause='testing' WHERE id=?", (run_id,))
    board = service.leaderboard()
    assert board and board[0]["name"] == "Board"
    assert "rooms" in board[0] and board[0]["rooms"] >= 1
