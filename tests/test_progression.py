"""XP awards big enough for several levels have to grant all of them."""
from app import db
from app.engine import progression
from app.engine.state import RunContext
from app.gen import services
from app.models.character import xp_to_reach
from app.runs import service


def test_one_xp_award_grants_every_level_it_pays_for(game, session_id):
    """level_up used to return None on its success path, which grant_xp read as failure —
    so the loop that exists to grant several levels at once always stopped after one, and
    the crawler stayed a level behind until the next award."""
    run_id = service.create_run(session_id, "Climber", "a fast learner")
    with db.tx() as conn:
        conn.execute("UPDATE runs SET xp=0, level=1 WHERE id=?", (run_id,))

    ctx = RunContext.load(run_id)
    before_hp = ctx.run["max_hp"]
    before_attack = ctx.run["stats"]["attack"]

    progression.grant_xp(ctx, xp_to_reach(3))

    assert ctx.run["level"] == 3, "one award worth two levels must grant both"

    # the stats have to follow the level, not just the number on the sheet. Both level-ups
    # are cached by now, so asking for them again is the same numbers that were granted.
    gains = [services.ensure_level_up(ctx.run["class_id"], lvl, ctx.run["floor_id"])
             for lvl in (2, 3)]
    assert ctx.run["max_hp"] == before_hp + sum(g.max_hp for g in gains)
    assert ctx.run["stats"]["attack"] == before_attack + sum(g.attack for g in gains)
    granted = [t for _, t in ctx.events if t.startswith("Level ")]
    assert len(granted) == 2, f"both level-ups must be announced, got {granted}"
