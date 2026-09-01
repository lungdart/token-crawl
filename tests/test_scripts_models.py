"""scripts/models.py has to still fit the app it calls into.

Nothing imports scripts/, so the calls it makes into app/gen drift silently: `compare`
raised on the first prompt builder, before it reached the network, and the only way to
find that out was to run it. This drives the whole subcommand on the fixture backend —
no API key, no network, no cost — so signature drift shows up as a red test instead.
"""
import pytest

from scripts import models


@pytest.mark.parametrize("kind", ["area", "enemy"])
def test_compare_runs_against_the_current_app_api(game, capsys, kind):
    models.compare(["google/gemini-2.5-flash"], kind)

    out = capsys.readouterr().out
    assert "FAILED" not in out, f"compare() could not generate a {kind}:\n{out}"
    assert "1 call(s), 0 rejected" in out  # the totals row: one call, nothing thrown away
    if kind == "area":
        # Rooms have no name; the coordinate is how one is identified now.
        assert "3, 3" in out
        assert "Rough stone" in out
    else:
        assert "Tunnel Goblin" in out
