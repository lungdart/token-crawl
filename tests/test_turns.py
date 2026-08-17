"""One crawler takes one turn at a time; different crawlers never wait on each other."""
import time
from concurrent.futures import ThreadPoolExecutor

from app import db
from app.engine.state import RunContext, one_turn_at_a_time
from app.runs import service


def _gold(run_id: int) -> int:
    return db.get().execute("SELECT gold FROM runs WHERE id=?", (run_id,)).fetchone()["gold"]


def _spend_ten(run_id: int) -> None:
    """A turn in miniature: read the run, change it, write it back."""
    with one_turn_at_a_time(run_id):
        ctx = RunContext.load(run_id)
        ctx.run["gold"] -= 10
        time.sleep(0.01)  # widen the window the lost writes lived in
        ctx.persist()


def test_overlapping_turns_do_not_erase_each_other(game, session_id):
    """The bug: eight simultaneous 10-gold spends against 100 gold left 90, not 20 — seven
    of the eight writes were overwritten, so the crawler kept the goods and the money."""
    run_id = service.create_run(session_id, "Doug", "a plumber")
    with db.tx() as conn:
        conn.execute("UPDATE runs SET gold=100 WHERE id=?", (run_id,))

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda _: _spend_ten(run_id), range(8)))

    assert _gold(run_id) == 20


def test_a_crawler_never_waits_on_another_crawler(game, session_id):
    """Turns are serial per crawler and parallel across them. If this ever serialises
    everyone, one slow generation freezes the whole game."""
    with db.tx() as conn:
        conn.execute("INSERT INTO sessions (id) VALUES ('othersession')")
    other = service.create_run("othersession", "Betty", "a locksmith")
    run_id = service.create_run(session_id, "Doug", "a plumber")

    def slow_turn(rid):
        with one_turn_at_a_time(rid):
            time.sleep(0.3)

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(slow_turn, [run_id, other]))
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"two crawlers took {elapsed:.2f}s — they blocked each other"


def test_the_same_crawler_does_queue(game, session_id):
    run_id = service.create_run(session_id, "Doug", "a plumber")

    def slow_turn(_):
        with one_turn_at_a_time(run_id):
            time.sleep(0.2)

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(slow_turn, range(2)))
    elapsed = time.monotonic() - start

    assert elapsed >= 0.4, f"two turns on one crawler overlapped ({elapsed:.2f}s)"
