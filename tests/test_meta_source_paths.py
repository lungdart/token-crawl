"""Comments that point at a file have to point at a file that exists.

Prose in a comment is never executed, so a path in one rots silently: the file gets
renamed and the comment keeps sending the next reader somewhere that isn't there.
This walks every repo-relative path token in `app/` and `scripts/` and resolves it.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PATH_TOKEN = re.compile(r"(?:app|scripts|tests|floors)/[\w./*-]+")

# app/gen/llm.py names the FixtureBackend lookup directory, which is created on
# demand and is legitimately absent from a clean checkout — the backend falls back
# to generic per-kind templates when it is missing.
RUNTIME_PATHS = {"tests/fixtures", "tests/fixtures/"}


def _resolves(tok: str) -> bool:
    if "*" in tok:
        return bool(list(REPO_ROOT.glob(tok)))
    target = REPO_ROOT / tok
    return target.is_dir() or target.is_file()


def test_source_comments_reference_real_files():
    missing = []
    for top in ("app", "scripts"):
        for path in sorted((REPO_ROOT / top).rglob("*.py")):
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                for tok in PATH_TOKEN.findall(line):
                    tok = tok.rstrip(".,;:)`'\"")
                    if tok in RUNTIME_PATHS or _resolves(tok):
                        continue
                    missing.append(f"{path.relative_to(REPO_ROOT)}:{lineno} -> {tok}")

    assert not missing, "source comments point at files that do not exist:\n" + "\n".join(missing)
