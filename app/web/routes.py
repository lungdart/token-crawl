from __future__ import annotations

import asyncio
import queue
from collections.abc import AsyncIterable
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.templating import Jinja2Templates

from app import db, logs
from app.config import settings
from app.engine import combat, movement, progress, resolver
from app.engine.state import RunContext
from app.gen.art import to_png
from app.runs import service
from app.runs import session as websession
from app.security import limits
from app.web import svgrender
from app.world import repo

log = logs.get(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


_DIRWORD = {"n": "north", "s": "south", "e": "east", "w": "west"}


def _game_context(request: Request, run_id: int) -> dict:
    ctx = RunContext.load(run_id)
    inv = ctx.inventory()
    cls = repo.get_class(ctx.run["class_id"])
    area_row = ctx.area_row()

    shop = None
    if area_row["is_safe_room"]:
        found = repo.ready_safe_room(area_row["id"])
        if found:
            stock = []
            for r in repo.safe_room_stock(area_row["id"]):
                item = repo.get_item(r["item_id"]) if r["item_id"] else None
                stock.append({"price": r["price"], "rarity": r["rarity"],
                              "name": item.name if item else "???", "hint": r["hint"]})
            shop = {"front": found[0], "stock": stock}

    order = {"rare": 0, "uncommon": 1, "common": 2, "junk": 3}
    inv.sort(key=lambda e: (not e["equipped"], order.get(e["item"].rarity, 9), e["item"].name))

    # The room's description and what is in it come from the world cache, not from the
    # log: they describe where the crawler IS, and stay put while the log scrolls.
    content = ctx.area()
    highlights = [{"kind": e.kind, "name": e.name, "brief": e.brief}
                  for e in ctx.visible_entities()]
    if area_row["has_stairs_down"]:
        highlights.append({"kind": "stairs", "name": "Stairs down",
                           "brief": "A way down to the next floor."})
    exits = [_DIRWORD[d] for d in content.exits.open_dirs()]

    return {
        "request": request,
        "run": ctx.run,
        "description": content.description,
        "highlights": highlights,
        "exits": exits,
        "coords": movement.coords(area_row),
        "area_id": area_row["id"],
        "has_art": repo.ready_art(area_row["id"]) is not None,
        "cls": cls,
        "abilities": combat.known_abilities(ctx),
        "inventory": inv,
        "events": service.recent_events(run_id),
        "minimap": svgrender.minimap_svg(ctx.run),
        "shop": shop,
        "resource": ctx.resource,
        "rooms": ctx.rooms_explored(),
        "rank": service.rank_of(run_id) if ctx.run["status"] != "alive" else None,
    }


def _active_run_id(sid: str) -> int | None:
    row = db.get().execute("SELECT active_run_id FROM sessions WHERE id=?", (sid,)).fetchone()
    return row["active_run_id"] if row else None


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    sid = websession.session_id(request)
    if service.active_run(sid):
        return websession.remember(RedirectResponse("/game", status_code=303), sid)
    page = templates.TemplateResponse(request, "index.html", {
        "max_concept": settings.max_concept_chars,
        "leaderboard": service.leaderboard(10),
        "error": request.query_params.get("error"),
    })
    return websession.remember(page, sid)


def _creation_failed(request: Request, sid: str, message: str):
    """Same page back with the reason on it. htmx asked without navigating, so tell it to
    reload rather than swapping a whole document into a corner of itself."""
    if request.headers.get("HX-Request"):
        return websession.remember(
            Response(status_code=204, headers={"HX-Redirect": f"/?error={quote(message)}"}), sid)
    page = templates.TemplateResponse(request, "index.html", {
        "max_concept": settings.max_concept_chars,
        "leaderboard": service.leaderboard(10),
        "error": message,
    })
    return websession.remember(page, sid)


@router.post("/crawler")
def create_crawler(request: Request, name: str = Form(...), concept: str = Form(...)):
    sid = websession.session_id(request)
    ip = websession.client_ip(request)
    try:
        limits.check_action(sid)
        limits.begin_action(sid)
        service.create_run(sid, name.strip()[:40], concept.strip()[: settings.max_concept_chars])
    except limits.RateLimited:
        return _creation_failed(request, sid, "Too many attempts. Wait a moment and try again.")
    except Exception:
        log.exception("failed to create a run for session %s", sid)
        return _creation_failed(request, sid, "Something broke while opening the dungeon. Try again.")
    # htmx follows a 303 with another XHR and would swap the game page into this one;
    # HX-Redirect tells it to navigate instead. A plain form post still gets the 303.
    if request.headers.get("HX-Request"):
        done = Response(status_code=204, headers={"HX-Redirect": "/game"})
    else:
        done = RedirectResponse("/game", status_code=303)
    return websession.remember(done, sid)


@router.get("/game", response_class=HTMLResponse)
def game(request: Request):
    sid = websession.session_id(request)
    run_id = _active_run_id(sid)
    if not run_id:
        return websession.remember(RedirectResponse("/", status_code=303), sid)
    page = templates.TemplateResponse(request, "game.html", _game_context(request, run_id))
    return websession.remember(page, sid)


@router.post("/action", response_class=HTMLResponse)
def action(request: Request, command: str = Form("")):
    sid = websession.session_id(request)
    ip = websession.client_ip(request)
    run_id = _active_run_id(sid)
    if not run_id:
        return HTMLResponse(
            "<div class='log-entry system'>No active crawl. <a href='/'>Roll a crawler</a>.</div>")
    ctx = resolver.handle_action(run_id, command, session_id=sid, ip=ip)
    gc = _game_context(request, run_id)
    gc["new_events"] = [{"kind": k, "text": t} for k, t in ctx.events] or [
        {"kind": "system", "text": "Nothing happens."}
    ]
    gc["echo_command"] = command
    page = templates.TemplateResponse(request, "partials/action_response.html", gc)
    return websession.remember(page, sid)


@router.get("/complete", response_class=HTMLResponse)
def complete(request: Request, command: str = ""):
    """Item-name suggestions for the `@` reference, rendered by the server.

    The names are written by a model and cached into the shared world, so they are
    somebody else's text arriving on your page. Rendering them here means Jinja escapes
    them like every other piece of text in the app, and nothing crosses to JavaScript as
    data. Empty when there is nothing to suggest, so the dropdown hides itself.
    """
    sid = request.cookies.get(websession.COOKIE, "")
    run_id = _active_run_id(sid) if sid else None
    if not run_id or "@" not in command:
        return HTMLResponse("")
    fragment = command.rsplit("@", 1)[1].strip().lower()
    names = [e["item"].name for e in RunContext.load(run_id).inventory()]
    hits = [n for n in names if fragment in n.lower()][:6]
    return templates.TemplateResponse(request, "partials/autocomplete.html", {"hits": hits})


@router.get("/art/{area_id}.png")
def room_art(area_id: int):
    """A room's picture, as a picture.

    World content: the same image for every crawler, and it never changes once drawn —
    so it is safe to tell browsers to keep it forever. A room that has not been drawn
    yet is not served at all; the page shows an unlit frame instead of asking for this.
    """
    art = repo.ready_art(area_id)
    if art is None:
        return Response(status_code=404)
    return Response(
        to_png(art), media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/events", response_class=EventSourceResponse)
async def events(request: Request) -> AsyncIterable[ServerSentEvent]:
    """One long-lived stream per player, carrying why they are waiting.

    Kept separate from /action on purpose: the action keeps returning one HTML response
    that htmx swaps as it always has, and this only carries status. Streaming the action
    itself would mean re-implementing htmx's out-of-band swapping by hand.
    """
    # Read the cookie, never mint one: this endpoint returns no cookie, so a session made
    # here would be a row nobody can ever reach again. Same for /complete, which the page
    # polls on every keystroke. Nothing to say to a caller without one.
    sid = request.cookies.get(websession.COOKIE, "")
    if not sid:
        return
    q = progress.subscribe(sid)
    try:
        while not await request.is_disconnected():
            drained = False
            while True:
                try:
                    # raw_data: plain text on the wire, not a JSON string the page must unwrap
                    yield ServerSentEvent(raw_data=q.get_nowait(), event="status")
                    drained = True
                except queue.Empty:
                    break
            if not drained:
                # A comment keeps an idle connection from being closed under us.
                yield ServerSentEvent(comment="waiting")
            await asyncio.sleep(0.2)
    finally:
        progress.unsubscribe(sid, q)


@router.get("/inventory", response_class=HTMLResponse)
def inventory_search(request: Request, q: str = ""):
    sid = request.cookies.get(websession.COOKIE, "")
    run_id = _active_run_id(sid) if sid else None
    if not run_id:
        return HTMLResponse("")
    gc = _game_context(request, run_id)
    if q.strip():
        ql = q.strip().lower()
        gc["inventory"] = [e for e in gc["inventory"]
                           if ql in e["item"].name.lower() or ql in e["item"].flavor.lower()]
    gc["inv_query"] = q
    # The panel, not just the list: the search box lives inside #inventory-panel and
    # targets it, so returning only the list would swap the input the player is typing
    # into out of the page.
    return templates.TemplateResponse(request, "partials/inventory_panel.html", gc)


@router.post("/reroll")
def reroll(request: Request):
    sid = websession.session_id(request)
    with db.tx() as conn:
        conn.execute("UPDATE sessions SET active_run_id=NULL WHERE id=?", (sid,))
    return websession.remember(RedirectResponse("/", status_code=303), sid)


@router.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(request: Request):
    return templates.TemplateResponse(request, "leaderboard.html", {
        "request": request, "leaderboard": service.leaderboard(50),
    })


@router.get("/health")
def health():
    return {"ok": True}
