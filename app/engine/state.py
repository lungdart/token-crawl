"""RunContext: loads a run + its surroundings, accumulates events, persists deltas.

Combat model:
- A room is a prompt. Entering never grants enemies a turn (design review #5); they act
  only after the crawler does something.
- Per-run enemy instance HP lives in runs.in_combat_json, scoped to the current area.
- Player volatile state (statuses, cooldowns, class resource) lives in runs.stats_json.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field

from app import db, logs
from app.engine.effects_engine import ActiveStatus, Combatant
from app.engine.rng import RunRNG
from app.models.entities import SLOT_INSTANCES, Item
from app.world import repo


log = logs.get(__name__)


# --- one turn at a time ------------------------------------------------------
#
# A turn reads the whole run into memory, changes it, and writes it back. Two turns for
# the SAME crawler overlapping means both read the same starting numbers and the second
# write erases the first — spend 100 gold eight times over and keep all eight purchases.
#
# The web server does not know what a crawler is; it just gets requests and runs them in
# parallel. Only the game knows two requests belong to one run, so the game has to say so.
# Turns for one crawler queue up; different crawlers never meet and nobody waits on anyone
# else.
#
# This holds within ONE process. It is enough because a single FastAPI process already
# serves requests in parallel (plain `def` handlers run on a thread pool), so there is no
# reason to run more than one — and SQLite ties us to a single machine regardless. Running
# several worker processes would need this moved into the database, the way generation
# claims already work.

_turn_locks: dict[int, threading.Lock] = {}
_turn_locks_guard = threading.Lock()
_MAX_TRACKED_RUNS = 10_000


@contextmanager
def one_turn_at_a_time(run_id: int):
    """Hold a crawler still for the length of one turn."""
    with _turn_locks_guard:
        lock = _turn_locks.get(run_id)
        if lock is None:
            if len(_turn_locks) >= _MAX_TRACKED_RUNS:  # drop runs nobody is mid-turn on
                for dead in [k for k, v in _turn_locks.items() if not v.locked()]:
                    del _turn_locks[dead]
            lock = _turn_locks[run_id] = threading.Lock()
    with lock:
        yield

# Used only when a floor's generated lines are unavailable. Each states plainly what
# happened — a fault must never be dressed up as a game outcome ("Nothing happens." for
# a miss is a lie about the fight).
_PLAIN_FALLBACK = {
    "player_miss": "You miss.",
    "enemy_miss": "It attacks you and misses.",
    "blocked_direction": "There is no way through that way.",
    "nothing_there": "That is not here.",
    "item_not_held": "You are not carrying that.",
    "not_equippable": "That cannot be worn or wielded.",
    "no_safe_room": "There is nowhere to trade here.",
    "cannot_afford": "You cannot afford that.",
    "empty_inventory": "You are carrying nothing.",
    "ability_on_cooldown": "That is not ready yet.",
    "resource_too_low": "You do not have enough left for that.",
    "no_stairs_here": "There are no stairs here.",
    "flee_failed": "You fail to get away.",
    "rejected": "That is not something you can do here.",
    "rate_limited": "Slow down.",
    "generation_paused": "No new ground is being opened up right now.",
}


@dataclass
class RunContext:
    run: dict
    rng: RunRNG
    events: list[tuple[str, str]] = field(default_factory=list)

    # --- loading -------------------------------------------------------------

    @classmethod
    def load(cls, run_id: int) -> "RunContext":
        row = db.get().execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"run {run_id}")
        run = dict(row)
        run["stats"] = json.loads(run["stats_json"])
        run["combat"] = json.loads(run["in_combat_json"]) if run["in_combat_json"] else None
        return cls(run=run, rng=RunRNG(run["rng_seed"], run["rng_counter"]))

    # --- events --------------------------------------------------------------

    def say(self, text: str, kind: str = "narration") -> None:
        self.events.append((kind, text))

    def line(self, category: str) -> str:
        """A themed line for a routine mechanical event, from this floor's cached bank."""
        from app.gen import services

        try:
            bank = services.ensure_response_bank(self.run["floor_id"])
        except Exception:
            log.exception("response bank unavailable for floor %s", self.run["floor_id"])
            return _PLAIN_FALLBACK.get(category, logs.FAULT)
        options = getattr(bank, category, None)
        if not options:
            log.error("response bank for floor %s has no lines for %r",
                      self.run["floor_id"], category)
            return _PLAIN_FALLBACK.get(category, logs.FAULT)
        return options[self.rng.next().randrange(len(options))]

    def say_line(self, category: str, kind: str = "narration") -> None:
        self.say(self.line(category), kind)

    # --- area ----------------------------------------------------------------

    def area_row(self):
        return repo.get_area_by_id(self.run["area_id"])

    def area(self):
        return repo.area_content(self.area_row())

    def area_state(self) -> dict:
        row = db.get().execute(
            "SELECT * FROM run_area_state WHERE run_id=? AND area_id=?",
            (self.run["id"], self.run["area_id"]),
        ).fetchone()
        if row is None:
            return {"killed": [], "taken": [], "used_rulings": []}
        return {
            "killed": json.loads(row["killed_keys_json"]),
            "taken": json.loads(row["taken_keys_json"]),
            "used_rulings": json.loads(row["used_ruling_ids_json"]),
        }

    def save_area_state(self, st: dict) -> None:
        with db.tx() as conn:
            conn.execute(
                """INSERT INTO run_area_state (run_id, area_id, killed_keys_json, taken_keys_json,
                                               used_ruling_ids_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(run_id, area_id) DO UPDATE SET
                     killed_keys_json=excluded.killed_keys_json,
                     taken_keys_json=excluded.taken_keys_json,
                     used_ruling_ids_json=excluded.used_ruling_ids_json""",
                (self.run["id"], self.run["area_id"],
                 json.dumps(st["killed"]), json.dumps(st["taken"]), json.dumps(st["used_rulings"])),
            )

    def visible_entities(self) -> list:
        st = self.area_state()
        gone = set(st["killed"]) | set(st["taken"])
        return [e for e in self.area().entities if e.key not in gone]

    def rooms_explored(self) -> int:
        return db.get().execute(
            "SELECT COUNT(*) c FROM run_area_state WHERE run_id=?", (self.run["id"],)
        ).fetchone()["c"]

    # --- inventory -----------------------------------------------------------

    def inventory(self) -> list[dict]:
        rows = db.get().execute(
            """SELECT ri.id AS inv_id, ri.qty, ri.equipped_slots, ri.item_id, i.item_json
               FROM run_inventory ri JOIN items i ON i.id = ri.item_id
               WHERE ri.run_id=? ORDER BY ri.id""",
            (self.run["id"],),
        ).fetchall()
        out = []
        for r in rows:
            slots = json.loads(r["equipped_slots"] or "[]")
            out.append({
                "inv_id": r["inv_id"], "item_id": r["item_id"], "qty": r["qty"],
                "equipped": bool(slots), "slots": slots,
                "item": Item.model_validate_json(r["item_json"]),
            })
        return out

    def add_item(self, item_id: int, qty: int = 1) -> None:
        with db.tx() as conn:
            row = conn.execute(
                "SELECT id, qty FROM run_inventory WHERE run_id=? AND item_id=? AND equipped_slots='[]'",
                (self.run["id"], item_id),
            ).fetchone()
            if row:
                conn.execute("UPDATE run_inventory SET qty=qty+? WHERE id=?", (qty, row["id"]))
            else:
                conn.execute(
                    "INSERT INTO run_inventory (run_id, item_id, qty) VALUES (?, ?, ?)",
                    (self.run["id"], item_id, qty),
                )

    def remove_item(self, inv_id: int, qty: int = 1) -> None:
        with db.tx() as conn:
            row = conn.execute("SELECT qty FROM run_inventory WHERE id=?", (inv_id,)).fetchone()
            if row is None:
                return
            if row["qty"] > qty:
                conn.execute("UPDATE run_inventory SET qty=qty-? WHERE id=?", (qty, inv_id))
            else:
                conn.execute("DELETE FROM run_inventory WHERE id=?", (inv_id,))

    def find_item(self, name: str) -> dict | None:
        """Resolve an item reference. `@name` (from the autocomplete) matches exactly."""
        raw = name.strip()
        exact = raw.startswith("@")
        needle = raw.lstrip("@").strip().lower()
        best = None
        for entry in self.inventory():
            n = entry["item"].name.lower()
            if n == needle:
                return entry
            if not exact and best is None and (needle in n or n in needle):
                best = entry
        return best

    # --- equipment -----------------------------------------------------------

    def occupied_slots(self) -> dict[str, dict]:
        """physical slot -> inventory entry currently filling it."""
        out = {}
        for entry in self.inventory():
            for s in entry["slots"]:
                out[s] = entry
        return out

    def assign_slots(self, item: Item) -> list[str] | None:
        """Pick physical slots for an item. The engine chooses which hand; generation only
        says how many are needed."""
        occupied = self.occupied_slots()
        chosen: list[str] = []
        for logical in item.slots:
            instances = SLOT_INSTANCES.get(logical, [])
            free = [i for i in instances if i not in occupied and i not in chosen]
            if free:
                chosen.append(free[0])
            elif instances:
                # No free instance: evict the first one (caller reports what came off).
                chosen.append(next(i for i in instances if i not in chosen))
            else:
                return None
        return chosen

    def set_equipped(self, inv_id: int, slots: list[str]) -> None:
        with db.tx() as conn:
            conn.execute("UPDATE run_inventory SET equipped_slots=? WHERE id=?",
                         (json.dumps(slots), inv_id))

    def equipped_weapon(self) -> dict | None:
        for entry in self.inventory():
            if entry["equipped"] and entry["item"].attack_dice:
                return entry
        return None

    # --- class resource ------------------------------------------------------

    @property
    def resource(self) -> dict | None:
        return self.run["stats"].get("resource")

    def spend_resource(self, amount: int) -> bool:
        res = self.resource
        if not res or amount <= 0:
            return amount <= 0
        if res["current"] < amount:
            return False
        res["current"] -= amount
        return True

    def change_resource(self, amount: int) -> int:
        res = self.resource
        if not res:
            return 0
        before = res["current"]
        res["current"] = max(0, min(res["max"], res["current"] + amount))
        return res["current"] - before

    def refill_resource(self) -> None:
        res = self.resource
        if res and res.get("refills_in_safe_room", True):
            res["current"] = res["max"]

    # --- combat view of the player ------------------------------------------

    def player_combatant(self) -> Combatant:
        s = self.run["stats"]
        c = Combatant(
            name=self.run["name"],
            hp=self.run["hp"], max_hp=self.run["max_hp"],
            attack=s["attack"], defense=s["defense"], speed=s["speed"],
            attack_dice="1d4",
        )
        weapon = self.equipped_weapon()
        if weapon:
            c.attack_dice = weapon["item"].attack_dice
        for entry in self.inventory():
            if not entry["equipped"]:
                continue
            for eff in entry["item"].equip_effects:
                if eff.type == "stat_modifier" and eff.turns is None:
                    c.statuses.append(ActiveStatus(kind="stat_mod", stat=eff.stat,
                                                   amount=eff.amount, turns_left=999))
        for st in s.get("statuses", []):
            c.statuses.append(ActiveStatus(**st))
        return c

    def store_player(self, c: Combatant) -> None:
        self.run["hp"] = max(0, min(c.hp, self.run["max_hp"]))
        self.run["stats"]["statuses"] = [
            vars(s) for s in c.statuses if 0 < s.turns_left < 999
        ]

    # --- persistence ---------------------------------------------------------

    def persist(self) -> None:
        r = self.run
        with db.tx() as conn:
            conn.execute(
                """UPDATE runs SET status=?, floor_id=?, area_id=?, hp=?, max_hp=?, stats_json=?,
                   xp=?, level=?, kills=?, gold=?, rng_counter=?, in_combat_json=?,
                   death_area_id=?, death_cause=?,
                   died_at=CASE WHEN ? THEN datetime('now') ELSE died_at END
                   WHERE id=?""",
                (r["status"], r["floor_id"], r["area_id"], r["hp"], r["max_hp"],
                 json.dumps(r["stats"]), r["xp"], r["level"], r["kills"], r["gold"],
                 self.rng.counter,
                 json.dumps(r["combat"]) if r["combat"] else None,
                 r["death_area_id"], r["death_cause"],
                 r["status"] == "dead" and r["died_at"] is None,
                 r["id"]),
            )
            for kind, text in self.events:
                conn.execute(
                    "INSERT INTO run_events (run_id, kind, text) VALUES (?, ?, ?)",
                    (r["id"], kind, text),
                )
