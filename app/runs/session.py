"""Anonymous sessions.

HTTP is stateless, so the server can't tell one browser from another. Each browser gets a
random id once; it sends that back on every request, and a `sessions` row maps it to the
active run.

Two steps, in this order: work out the id (no response needed), then put the cookie on the
response you're actually returning.
"""
import uuid

from fastapi import Request, Response

from app import db

COOKIE = "tc_session"
MAX_AGE = 60 * 60 * 24 * 365


def session_id(request: Request) -> str:
    """The caller's session id, creating one if the browser doesn't have a valid one yet."""
    sid = request.cookies.get(COOKIE)
    if sid and db.get().execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
        return sid
    sid = uuid.uuid4().hex
    with db.tx() as conn:
        conn.execute("INSERT INTO sessions (id) VALUES (?)", (sid,))
    return sid


def remember(response: Response, sid: str) -> Response:
    """Attach the session cookie to the response being returned."""
    response.set_cookie(COOKIE, sid, max_age=MAX_AGE, httponly=True, samesite="lax")
    return response


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
