"""Failures must be logged in full and never disguised as game events."""
import logging

import pytest

from app import db, logs
from app.engine.state import RunContext
from app.gen import llm
from app.runs import service


def test_level_is_not_banked_when_its_rewards_cannot_be_generated(game, session_id, caplog):
    """The level number used to be written before generation was attempted, so a failure
    kept the level and lost its stats permanently — the crawler was already that level, so
    it never retried."""
    run_id = service.create_run(session_id, "Leveler", "a persistent sort")
    with db.tx() as conn:
        conn.execute("UPDATE runs SET xp=0, level=1 WHERE id=?", (run_id,))

    class Broken:
        def generate(self, **kw):
            raise llm.GenerationError("upstream is down")

    ctx = RunContext.load(run_id)
    before = dict(ctx.run["stats"])
    llm.set_backend(Broken())
    from app.engine import progression
    with caplog.at_level(logging.ERROR):
        progression.grant_xp(ctx, 999)

    assert ctx.run["level"] == 1, "a level must not be banked without its rewards"
    assert ctx.run["stats"] == before, "no stats should be granted"
    assert any("generation failed" in r.message for r in caplog.records), "must be logged"
    assert any(logs.FAULT in t for _, t in ctx.events), "player must be told it was a fault"
    assert not any("You reach level" in t for _, t in ctx.events), "must not imply success"


def test_a_broken_response_bank_does_not_lie_about_what_happened(game, session_id, caplog):
    """A miss used to render as 'Nothing happens.' when the floor's lines were unavailable —
    a fault describing itself as a game outcome."""
    run_id = service.create_run(session_id, "Misser", "a swinger of blades")
    ctx = RunContext.load(run_id)

    class Broken:
        def generate(self, **kw):
            raise llm.GenerationError("upstream is down")

    llm.set_backend(Broken())
    with caplog.at_level(logging.ERROR):
        line = ctx.line("player_miss")

    assert line == "You miss.", "must state what actually happened"
    assert "Nothing happens" not in line
    assert any("response bank unavailable" in r.message for r in caplog.records)


def test_internal_errors_are_never_shown_to_the_player(client, game, monkeypatch, caplog):
    """The failure page used to print the Python exception type and message, which would
    include upstream provider payloads."""
    def boom(*a, **k):
        raise RuntimeError("secret-internal-detail-12345")

    monkeypatch.setattr("app.web.routes.service.create_run", boom)
    with caplog.at_level(logging.ERROR):
        r = client.post("/crawler", data={"name": "X", "concept": "a test"})

    assert r.status_code == 200
    assert "secret-internal-detail-12345" not in r.text
    assert "RuntimeError" not in r.text
    # the real error is logged in full, with its traceback
    assert any("failed to create a run" in rec.message for rec in caplog.records)
    assert any(rec.exc_info and "secret-internal-detail-12345" in str(rec.exc_info[1])
               for rec in caplog.records)
