import pytest

from app import db
from app.config import settings
from app.gen import llm, locks
from app.security import limits
from app.world import floors


@pytest.fixture()
def game(tmp_path):
    """Fresh file DB + fixture backend + loaded floor 1. Zero API calls."""
    db.init(str(tmp_path / "test.sqlite3"))
    locks._locks.clear()
    backend = llm.FixtureBackend(fixtures_dir=tmp_path / "no_fixtures")
    llm.set_backend(backend)
    floors.load_floors("floors")
    limits.reset()       # fresh rate limits per test
    limits.end_action()
    yield backend
    limits.reset()
    limits.end_action()
    llm.set_backend(None)


@pytest.fixture()
def session_id(game):
    with db.tx() as conn:
        conn.execute("INSERT INTO sessions (id) VALUES ('testsession')")
    return "testsession"


@pytest.fixture()
def client(game):
    """App wired to the test database, without the startup hook re-initialising it."""
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.testclient import TestClient

    from app.web.routes import router

    app = FastAPI()
    app.include_router(router)
    app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
    return TestClient(app)
