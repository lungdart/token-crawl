"""The live tests have to survive contact with the models they read.

Nothing runs them: addopts is `-m 'not live'`, and test_meta_test_count only *collects*
them. So a field that was deleted from a model stays readable in tests/test_live.py for
as long as nobody spends the API credit to find out. Here the two bodies that touch area
content are called against the fixture backend on a temp DB — same `game` fixture the
playthrough uses, zero API calls — so an attribute the model does not have fails offline.
"""
from tests import test_live


def test_landing_body_runs_against_the_fixture_world(game):
    test_live.test_live_landing_generation_and_cache(game)


def test_enemy_body_runs_against_the_fixture_world(game):
    from app.gen import services
    from app.world import repo

    content = repo.area_content(services.ensure_area(1, 1, 0))
    assert [e for e in content.entities if e.kind == "enemy"], (
        "the fixture room at (1,0) must hold an enemy, or the live body skips instead of "
        "running and this proves nothing"
    )
    test_live.test_live_enemy_belongs_to_its_room(game)
