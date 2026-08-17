from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import db, logs
from app.config import settings
from app.engine import combat, resolver
from app.engine.state import RunContext
from app.runs import service
from app.runs import session as websession
from app.security import limits
from app.web import svgrender
from app.world import repo

log = logs.get(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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
    return {
        "request": request,
        "run": ctx.run,
        "cls": cls,
        "abilities": combat.known_abilities(ctx),
        "inventory": inv,
        "events": service.recent_events(run_id),
        "minimap": svgrender.minimap_svg(ctx.run),
        "shop": shop,
        "resource": ctx.resource,
        "rooms": ctx.rooms_explored(),
        "item_names": [e["item"].name for e in inv],
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
        page = templates.TemplateResponse(request, "index.html", {
            "max_concept": settings.max_concept_chars,
            "leaderboard": service.leaderboard(10),
            "error": "Too many attempts. Wait a moment and try again.",
        })
        return websession.remember(page, sid)
    except Exception:
        log.exception("failed to create a run for session %s", sid)
        page = templates.TemplateResponse(request, "index.html", {
            "max_concept": settings.max_concept_chars,
            "leaderboard": service.leaderboard(10),
            "error": "Something broke while opening the dungeon. Try again.",
        })
        return websession.remember(page, sid)
    return websession.remember(RedirectResponse("/game", status_code=303), sid)


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
    return templates.TemplateResponse(request, "partials/inventory_list.html", gc)


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
