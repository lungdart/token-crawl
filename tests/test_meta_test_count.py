"""The test counts in DESIGN-REVIEW.md have to be the counts pytest actually reports.

Deliberately exact: adding a test means updating that sentence. That is the point —
the count is what drifted, and a range would drift again quietly. Collection only, so
this does not recurse and does not need an API key; it says nothing about whether the
live tests pass.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _collected(*args: str) -> int:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout
    found = re.search(r"(\d+)(?:/\d+)? tests? collected", out)
    assert found, f"pytest --collect-only printed no count we recognise:\n{out}"
    return int(found.group(1))


def test_design_review_states_the_real_test_counts():
    offline = _collected()
    live = _collected("-m", "live")  # a later -m overrides the one in addopts

    doc = (REPO_ROOT / "DESIGN-REVIEW.md").read_text()
    said = re.search(r"(\d+) offline tests \+ (\d+) live tests", doc)
    assert said, "DESIGN-REVIEW.md never says how many offline and live tests there are"

    assert (int(said.group(1)), int(said.group(2))) == (offline, live), (
        f"DESIGN-REVIEW.md says {said.group(0)!r}; pytest collects {offline} offline and "
        f"{live} live. Update that line to read "
        f"'{offline} offline tests + {live} live tests pass.'"
    )
