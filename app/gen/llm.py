"""The LLM backend seam. Everything testable hangs off this protocol.

OpenRouterBackend: real calls to OpenRouter's OpenAI-compatible chat-completions
endpoint, with strict JSON-schema structured outputs, provider filtering, prompt
caching, and exact per-call cost accounting.
FixtureBackend: canned/templated JSON — the whole game runs offline.

Why validation is still client-side: OpenRouter's strict mode is enforced
per-provider-endpoint. Some providers guarantee schema compliance; others treat
the schema as a strong hint. So every response is parsed defensively and validated
against the real Pydantic model, with one retry-with-feedback on failure.
`provider.require_parameters` keeps routing to endpoints that claim support.

That retry covers INTEGRITY only — schema, types, dice parsing. Balance is never
enforced here: the scale table is a reference the AI writes against, not a limit, and an
out-of-depth horror is the fun rather than a fault.
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
from pathlib import Path
from typing import Callable, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app import db, logs
from app.config import settings
from app.gen.schema import response_format
from app.security import limits

log = logs.get(__name__)

M = TypeVar("M", bound=BaseModel)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


class GenerationError(Exception):
    pass


class LLMBackend(Protocol):
    def generate(
        self,
        *,
        kind: str,
        model: str,
        system_blocks: list[dict],
        user: str,
        output_model: type[M],
        max_tokens: int | None = None,
        session_id: str | None = None,
    ) -> M: ...

    def draw(self, *, kind: str, model: str, prompt: str) -> bytes: ...


def _log_call(kind: str, model: str, usage: dict | None, duration_ms: int, ok: bool,
              error: str | None = None) -> None:
    usage = usage or {}
    details = usage.get("prompt_tokens_details") or {}
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO llm_calls (kind, model, input_tokens, output_tokens,
                                      cache_read_tokens, cost_usd, duration_ms, ok, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, model,
             usage.get("prompt_tokens", 0) or 0,
             usage.get("completion_tokens", 0) or 0,
             details.get("cached_tokens", 0) or 0,
             float(usage.get("cost", 0.0) or 0.0),
             duration_ms, int(ok), (error or None) and error[:500]),
        )


def extract_json(text: str) -> dict:
    """Parse a model response that should be JSON but might be fenced or padded."""
    cleaned = _FENCE.sub("", text or "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


class OpenRouterBackend:
    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None):
        self.api_key = api_key or settings.openrouter_api_key
        if not self.api_key:
            raise GenerationError(
                "OPENROUTER_API_KEY is not set. Put it in .env, or run with LLM_BACKEND=fixture."
            )
        # Kept open for the life of the app so connections are reused rather than
        # renegotiated per call. `owns_client` so closing only ever shuts one we opened.
        self.owns_client = client is None
        self.client = client or httpx.Client(timeout=settings.request_timeout_s)

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Optional attribution headers; they also unlock OpenRouter's app leaderboard.
            "HTTP-Referer": settings.app_url,
            "X-Title": settings.app_title,
        }

    def generate(self, *, kind, model, system_blocks, user, output_model,
                 max_tokens=None, session_id=None):
        messages = [
            {"role": "system", "content": system_blocks},
            {"role": "user", "content": user},
        ]
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens or settings.gen_max_tokens,
            "response_format": response_format(output_model, kind),
            # Without this, OpenRouter happily routes to a provider that ignores
            # response_format and hands back freeform prose.
            "provider": {"require_parameters": True},
            # Return real cost + cache stats in the usage object.
            "usage": {"include": True},
        }
        if session_id:
            # Pin a burst of related calls (e.g. generating one floor) to the same
            # upstream, so the cached floor-brief prefix stays warm across them.
            body["session_id"] = session_id[:256]

        # One player action is one trigger no matter how many calls it makes; this
        # waits only on the first call of an action.
        limits.charge_generation()

        last_error: str | None = None
        for attempt in range(settings.max_generation_attempts):
            start = time.monotonic()
            try:
                resp = self.client.post(OPENROUTER_URL, headers=self._headers(), json=body)
            except httpx.HTTPError as exc:
                last_error = f"transport error: {exc}"
                _log_call(kind, model, None, int((time.monotonic() - start) * 1000), False, last_error)
                continue
            duration = int((time.monotonic() - start) * 1000)

            if resp.status_code != 200:
                detail = _error_detail(resp)
                _log_call(kind, model, None, duration, False, f"HTTP {resp.status_code}: {detail}")
                # 4xx other than rate-limit won't fix themselves on retry.
                if resp.status_code not in (408, 429) and resp.status_code < 500:
                    raise GenerationError(f"{kind}: OpenRouter {resp.status_code}: {detail}")
                last_error = f"HTTP {resp.status_code}: {detail}"
                continue

            payload = resp.json()
            usage = payload.get("usage")
            choice = (payload.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content")

            if choice.get("finish_reason") == "length":
                last_error = "output truncated (hit max_tokens)"
                _log_call(kind, model, usage, duration, False, last_error)
                continue

            try:
                obj = output_model.model_validate(extract_json(content or ""))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)[:800]
                _log_call(kind, model, usage, duration, False, f"schema: {last_error}")
                messages = messages[:2] + [
                    {"role": "assistant", "content": content or ""},
                    {"role": "user", "content":
                        f"That response failed validation:\n{last_error}\n"
                        f"Reply with ONLY corrected JSON matching the schema exactly."},
                ]
                body["messages"] = messages
                continue

            _log_call(kind, model, usage, duration, True)
            return obj

        raise GenerationError(f"{kind}: generation failed after retries: {last_error}")


    def draw(self, *, kind: str, model: str, prompt: str) -> bytes:
        """Ask for a picture. Returns the raw image bytes.

        A separate path from generate(): there is no schema to enforce and nothing to
        retry into — an image either comes back or it does not.
        """
        limits.charge_generation()
        started = time.monotonic()
        try:
            resp = self.client.post(
                OPENROUTER_URL, headers=self._headers(),
                json={"model": model,
                      "messages": [{"role": "user", "content": prompt}],
                      "modalities": ["image", "text"],
                      "usage": {"include": True}},
            )
        except httpx.HTTPError as exc:
            _log_call(kind, model, None, int((time.monotonic() - started) * 1000), False,
                      error=str(exc))
            raise GenerationError(f"{kind}: {exc}") from exc

        duration = int((time.monotonic() - started) * 1000)
        if resp.status_code != 200:
            detail = _error_detail(resp)
            _log_call(kind, model, None, duration, False, error=detail)
            raise GenerationError(f"{kind}: {detail}")

        data = resp.json()
        usage = data.get("usage") or {}
        images = (data.get("choices") or [{}])[0].get("message", {}).get("images") or []
        if not images:
            _log_call(kind, model, usage, duration, False, error="no image in response")
            raise GenerationError(f"{kind}: the model returned no image")
        _log_call(kind, model, usage, duration, True)
        url = images[0].get("image_url", {}).get("url", "")
        return base64.b64decode(url.split(",", 1)[1])


def _error_detail(resp: httpx.Response) -> str:
    try:
        return json.dumps(resp.json().get("error", resp.json()))[:300]
    except Exception:
        return resp.text[:300]


class FixtureBackend:
    """Deterministic canned outputs. Looks for tests/fixtures/<kind>/<slug>.json,
    else falls back to a generic per-kind template so the game is playable offline."""

    def __init__(self, fixtures_dir: str | Path | None = None):
        self.dir = Path(fixtures_dir) if fixtures_dir else Path("tests/fixtures")
        self.calls: list[tuple[str, str]] = []  # (kind, user) — inspected by tests

    def generate(self, *, kind, model, system_blocks, user, output_model,
                 max_tokens=None, session_id=None):
        self.calls.append((kind, user))
        data = self._lookup(kind, user) or _generic_fixture(kind, user)
        if data is None:
            raise GenerationError(f"no fixture for kind={kind}")
        obj = output_model.model_validate(data)
        _log_call(kind, "fixture", None, 0, True)
        return obj

    def draw(self, *, kind: str, model: str, prompt: str) -> bytes:
        """No network: draw a placeholder whose colours come from the prompt, so offline
        rooms differ from each other and the pipeline downstream is exercised for real."""
        from PIL import Image, ImageDraw

        self.calls.append((kind, prompt))
        seed = sum(ord(c) for c in prompt)
        img = Image.new("RGB", (256, 192), (18 + seed % 24, 14 + seed % 18, 22 + seed % 30))
        d = ImageDraw.Draw(img)
        for i in range(12):
            v = 30 + (seed * (i + 3)) % 150
            d.rectangle([0, 60 + i * 6, 256, 66 + i * 6], fill=(v, v - 10, v - 24))
        d.rectangle([90 + seed % 40, 40, 150 + seed % 40, 120],
                    fill=(200, 150 + seed % 60, 60))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _lookup(self, kind: str, user: str) -> dict | None:
        kind_dir = self.dir / kind
        if not kind_dir.is_dir():
            return None
        for path in sorted(kind_dir.glob("*.json")):
            data = json.loads(path.read_text())
            match = data.get("_match")
            if match is None or match.lower() in user.lower():
                return data.get("output", data)
        return None


def _generic_fixture(kind: str, user: str) -> dict | None:
    """Playable-offline defaults per generation kind."""
    if kind == "floor_plan":
        return {
            "slug": "deeper-workings",
            "title": "The Deeper Workings",
            "theme": "## Theme\n\nThe tool marks give way to something older that was cut, not "
                     "dug.\n\n## Denizens\n\nThings that were left down here on purpose.\n\n"
                     "## Set Pieces\n\nA shaft with no bottom anyone has found.\n\n"
                     "## Loot\n\nTools made for hands that are not quite hands.",
        }
    if kind == "area":
        return {
            "name": "Hewn Passage",
            "description": "Rough stone, tool-marked, widening a little before it narrows again. Grit underfoot.",
            "exits": {"n": True, "s": True, "e": True, "w": True},
            "entities": [
                {"key": "tunnel_goblin", "kind": "enemy", "name": "Tunnel Goblin",
                 "brief": "A goblin with a scavenged pry-bar watches from the low end of the passage."},
                {"key": "cut_marks", "kind": "feature", "name": "Cut Marks",
                 "brief": "Tool marks in the wall that stop partway through a stroke."},
            ],
        }
    if kind == "enemy":
        return {
            "name": "Tunnel Goblin", "flavor": "Territorial, and better organized than it looks.",
            "level": 1, "hp": 9, "attack": 5, "defense": 3, "speed": 4, "xp": 6,
            "gold": "1d6", "attack_dice": "1d4", "abilities": [],
        }
    if kind == "drop_table":
        return {
            "nothing_weight": 40,
            "slots": [
                {"weight": 30, "qty_dice": "1", "rarity": "junk", "hint": "a bent scavenged tool"},
                {"weight": 20, "qty_dice": "1", "rarity": "common", "hint": "a pry-bar sharpened at one end"},
                {"weight": 10, "qty_dice": "1", "rarity": "uncommon", "hint": "something clean that does not belong down here"},
            ],
        }
    if kind == "item":
        return {
            "name": "Sharpened Pry-Bar", "flavor": "Iron, notched, honed on one end by someone patient.",
            "rarity": "common", "slots": ["hand"], "use_effects": [], "equip_effects": [],
            "consumed_on_use": False, "value_gold": 8, "attack_dice": "1d6",
        }
    if kind == "class":
        return {
            "name": "Tunnel Pilgrim", "flavor": "Came down here on purpose, which worries people.",
            "max_hp": 22, "attack": 5, "defense": 3, "speed": 4,
            "resource": {"name": "Resolve", "max_value": 10, "starts_full": True,
                         "per_turn": 0, "refills_in_safe_room": True},
            "starting_items": [{"hint": "a humble but pointed walking implement", "slots": ["hand"]}],
            "starting_abilities": [{
                "name": "Set Footing", "flavor": "You plant yourself and swing from the shoulder.",
                "cooldown": 2, "resource_cost": 2, "hp_cost": 0,
                "effects": [{"type": "damage", "amount": "1d6", "damage_type": "physical"}],
            }],
        }
    if kind == "level_up":
        return {
            "max_hp": 5, "attack": 1, "defense": 1, "speed": 1, "resource_max": 2,
            "new_ability": {
                "name": "Second Wind", "flavor": "You remember you would rather not die here.",
                "cooldown": 3, "resource_cost": 0, "hp_cost": 0,
                "effects": [{"type": "heal", "amount": "1d8"}],
            },
            "announcement": "You are steadier on your feet than you were.",
        }
    if kind == "adjudication":
        return {
            "rejected": False, "rejection_quip": None,
            "narration_success": "It gives, grudgingly, and something shifts.",
            "narration_failure": "It does not give, and your fingers pay for the attempt.",
            "success_kind": "stat_check", "check_stat": "attack", "difficulty": 8,
            "effects_on_success": [], "effects_on_failure": [], "grants_item_spec": None,
            "repeatable": True,
        }
    if kind == "safe_room":
        return {
            "keeper_name": "The Quartermaster",
            "keeper_flavor": "Sits behind a counter assembled from crate lids. Does not explain itself.",
            "greeting": "The counter is open. Coin only.",
            "rest_line": "The air here is dry and still, and the ache goes out of you.",
            "stock": [
                {"rarity": "common", "hint": "a dependable length of pipe", "price": 15},
                {"rarity": "common", "hint": "a flask of something restorative", "price": 10},
                {"rarity": "uncommon", "hint": "padding scavenged into armor", "price": 40},
                {"rarity": "junk", "hint": "a souvenir of no clear purpose", "price": 3},
            ],
        }
    if kind == "response_bank":
        two = lambda a, b: [a, b]
        return {
            "player_miss": two("Your swing goes wide.", "The blow lands on stone instead."),
            "enemy_miss": two("It strikes at you and misses.", "The attack passes close and finds nothing."),
            "blocked_direction": two("There is no way through in that direction.", "Solid rock that way."),
            "nothing_there": two("There is nothing here matching that.", "Your hand closes on nothing."),
            "item_not_held": two("You are not carrying that.", "Nothing like that is in your pack."),
            "not_equippable": two("That is not something you can wear or wield.", "It will not go on."),
            "no_safe_room": two("There is no counter here to trade at.", "Nobody here is selling."),
            "cannot_afford": two("You do not have the coin for it.", "Your purse comes up short."),
            "empty_inventory": two("You are carrying nothing at all.", "Your pack is empty."),
            "ability_on_cooldown": two("You are not ready to do that again.", "Not yet."),
            "resource_too_low": two("You do not have enough left for that.", "There is nothing left to spend."),
            "no_stairs_here": two("There are no stairs in this room.", "Nothing here leads down."),
            "blocked_by_enemies": two("It moves to cut you off.", "Not while that is watching you."),
            "flee_failed": two("You do not get clear.", "The way out closes before you reach it."),
            "rejected": ["That is not something you can do here.",
                         "Nothing in this dungeon is listening for that.",
                         "The tunnel does not respond to that."],
            "rate_limited": two("Slow down.", "Give it a moment."),
            "generation_paused": two("The way ahead has not been carved yet.",
                                     "Nothing new is being opened up right now."),
        }
    if kind == "parse":
        return None  # parsing has its own keyword fast path; no generic fixture
    return None


_backend: LLMBackend | None = None


def get_backend() -> LLMBackend:
    global _backend
    if _backend is None:
        _backend = FixtureBackend() if settings.llm_backend == "fixture" else OpenRouterBackend()
    return _backend


def set_backend(backend: LLMBackend | None) -> None:
    """Swap the backend. Closes whatever was there, since replacing a backend without
    closing it leaks its open connections."""
    global _backend
    close_backend()
    _backend = backend


def close_backend() -> None:
    """Shut the current backend's connections. Called on app shutdown and whenever the
    backend is replaced."""
    global _backend
    closer = getattr(_backend, "close", None)
    if closer is not None:
        try:
            closer()
        except Exception:
            log.exception("failed to close the LLM backend cleanly")
    _backend = None
