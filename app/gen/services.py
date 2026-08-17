"""JIT generation services: cache-check -> claim -> generate -> note -> store.

One function per generation type; all share the claim protocol in gen.locks and the backend
seam in gen.llm. Nothing is generated until a crawler first observes it.

Balance is not enforced here. The scale table is a reference written into the prompts; what
comes back is accepted. Integrity — schema, types, dice — is enforced by the Pydantic models.
"""
from __future__ import annotations

import time

from app import db, logs
from app.config import settings
from app.gen import llm, prompts
from app.gen.art import to_room_art
from app.engine import progress
from app.gen.locks import claim_row, get_or_generate
from app.security import limits
from app.models.character import CrawlerClass, LevelUp
from app.models.entities import AreaContent, DropTable, EnemyStatBlock, Item, SafeRoom
from app.models.floor_brief import FloorBrief, GeneratedFloor
from app.models.responses import ResponseBank
from app.models.scene import RoomArt
from app.models.rulings import InteractionRuling
from app.world import floors, repo


log = logs.get(__name__)


def _reclaim(table: str, where: str, params: tuple) -> bool:
    with db.tx() as conn:
        return claim_row(conn, table, where, params)


def _session(floor_id: int) -> str:
    """Pin a floor's generation burst to one provider so its cached prefix stays warm."""
    return f"floor-{floor_id}"


# --- floor plans -------------------------------------------------------------

def ensure_floor(floor_id: int):
    """The plan for a floor, written on the first descent into it and cached forever.

    Floor 1 is hand-written and loaded at startup; everything below is the AI's, following
    on from the floor above. Code decides the numbers from the depth — see
    models/scale.floor_levels — so difficulty climbs steadily however deep it goes.
    """
    above = floors.get_brief(floor_id - 1)

    def generate_and_store():
        plan = llm.get_backend().generate(
            kind="floor_plan", model=settings.gen_model,
            system_blocks=prompts.floor_plan_system(),
            session_id=_session(floor_id),
            user=prompts.floor_plan_prompt(floor_id, above.title, above.body_md),
            output_model=GeneratedFloor,
        )
        brief = FloorBrief(
            floor=floor_id, slug=plan.slug, title=plan.title, body_md=plan.theme,
            stairs=above.stairs, safe_rooms=above.safe_rooms,
            target_enemy_density=above.target_enemy_density,
        )
        repo.store_floor(floor_id, brief.slug, brief.title, brief.body_md,
                         brief.model_dump_json(exclude={"body_md"}))
        return repo.ready_floor(floor_id)

    return get_or_generate(
        kind="floor_plan", key=(floor_id,),
        select_ready=lambda: repo.ready_floor(floor_id),
        claim=lambda: repo.claim_floor(floor_id),
        reclaim_stale=lambda _s: _reclaim("floors", "id=?", (floor_id,)),
        generate_and_store=generate_and_store,
        mark_failed=lambda: repo.mark_failed("floors", "id=?", (floor_id,)),
    )


# --- room art ----------------------------------------------------------------

def ensure_room_art(floor_id: int, area_id: int, description: str,
                    *, has_stairs: bool = False, is_safe_room: bool = False) -> RoomArt:
    """The picture of a room, drawn on first sight and cached forever.

    An image model draws it full size and full colour; app/gen/art.py shrinks it to 64x48
    and quantizes it to sixteen dithered colours. See scripts/palette_bakeoff.py for the
    comparison behind those numbers.
    """
    def generate_and_store():
        png = llm.get_backend().draw(
            kind="room_art", model=settings.image_model,
            prompt=prompts.room_art_prompt(description, has_stairs=has_stairs,
                                           is_safe_room=is_safe_room),
        )
        art = RoomArt.model_validate(to_room_art(png))
        repo.store_art(area_id, art)
        return repo.ready_art(area_id)

    return get_or_generate(
        kind="room_art", key=(area_id,),
        select_ready=lambda: repo.ready_art(area_id),
        claim=lambda: repo.claim_art(area_id),
        reclaim_stale=lambda _s: _reclaim("visual_assets", "kind='room' AND ref_id=?", (area_id,)),
        generate_and_store=generate_and_store,
        mark_failed=lambda: repo.mark_failed("visual_assets", "kind='room' AND ref_id=?", (area_id,)),
    )


# --- response bank -----------------------------------------------------------

def ensure_response_bank(floor_id: int) -> ResponseBank:
    """The floor's themed lines for routine mechanical events (design review #1)."""
    brief = floors.get_brief(floor_id)

    def generate_and_store():
        bank = llm.get_backend().generate(
            kind="response_bank", model=settings.gen_model,
            system_blocks=prompts.floor_prefix(brief),
            session_id=_session(floor_id),
            user=prompts.response_bank_prompt(),
            output_model=ResponseBank,
        )
        repo.store_bank(floor_id, bank)
        return repo.ready_bank(floor_id)

    return get_or_generate(
        kind="response_bank", key=(floor_id,),
        select_ready=lambda: repo.ready_bank(floor_id),
        claim=lambda: repo.claim_bank(floor_id),
        reclaim_stale=lambda _s: _reclaim("response_banks", "floor_id=?", (floor_id,)),
        generate_and_store=generate_and_store,
        mark_failed=lambda: repo.mark_failed("response_banks", "floor_id=?", (floor_id,)),
    )[0]


def warm_response_banks(floor_ids) -> None:
    """Write every floor's stock lines up front, at startup.

    They used to be written the first time one was needed, which put a model call in the
    middle of somebody's first missed swing. Nothing in them depends on play, so there is
    no reason to wait for the moment they are wanted. Cached forever after the first boot.

    A floor that fails here is logged and skipped, not fatal: the lazy path in
    engine/state.py still works, so the game starts either way.
    """
    for floor_id in floor_ids:
        try:
            ensure_response_bank(floor_id)
        except Exception:
            log.exception("could not write the stock lines for floor %s at startup; "
                          "they will be written on first use instead", floor_id)


# --- areas -------------------------------------------------------------------

def ensure_area(floor_id: int, x: int, y: int):
    """Return the ready area row at (x,y), generating it on first observation.

    Floors are unbounded; stairs and safe rooms come from a seeded per-coordinate roll so
    every crawler finds them in the same places.
    """
    brief = floors.get_brief(floor_id)
    is_landing = (x, y) == floors.LANDING
    has_stairs = floors.has_stairs(brief, x, y)
    is_safe = floors.is_safe_room(brief, x, y)

    def generate_and_store():
        neighbors = repo.neighbor_summaries(floor_id, x, y)
        forced = {d: info["open_toward_us"] for d, info in neighbors.items()}
        content = llm.get_backend().generate(
            kind="area", model=settings.gen_model,
            system_blocks=prompts.floor_prefix(brief),
            session_id=_session(floor_id),
            user=prompts.area_prompt(
                x, y, is_landing=is_landing, has_stairs=has_stairs, is_safe_room=is_safe,
                distance=floors.distance_from_landing(x, y),
                neighbors=neighbors, forced_exits=forced,
                enemy_density=brief.target_enemy_density,
            ),
            output_model=AreaContent,
        )
        # Reciprocity is enforced in code, never trusted from the model.
        for d, must_open in forced.items():
            setattr(content.exits, d, must_open)
        if is_landing or is_safe:
            content.entities = [e for e in content.entities if e.kind != "enemy"]
        if not content.exits.open_dirs():  # never generate a sealed room
            content.exits.n = True
        repo.store_area(floor_id, x, y, content, is_landing, has_stairs, is_safe)
        return repo.get_area_row(floor_id, x, y)

    return get_or_generate(
        kind="area", key=(floor_id, x, y),
        select_ready=lambda: repo.ready_area(floor_id, x, y),
        claim=lambda: repo.claim_area(floor_id, x, y),
        reclaim_stale=lambda _s: _reclaim("areas", "floor_id=? AND x=? AND y=?", (floor_id, x, y)),
        generate_and_store=generate_and_store,
        mark_failed=lambda: repo.mark_failed("areas", "floor_id=? AND x=? AND y=?", (floor_id, x, y)),
    )


# --- enemies -----------------------------------------------------------------

def ensure_enemy(floor_id: int, name_key: str, display_name: str,
                 area_name: str, area_description: str) -> EnemyStatBlock:
    brief = floors.get_brief(floor_id)

    def generate_and_store():
        block = llm.get_backend().generate(
            kind="enemy", model=settings.gen_model,
            system_blocks=prompts.floor_prefix(brief),
            session_id=_session(floor_id),
            user=prompts.enemy_prompt(name_key, display_name, area_name, area_description),
            output_model=EnemyStatBlock,
        )
        repo.store_enemy(floor_id, name_key, block)
        return repo.ready_enemy(floor_id, name_key)

    return get_or_generate(
        kind="enemy", key=(floor_id, name_key),
        select_ready=lambda: repo.ready_enemy(floor_id, name_key),
        claim=lambda: repo.claim_enemy(floor_id, name_key),
        reclaim_stale=lambda _s: _reclaim("enemy_types", "floor_id=? AND name_key=?", (floor_id, name_key)),
        generate_and_store=generate_and_store,
        mark_failed=lambda: repo.mark_failed("enemy_types", "floor_id=? AND name_key=?", (floor_id, name_key)),
    )[0]


# --- drop tables & JIT items -------------------------------------------------

def ensure_drop_slots(floor_id: int, name_key: str, enemy_name: str, enemy_flavor: str):
    """First kill of a type: generate the slot manifest (weights/hints, no items)."""
    brief = floors.get_brief(floor_id)
    etid = repo.enemy_type_id(floor_id, name_key)

    def generate_and_store():
        table = llm.get_backend().generate(
            kind="drop_table", model=settings.gen_model,
            system_blocks=prompts.floor_prefix(brief),
            session_id=_session(floor_id),
            user=prompts.drop_table_prompt(enemy_name, enemy_flavor),
            output_model=DropTable,
        )
        repo.store_drop_table(etid, table.nothing_weight, table.slots)
        return repo.ready_drop_slots(etid)

    return get_or_generate(
        kind="drop_table", key=(etid,),
        select_ready=lambda: repo.ready_drop_slots(etid),
        claim=lambda: repo.claim_drop_table(etid),
        reclaim_stale=lambda _s: _reclaim("drop_tables", "enemy_type_id=?", (etid,)),
        generate_and_store=generate_and_store,
        mark_failed=lambda: repo.mark_failed("drop_tables", "enemy_type_id=?", (etid,)),
    )


def generate_item(floor_id: int, hint: str, rarity: str, source: str,
                  name_key: str, context: str = "") -> int:
    """Mint a concrete item from a creative brief. Returns item id (cached by name_key)."""
    brief = floors.get_brief(floor_id)
    existing = db.get().execute(
        "SELECT id FROM items WHERE floor_id IS ? AND name_key=?", (floor_id, name_key)
    ).fetchone()
    if existing:
        return existing["id"]

    level = brief.levels.max


    progress.emit(limits.current_session(), "item")
    item = llm.get_backend().generate(
        kind="item", model=settings.gen_model,
        system_blocks=prompts.floor_prefix(brief),
        session_id=_session(floor_id),
        user=prompts.item_prompt(hint, rarity, source, context, level),
        output_model=Item,
    )
    return repo.insert_item(floor_id, name_key, item)


def _await_slot(fetch):
    """Poll for another request's in-flight item generation to land."""
    for _ in range(60):
        row = fetch()
        if row is not None and row["status"] == "ready" and row["item_id"]:
            return row["item_id"]
        time.sleep(0.25)
    raise llm.GenerationError("item still generating elsewhere")


def resolve_drop_slot_item(floor_id: int, entry_row, enemy_name: str, enemy_flavor: str = "") -> int:
    """A roll landed on this slot. Generate its item on first landing; cached after."""
    if entry_row["status"] == "ready" and entry_row["item_id"]:
        return entry_row["item_id"]
    if repo.claim_drop_slot_item(entry_row["id"]):
        try:
            item_id = generate_item(
                floor_id, entry_row["hint"], entry_row["rarity"],
                f"dropped by {enemy_name}",
                name_key=f"drop_{entry_row['enemy_type_id']}_{entry_row['slot_index']}",
                context=f"{enemy_name}: {enemy_flavor}",
            )
            repo.store_drop_slot_item(entry_row["id"], item_id)
            return item_id
        except Exception:
            repo.mark_failed("drop_table_entries", "id=?", (entry_row["id"],))
            raise
    return _await_slot(lambda: repo.drop_slot(entry_row["enemy_type_id"], entry_row["slot_index"]))


# --- safe rooms --------------------------------------------------------------

def ensure_safe_room(floor_id: int, area_id: int, area_name: str) -> SafeRoom:
    brief = floors.get_brief(floor_id)
    level = brief.levels.max

    def generate_and_store():
        room = llm.get_backend().generate(
            kind="safe_room", model=settings.gen_model,
            system_blocks=prompts.floor_prefix(brief),
            session_id=_session(floor_id),
            user=prompts.safe_room_prompt(area_name, brief.safe_rooms.stock_slots, level),
            output_model=SafeRoom,
        )
        repo.store_safe_room(area_id, room)
        return repo.ready_safe_room(area_id)

    return get_or_generate(
        kind="safe_room", key=(area_id,),
        select_ready=lambda: repo.ready_safe_room(area_id),
        claim=lambda: repo.claim_safe_room(area_id),
        reclaim_stale=lambda _s: _reclaim("safe_rooms", "area_id=?", (area_id,)),
        generate_and_store=generate_and_store,
        mark_failed=lambda: repo.mark_failed("safe_rooms", "area_id=?", (area_id,)),
    )[0]


def resolve_stock_item(floor_id: int, slot_row, keeper: str = "") -> int:
    if slot_row["status"] == "ready" and slot_row["item_id"]:
        return slot_row["item_id"]
    if repo.claim_stock_item(slot_row["id"]):
        try:
            item_id = generate_item(
                floor_id, slot_row["hint"], slot_row["rarity"], "safe-room stock",
                name_key=f"stock_{slot_row['area_id']}_{slot_row['slot_index']}",
                context=f"sold by {keeper}" if keeper else "",
            )
            repo.store_stock_item(slot_row["id"], item_id)
            return item_id
        except Exception:
            repo.mark_failed("safe_room_stock", "id=?", (slot_row["id"],))
            raise
    area_id, idx = slot_row["area_id"], slot_row["slot_index"]
    return _await_slot(lambda: next(
        (r for r in repo.safe_room_stock(area_id) if r["slot_index"] == idx), None))


# --- classes & level-ups -----------------------------------------------------

def ensure_class(concept: str, floor_id: int = 1) -> tuple[int, CrawlerClass]:
    """Character creation: class cached by normalized concept. Returns (class_id, class)."""
    key = floors.concept_key(concept)
    brief = floors.get_brief(floor_id)

    def generate_and_store():
        cls = llm.get_backend().generate(
            kind="class", model=settings.gen_model,
            system_blocks=prompts.floor_prefix(brief),
            session_id=_session(floor_id),
            user=prompts.class_prompt(concept),
            output_model=CrawlerClass,
        )
        repo.store_class(key, cls)
        return repo.ready_class(key)

    cls = get_or_generate(
        kind="class", key=(key,),
        select_ready=lambda: repo.ready_class(key),
        claim=lambda: repo.claim_class(key),
        reclaim_stale=lambda _s: _reclaim("classes", "concept_key=?", (key,)),
        generate_and_store=generate_and_store,
        mark_failed=lambda: repo.mark_failed("classes", "concept_key=?", (key,)),
    )[0]
    return repo.class_id_for(key), cls


def ensure_level_up(class_id: int, level: int, floor_id: int = 1) -> LevelUp:
    """What this class gains at this level. Cached per class+level, so every crawler of a
    class levels identically."""
    brief = floors.get_brief(floor_id)
    cls = repo.get_class(class_id)
    prior = repo.level_ups_up_to(class_id, level - 1)
    known = [a.name for a in cls.starting_abilities]
    known += [lv.new_ability.name for lv in prior if lv.new_ability]

    # Where this class actually stands going into the level, so the AI can correct drift
    # instead of stacking another step onto it. Computed from the class and its cached
    # earlier levels rather than from a live run, so it is the same for every crawler.
    current = {k: getattr(cls, k) + sum(getattr(lv, k) for lv in prior)
               for k in ("max_hp", "attack", "defense", "speed")}

    def generate_and_store():
        lvl = llm.get_backend().generate(
            kind="level_up", model=settings.gen_model,
            system_blocks=prompts.floor_prefix(brief),
            session_id=_session(floor_id),
            user=prompts.level_up_prompt(
                cls.name, cls.flavor, level,
                cls.resource.name if cls.resource else None, known, current),
            output_model=LevelUp,
        )
        repo.store_level_up(class_id, level, lvl)
        return repo.ready_level_up(class_id, level)

    return get_or_generate(
        kind="level_up", key=(class_id, level),
        select_ready=lambda: repo.ready_level_up(class_id, level),
        claim=lambda: repo.claim_level_up(class_id, level),
        reclaim_stale=lambda _s: _reclaim("level_ups", "class_id=? AND level=?", (class_id, level)),
        generate_and_store=generate_and_store,
        mark_failed=lambda: repo.mark_failed("level_ups", "class_id=? AND level=?", (class_id, level)),
    )[0]


# --- adjudication ------------------------------------------------------------

def ensure_ruling(floor_id: int, area_id: int, area_desc: str, target_key: str,
                  target_brief: str, verb_key: str, raw_text: str) -> InteractionRuling:
    """Novel action: the RULING is cached per (area, target, verb) — including rejections, so
    a jailbreak that fooled the parser costs the generation model exactly once, ever."""
    brief = floors.get_brief(floor_id)

    def generate_and_store():
        ruling = llm.get_backend().generate(
            kind="adjudication", model=settings.gen_model,
            system_blocks=prompts.floor_prefix(brief),
            session_id=_session(floor_id),
            user=prompts.adjudication_prompt(area_desc, target_key, target_brief, verb_key, raw_text),
            output_model=InteractionRuling,
        )
        repo.store_ruling(area_id, target_key, verb_key, ruling)
        return repo.ready_ruling(area_id, target_key, verb_key)

    return get_or_generate(
        kind="adjudication", key=(area_id, target_key, verb_key),
        select_ready=lambda: repo.ready_ruling(area_id, target_key, verb_key),
        claim=lambda: repo.claim_ruling(area_id, target_key, verb_key),
        reclaim_stale=lambda _s: _reclaim(
            "interaction_rulings", "area_id=? AND target_key=? AND verb_key=?",
            (area_id, target_key, verb_key)),
        generate_and_store=generate_and_store,
        mark_failed=lambda: repo.mark_failed(
            "interaction_rulings", "area_id=? AND target_key=? AND verb_key=?",
            (area_id, target_key, verb_key)),
    )[0]
