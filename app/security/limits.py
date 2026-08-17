"""Rate limiting.

Two limits, both per session, both aimed at bad actors rather than at players:

  * every action is capped at 10/sec. No human types that fast; a script does.
    Exceeding it is refused.
  * any action that triggers generation is capped at 1/sec. The unit is the ACTION,
    not the model calls underneath it — walking into an unexplored room may cause one
    call or fifty, and it counts once either way. Actions that trigger nothing are not
    counted here at all, so ordinary play through explored ground is never limited.

When the generation limit is hit the caller waits. There is no rejection path: a person
can only reach it by double-clicking into unexplored ground, and a brief pause is
invisible where a refusal would read as the game breaking.

This module contains no player-facing text. Everything the player reads comes from the
floor's generated lines, like all other content in the game.
"""
from __future__ import annotations

import threading
import time

ACTIONS_PER_SECOND = 10.0
GENERATIONS_PER_SECOND = 1.0


class RateLimited(Exception):
    """Too many actions too fast. Carries no text; the caller decides what is shown."""


class _Bucket:
    """`capacity` allowances, refilling at `rate` per second."""

    def __init__(self, capacity: float, rate: float):
        self.capacity = capacity
        self.rate = rate
        self.state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last seen)
        self.lock = threading.Lock()

    def take(self, key: str) -> float:
        """Spend one allowance. Returns 0.0 if one was available, otherwise the seconds
        until one will be."""
        now = time.monotonic()
        with self.lock:
            tokens, last = self.state.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self.state[key] = (tokens, now)
                return (1.0 - tokens) / self.rate
            self.state[key] = (tokens - 1.0, now)
            if len(self.state) > 10_000:  # bound memory; drop keys idle for an hour
                cutoff = now - 3600
                self.state = {k: v for k, v in self.state.items() if v[1] > cutoff}
            return 0.0


_actions = _Bucket(capacity=ACTIONS_PER_SECOND, rate=ACTIONS_PER_SECOND)
_generations = _Bucket(capacity=1.0, rate=GENERATIONS_PER_SECOND)


def check_action(session_id: str) -> None:
    """Anti-slam, on every action. Refuses rather than waits — nothing a person does
    reaches this."""
    if _actions.take(f"a:{session_id}") > 0:
        raise RateLimited("action rate")


def await_generation_slot(session_id: str) -> float:
    """Call before an action that may generate. Waits until a slot is free and returns
    the seconds waited.

    One action is one trigger regardless of how many model calls it makes underneath.
    """
    waited = 0.0
    while True:
        delay = _generations.take(f"g:{session_id}")
        if delay <= 0:
            return waited
        time.sleep(delay)
        waited += delay


def reset() -> None:
    """Clear all counters. For tests."""
    for bucket in (_actions, _generations):
        with bucket.lock:
            bucket.state.clear()


# --- one action = one trigger -------------------------------------------------
#
# Which actions generate cannot be predicted from the action alone: walking into an
# explored room generates nothing, walking into an unexplored one generates several
# things, and even an attack generates on the first kill of a creature type. So the
# slot is charged at the moment generation first happens within an action, and only
# once per action however many calls follow.

_current = threading.local()


def begin_action(session_id: str) -> None:
    """Start of one player action."""
    _current.session = session_id
    _current.charged = False


def end_action() -> None:
    _current.session = None
    _current.charged = False


def charge_generation() -> float:
    """Called immediately before a model call. Waits for this action's generation slot,
    the first time only. Returns the seconds waited."""
    if getattr(_current, "charged", False):
        return 0.0
    session_id = getattr(_current, "session", None)
    if session_id is None:
        return 0.0  # not inside a player action (startup warming, tooling, tests)
    _current.charged = True
    return await_generation_slot(session_id)
