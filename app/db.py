import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from app.config import settings

_SCHEMA = Path(__file__).parent / "schema.sql"

_local = threading.local()
_db_path: str | None = None
_init_lock = threading.Lock()
_schema_ready = False


def init(db_path: str | None = None) -> None:
    """Set the database path and create the schema. Call once at startup (or per test)."""
    global _db_path, _schema_ready
    _db_path = db_path or settings.db_path
    _local.__dict__.clear()
    with _init_lock:
        conn = _open()
        conn.executescript(_SCHEMA.read_text())
        _add_missing_columns(conn)
        conn.commit()
        _schema_ready = True


# CREATE TABLE IF NOT EXISTS will not add a column to a table that already exists, so a
# database made by an older build keeps its old shape. Adding a nullable column here lets
# an existing world carry forward instead of having to be thrown away.
_ADDED_COLUMNS = (
    ("floors", "status", "TEXT NOT NULL DEFAULT 'ready'"),
    ("floors", "claimed_at", "TEXT"),
)


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if have and column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _open() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(_db_path or settings.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        _local.conn = conn
    return conn


def get() -> sqlite3.Connection:
    """Thread-local connection. WAL allows concurrent readers + one writer."""
    if not _schema_ready:
        init()
    return _open()


@contextmanager
def tx():
    """Write transaction. BEGIN IMMEDIATE serializes writers; keep these short."""
    conn = get()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
