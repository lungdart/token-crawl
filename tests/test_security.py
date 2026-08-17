"""Rate limiting: stop bad actors, never inconvenience a player."""
import threading
import time

import pytest

from app.security import limits


@pytest.fixture(autouse=True)
def clean_limits():
    limits.reset()
    limits.end_action()
    yield
    limits.reset()
    limits.end_action()


def test_normal_play_is_never_limited():
    """A person acting at human speed must never hit either limit."""
    for _ in range(20):
        limits.check_action("player")
        time.sleep(0.11)  # ~9 actions/sec, faster than anyone really types


def test_slamming_is_refused():
    """No wait, no mercy — nothing legitimate reaches this."""
    with pytest.raises(limits.RateLimited):
        for _ in range(200):
            limits.check_action("bot")


def test_limits_are_per_session():
    for _ in range(200):
        try:
            limits.check_action("bot")
        except limits.RateLimited:
            break
    limits.check_action("someone-else")  # unaffected by the other session


def test_one_action_is_one_trigger_however_many_calls():
    """Walking into an unexplored room may make one model call or fifty. It counts once."""
    limits.begin_action("s")
    assert limits.charge_generation() == 0.0        # first call in this action
    for _ in range(50):
        assert limits.charge_generation() == 0.0    # the rest are free
    limits.end_action()


def test_second_generating_action_waits_rather_than_failing():
    """When the generation limit is hit the player waits. There is no rejection path."""
    limits.begin_action("s")
    limits.charge_generation()
    limits.end_action()

    limits.begin_action("s")
    start = time.monotonic()
    waited = limits.charge_generation()   # must not raise
    elapsed = time.monotonic() - start
    limits.end_action()

    assert waited > 0, "should have waited for a slot"
    assert 0.5 < elapsed < 1.5, f"expected roughly a one-second wait, got {elapsed:.2f}s"


def test_generation_outside_an_action_is_not_charged():
    """Warming content at startup, or tooling, is not a player action."""
    limits.end_action()
    for _ in range(10):
        assert limits.charge_generation() == 0.0


def test_generation_slots_are_per_session():
    limits.begin_action("a")
    limits.charge_generation()
    limits.end_action()

    limits.begin_action("b")
    start = time.monotonic()
    limits.charge_generation()
    limits.end_action()
    assert time.monotonic() - start < 0.2, "one session must not delay another"


def test_action_state_does_not_leak_between_threads():
    """Each request runs on its own thread; one player's action must not mark another's."""
    seen = {}

    def worker():
        seen["charged"] = limits.charge_generation()

    limits.begin_action("main")
    limits.charge_generation()
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    limits.end_action()
    assert seen["charged"] == 0.0  # that thread had no action of its own
