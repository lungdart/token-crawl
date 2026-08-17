"""World-cache reads/writes. All content is validated Pydantic serialized to JSON."""
from __future__ import annotations

from app import db
from app.models.character import CrawlerClass, LevelUp
from app.models.entities import AreaContent, EnemyStatBlock, Item, SafeRoom
from app.models.responses import ResponseBank
from app.models.scene import RoomArt
from app.models.rulings import InteractionRuling


def _select_json(table: str, model_cls, json_col: str, where: str, params: tuple):
    row = db.get().execute(
        f"SELECT * FROM {table} WHERE {where} AND status='ready'", params
    ).fetchone()
    if row is None:
        return None
    return model_cls.model_validate_json(row[json_col]), row


def mark_failed(table: str, where: str, params: tuple) -> None:
    with db.tx() as conn:
        conn.execute(f"UPDATE {table} SET status='failed' WHERE {where}", params)


# --- areas -------------------------------------------------------------------

def get_area_row(floor_id: int, x: int, y: int):
    return db.get().execute(
        "SELECT * FROM areas WHERE floor_id=? AND x=? AND y=?", (floor_id, x, y)
    ).fetchone()


def get_area_by_id(area_id: int):
    return db.get().execute("SELECT * FROM areas WHERE id=?", (area_id,)).fetchone()


def area_content(row) -> AreaContent:
    return AreaContent.model_validate_json(row["content_json"])


def ready_area(floor_id: int, x: int, y: int):
    row = get_area_row(floor_id, x, y)
    return row if row is not None and row["status"] == "ready" else None


def claim_area(floor_id: int, x: int, y: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO areas (floor_id, x, y, status) VALUES (?, ?, ?, 'generating')",
            (floor_id, x, y),
        )
        return cur.rowcount > 0


def store_area(floor_id: int, x: int, y: int, content: AreaContent,
               is_landing: bool, has_stairs: bool, is_safe_room: bool) -> None:
    with db.tx() as conn:
        conn.execute(
            """UPDATE areas SET status='ready', content_json=?, is_landing=?, has_stairs_down=?,
               is_safe_room=? WHERE floor_id=? AND x=? AND y=?""",
            (content.model_dump_json(), int(is_landing), int(has_stairs), int(is_safe_room),
             floor_id, x, y),
        )


def neighbor_summaries(floor_id: int, x: int, y: int) -> dict[str, dict]:
    """Ready neighbours' opening line + reciprocal-exit info, for generation context.

    Rooms have no names, so a neighbour is described by the first sentence of what it
    actually looks like — which is better context for matching character anyway."""
    out = {}
    for d, (dx, dy) in {"n": (0, 1), "s": (0, -1), "e": (1, 0), "w": (-1, 0)}.items():
        row = ready_area(floor_id, x + dx, y + dy)
        if row:
            c = area_content(row)
            back = {"n": "s", "s": "n", "e": "w", "w": "e"}[d]
            gist = c.description.split(". ")[0][:120]
            out[d] = {"gist": gist, "open_toward_us": getattr(c.exits, back)}
    return out


# --- response bank -----------------------------------------------------------

def ready_bank(floor_id: int):
    return _select_json("response_banks", ResponseBank, "bank_json", "floor_id=?", (floor_id,))


def claim_bank(floor_id: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO response_banks (floor_id) VALUES (?)", (floor_id,))
        return cur.rowcount > 0


def store_bank(floor_id: int, bank: ResponseBank) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE response_banks SET status='ready', bank_json=? WHERE floor_id=?",
            (bank.model_dump_json(), floor_id),
        )


# --- room art ----------------------------------------------------------------

def ready_art(area_id: int):
    row = db.get().execute(
        "SELECT asset_json FROM visual_assets WHERE kind='room' AND ref_id=? AND status='ready'",
        (area_id,)).fetchone()
    return RoomArt.model_validate_json(row["asset_json"]) if row else None


def claim_art(area_id: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO visual_assets (kind, ref_id) VALUES ('room', ?)", (area_id,))
        return cur.rowcount > 0


def store_art(area_id: int, art: RoomArt) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE visual_assets SET status='ready', asset_json=? WHERE kind='room' AND ref_id=?",
            (art.model_dump_json(), area_id))


# --- floors ------------------------------------------------------------------

def ready_floor(floor_id: int):
    """The stored plan for a floor, or None if it has not been written yet."""
    row = db.get().execute(
        "SELECT * FROM floors WHERE id=? AND status='ready'", (floor_id,)).fetchone()
    return row


def claim_floor(floor_id: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO floors (id, slug, title, brief_md, config_json, status,
                                             claimed_at)
               VALUES (?, '', '', '', '{}', 'generating', datetime('now'))""",
            (floor_id,),
        )
        return cur.rowcount > 0


def store_floor(floor_id: int, slug: str, title: str, body_md: str, config_json: str) -> None:
    with db.tx() as conn:
        conn.execute(
            """UPDATE floors SET status='ready', slug=?, title=?, brief_md=?, config_json=?
               WHERE id=?""",
            (slug, title, body_md, config_json, floor_id),
        )


# --- enemy types -------------------------------------------------------------

def ready_enemy(floor_id: int, name_key: str):
    return _select_json("enemy_types", EnemyStatBlock, "stat_block_json",
                        "floor_id=? AND name_key=?", (floor_id, name_key))


def claim_enemy(floor_id: int, name_key: str) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO enemy_types (floor_id, name_key) VALUES (?, ?)",
            (floor_id, name_key),
        )
        return cur.rowcount > 0


def store_enemy(floor_id: int, name_key: str, block: EnemyStatBlock) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE enemy_types SET status='ready', stat_block_json=? WHERE floor_id=? AND name_key=?",
            (block.model_dump_json(), floor_id, name_key),
        )


def enemy_type_id(floor_id: int, name_key: str) -> int | None:
    row = db.get().execute(
        "SELECT id FROM enemy_types WHERE floor_id=? AND name_key=?", (floor_id, name_key)
    ).fetchone()
    return row["id"] if row else None


# --- drop tables (slots) -----------------------------------------------------

def ready_drop_slots(enemy_type_id: int):
    marker = db.get().execute(
        "SELECT * FROM drop_tables WHERE enemy_type_id=? AND status='ready'", (enemy_type_id,)
    ).fetchone()
    if marker is None:
        return None
    slots = db.get().execute(
        "SELECT * FROM drop_table_entries WHERE enemy_type_id=? ORDER BY slot_index", (enemy_type_id,)
    ).fetchall()
    return marker, slots


def claim_drop_table(enemy_type_id: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO drop_tables (enemy_type_id) VALUES (?)", (enemy_type_id,)
        )
        return cur.rowcount > 0


def store_drop_table(enemy_type_id: int, nothing_weight: int, slots) -> None:
    with db.tx() as conn:
        for i, s in enumerate(slots):
            conn.execute(
                """INSERT OR IGNORE INTO drop_table_entries
                   (enemy_type_id, slot_index, weight, qty_dice, rarity, hint, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (enemy_type_id, i, s.weight, s.qty_dice, s.rarity, s.hint),
            )
        conn.execute(
            "UPDATE drop_tables SET status='ready', nothing_weight=? WHERE enemy_type_id=?",
            (nothing_weight, enemy_type_id),
        )


def drop_slot(enemy_type_id: int, slot_index: int):
    return db.get().execute(
        "SELECT * FROM drop_table_entries WHERE enemy_type_id=? AND slot_index=?",
        (enemy_type_id, slot_index),
    ).fetchone()


def claim_drop_slot_item(entry_id: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            "UPDATE drop_table_entries SET status='generating', claimed_at=datetime('now') "
            "WHERE id=? AND status='pending'", (entry_id,),
        )
        return cur.rowcount > 0


def store_drop_slot_item(entry_id: int, item_id: int) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE drop_table_entries SET status='ready', item_id=? WHERE id=?", (item_id, entry_id)
        )


# --- items -------------------------------------------------------------------

def insert_item(floor_id: int | None, name_key: str, item: Item) -> int:
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO items (floor_id, name_key, item_json) VALUES (?, ?, ?) "
            "ON CONFLICT(floor_id, name_key) DO UPDATE SET item_json=item_json RETURNING id",
            (floor_id, name_key, item.model_dump_json()),
        )
        return cur.fetchone()["id"]


def get_item(item_id: int) -> Item | None:
    row = db.get().execute("SELECT item_json FROM items WHERE id=?", (item_id,)).fetchone()
    return Item.model_validate_json(row["item_json"]) if row else None


# --- safe rooms --------------------------------------------------------------

def ready_safe_room(area_id: int):
    return _select_json("safe_rooms", SafeRoom, "safe_room_json", "area_id=?", (area_id,))


def claim_safe_room(area_id: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO safe_rooms (area_id) VALUES (?)", (area_id,))
        return cur.rowcount > 0


def store_safe_room(area_id: int, room: SafeRoom) -> None:
    with db.tx() as conn:
        for i, s in enumerate(room.stock):
            conn.execute(
                """INSERT OR IGNORE INTO safe_room_stock (area_id, slot_index, rarity, hint, price, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (area_id, i, s.rarity, s.hint, s.price),
            )
        conn.execute(
            "UPDATE safe_rooms SET status='ready', safe_room_json=? WHERE area_id=?",
            (room.model_dump_json(), area_id),
        )


def safe_room_stock(area_id: int):
    return db.get().execute(
        "SELECT * FROM safe_room_stock WHERE area_id=? ORDER BY slot_index", (area_id,)
    ).fetchall()


def claim_stock_item(slot_id: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            "UPDATE safe_room_stock SET status='generating', claimed_at=datetime('now') "
            "WHERE id=? AND status='pending'", (slot_id,),
        )
        return cur.rowcount > 0


def store_stock_item(slot_id: int, item_id: int) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE safe_room_stock SET status='ready', item_id=? WHERE id=?", (item_id, slot_id))


# --- classes / level-ups -----------------------------------------------------

def ready_class(concept_key: str):
    return _select_json("classes", CrawlerClass, "class_json", "concept_key=?", (concept_key,))


def claim_class(concept_key: str) -> bool:
    with db.tx() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO classes (concept_key) VALUES (?)", (concept_key,))
        return cur.rowcount > 0


def store_class(concept_key: str, cls: CrawlerClass) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE classes SET status='ready', class_json=? WHERE concept_key=?",
            (cls.model_dump_json(), concept_key),
        )


def class_id_for(concept_key: str) -> int | None:
    row = db.get().execute("SELECT id FROM classes WHERE concept_key=?", (concept_key,)).fetchone()
    return row["id"] if row else None


def get_class(class_id: int) -> CrawlerClass | None:
    row = db.get().execute(
        "SELECT class_json FROM classes WHERE id=? AND status='ready'", (class_id,)
    ).fetchone()
    return CrawlerClass.model_validate_json(row["class_json"]) if row else None


def ready_level_up(class_id: int, level: int):
    return _select_json("level_ups", LevelUp, "level_up_json",
                        "class_id=? AND level=?", (class_id, level))


def claim_level_up(class_id: int, level: int) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO level_ups (class_id, level) VALUES (?, ?)", (class_id, level)
        )
        return cur.rowcount > 0


def store_level_up(class_id: int, level: int, lvl: LevelUp) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE level_ups SET status='ready', level_up_json=? WHERE class_id=? AND level=?",
            (lvl.model_dump_json(), class_id, level),
        )


def level_ups_up_to(class_id: int, level: int) -> list[LevelUp]:
    rows = db.get().execute(
        "SELECT level_up_json FROM level_ups WHERE class_id=? AND level<=? AND status='ready' ORDER BY level",
        (class_id, level),
    ).fetchall()
    return [LevelUp.model_validate_json(r["level_up_json"]) for r in rows]


# --- interaction rulings -----------------------------------------------------

def ready_ruling(area_id: int, target_key: str, verb_key: str):
    return _select_json(
        "interaction_rulings", InteractionRuling, "ruling_json",
        "area_id=? AND target_key=? AND verb_key=?", (area_id, target_key, verb_key),
    )


def claim_ruling(area_id: int, target_key: str, verb_key: str) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO interaction_rulings (area_id, target_key, verb_key) VALUES (?, ?, ?)",
            (area_id, target_key, verb_key),
        )
        return cur.rowcount > 0


def store_ruling(area_id: int, target_key: str, verb_key: str, ruling: InteractionRuling) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE interaction_rulings SET status='ready', ruling_json=? "
            "WHERE area_id=? AND target_key=? AND verb_key=?",
            (ruling.model_dump_json(), area_id, target_key, verb_key),
        )


def ruling_id(area_id: int, target_key: str, verb_key: str) -> int | None:
    row = db.get().execute(
        "SELECT id FROM interaction_rulings WHERE area_id=? AND target_key=? AND verb_key=?",
        (area_id, target_key, verb_key),
    ).fetchone()
    return row["id"] if row else None
