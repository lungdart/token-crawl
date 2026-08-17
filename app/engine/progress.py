"""Telling a crawler why they are waiting.

Most turns are instant: the room is already written and nothing costs a model call. Some
turns are the first time anyone has done that thing anywhere, and those take seconds while
the dungeon is written. From the player's side the two are indistinguishable until one of
them doesn't come back, which reads as the game hanging.

The server knows exactly which is which — it checks the cache before it spends anything —
but a single POST cannot say so halfway through answering. So the page holds one
server-sent event stream open, and generation pushes a line down it the moment it starts.

This is interface text, not game content: it says what the machine is doing and never
pretends to be the dungeon talking.
"""
from __future__ import annotations

import queue
import threading

# What each kind of generation is called when a player is waiting on it.
WORDING = {
    "area": "Nobody has been here before. The dungeon is deciding what this room is…",
    "room_art": "Drawing the room…",
    "enemy": "Nothing has fought this before. Working out what it is…",
    "drop_table": "Working out what it was carrying…",
    "item": "Making something that has never existed…",
    "safe_room": "Finding out who keeps this place…",
    "class": "Making your crawler…",
    "level_up": "Working out what you become…",
    "adjudication": "Nobody has tried this here before. Ruling on it…",
    "floor_plan": "Writing the floor below…",
    "response_bank": "Finding this floor's voice…",
}
FALLBACK = "Writing something that has never existed…"

_lock = threading.Lock()
_channels: dict[str, list[queue.Queue]] = {}


def subscribe(session_id: str) -> queue.Queue:
    q: queue.Queue = queue.Queue()
    with _lock:
        _channels.setdefault(session_id, []).append(q)
    return q


def unsubscribe(session_id: str, q: queue.Queue) -> None:
    with _lock:
        listeners = _channels.get(session_id)
        if not listeners:
            return
        if q in listeners:
            listeners.remove(q)
        if not listeners:
            _channels.pop(session_id, None)


def emit(session_id: str | None, kind: str) -> None:
    """Called as generation starts. Never raises: a status line is not worth a turn."""
    if not session_id:
        return
    with _lock:
        listeners = list(_channels.get(session_id, ()))
    for q in listeners:
        try:
            q.put_nowait(WORDING.get(kind, FALLBACK))
        except Exception:  # pragma: no cover — an unbounded queue does not fill
            pass


def listeners(session_id: str) -> int:
    with _lock:
        return len(_channels.get(session_id, ()))
