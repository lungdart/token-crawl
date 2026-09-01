"""There is one `client` fixture, and it lives in conftest.

A second one with the same name in a test module wins over the shared one for every
test in that module, silently. That is how ten web tests came to run against a copy
of the fixture while only one test used the real thing — and the copy had already
started to drift from it. Wiring the test app is one job in one place.
"""
import re
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

CLIENT_FIXTURE = re.compile(r"^def client\(")


def test_only_conftest_defines_the_client_fixture():
    defined = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if CLIENT_FIXTURE.match(line):
                defined.append(f"{path.relative_to(TESTS_DIR.parent)}:{lineno}")

    assert len(defined) == 1 and defined[0].startswith("tests/conftest.py:"), (
        "the `client` fixture must be defined once, in tests/conftest.py; found it at:\n"
        + "\n".join(defined)
    )
