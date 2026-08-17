"""get_or_generate: the claim/wait/finalize protocol that makes lazy shared-world
generation safe. First-write-wins via UNIQUE constraints; in-process threading
locks make the common single-process case cheap; stale claims (>60s) are
re-claimable so a crash mid-generation never wedges a cell forever."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from app import db
from app.engine import progress
from app.security import limits

log = logging.getLogger(__name__)

_locks: dict[tuple, threading.Lock] = {}
_locks_guard = threading.Lock()

STALE_SECONDS = 60
WAIT_TIMEOUT = 30.0
POLL_INTERVAL = 0.25


class GenerationPending(Exception):
    """Another request is generating this key and we timed out waiting."""


def _key_lock(key: tuple) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def get_or_generate(
    *,
    kind: str,
    select_ready: Callable[[], object | None],
    claim: Callable[[], bool],
    reclaim_stale: Callable[[int], bool],
    generate_and_store: Callable[[], object],
    mark_failed: Callable[[], None],
    key: tuple,
):
    """Generic cache-or-generate. The callables own table specifics:

    select_ready()        -> cached object if status='ready', else None
    claim()               -> True if we inserted a fresh 'generating' claim row
    reclaim_stale(secs)   -> True if we took over a stale/failed claim
    generate_and_store()  -> run the LLM, validate, UPDATE row to 'ready'; returns object
    mark_failed()         -> flip our claim to 'failed' (re-claimable)
    """
    cached = select_ready()
    if cached is not None:
        return cached

    with _key_lock((kind, *key)):
        cached = select_ready()
        if cached is not None:
            return cached
        owns = claim() or reclaim_stale(STALE_SECONDS)
        if owns:
            # The cache has been checked and missed and we hold the claim: this player is
            # about to wait on a model. Tell them why, before it starts.
            progress.emit(limits.current_session(), kind)
            try:
                return generate_and_store()
            except Exception:
                log.exception("generation failed for %s %s; claim released for retry", kind, key)
                mark_failed()
                raise

    # Another process/thread holds a fresh claim: poll for it to finish.
    deadline = time.monotonic() + WAIT_TIMEOUT
    while time.monotonic() < deadline:
        cached = select_ready()
        if cached is not None:
            return cached
        time.sleep(POLL_INTERVAL)
    raise GenerationPending(f"{kind}:{key} still generating elsewhere")


def claim_row(conn, table: str, where: str, params: tuple) -> bool:
    """Reclaim helper: take over a row whose claim is stale or failed."""
    cur = conn.execute(
        f"""UPDATE {table} SET status='generating', claimed_at=datetime('now')
            WHERE {where} AND (status='failed'
                  OR (status='generating' AND claimed_at < datetime('now', '-60 seconds')))""",
        params,
    )
    return cur.rowcount > 0
