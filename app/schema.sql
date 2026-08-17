-- World cache: shared, immutable once status='ready'.
-- Claim pattern: status generating|ready|failed + claimed_at for stale reclamation.

-- Floor 1 is hand-written and loaded from floors/*.md at startup. Everything below it is
-- written by the AI the first time a crawler takes the stairs, then cached like the rest of
-- the world, so every crawler descends into the same place.
CREATE TABLE IF NOT EXISTS floors (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL,
  title TEXT NOT NULL,
  brief_md TEXT NOT NULL,
  config_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready',
  claimed_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Floors are infinite: x/y are unbounded. No grid, no edges.
CREATE TABLE IF NOT EXISTS areas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  floor_id INTEGER NOT NULL REFERENCES floors(id),
  x INTEGER NOT NULL,
  y INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'generating',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  content_json TEXT,
  is_landing INTEGER NOT NULL DEFAULT 0,
  has_stairs_down INTEGER NOT NULL DEFAULT 0,
  is_safe_room INTEGER NOT NULL DEFAULT 0,
  UNIQUE(floor_id, x, y)
);

-- Per-floor bank of themed lines for mechanical events (design review #1).
CREATE TABLE IF NOT EXISTS response_banks (
  floor_id INTEGER PRIMARY KEY REFERENCES floors(id),
  status TEXT NOT NULL DEFAULT 'generating',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  bank_json TEXT
);

CREATE TABLE IF NOT EXISTS enemy_types (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  floor_id INTEGER NOT NULL REFERENCES floors(id),
  name_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'generating',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  stat_block_json TEXT,
  UNIQUE(floor_id, name_key)
);

-- Drop tables are JIT two-stage: slots first (on first kill), items per-slot
-- when a roll first lands on that slot.
CREATE TABLE IF NOT EXISTS drop_table_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  enemy_type_id INTEGER NOT NULL REFERENCES enemy_types(id),
  slot_index INTEGER NOT NULL,
  weight INTEGER NOT NULL,
  qty_dice TEXT NOT NULL DEFAULT '1',
  rarity TEXT NOT NULL,
  hint TEXT NOT NULL,
  item_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(enemy_type_id, slot_index)
);

CREATE TABLE IF NOT EXISTS drop_tables (
  enemy_type_id INTEGER PRIMARY KEY REFERENCES enemy_types(id),
  status TEXT NOT NULL DEFAULT 'generating',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  nothing_weight INTEGER
);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  floor_id INTEGER,
  name_key TEXT NOT NULL,
  item_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(floor_id, name_key)
);

-- Safe rooms: shop + inn in one. Scattered pseudo-randomly like stairs.
CREATE TABLE IF NOT EXISTS safe_rooms (
  area_id INTEGER PRIMARY KEY REFERENCES areas(id),
  status TEXT NOT NULL DEFAULT 'generating',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  safe_room_json TEXT
);

CREATE TABLE IF NOT EXISTS safe_room_stock (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  area_id INTEGER NOT NULL REFERENCES areas(id),
  slot_index INTEGER NOT NULL,
  rarity TEXT NOT NULL,
  hint TEXT NOT NULL,
  item_id INTEGER,
  price INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(area_id, slot_index)
);

CREATE TABLE IF NOT EXISTS classes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  concept_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'generating',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  class_json TEXT
);

-- What a class gains at a given level. AI-decided, cached per class+level.
CREATE TABLE IF NOT EXISTS level_ups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  class_id INTEGER NOT NULL REFERENCES classes(id),
  level INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'generating',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  level_up_json TEXT,
  UNIQUE(class_id, level)
);

CREATE TABLE IF NOT EXISTS interaction_rulings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  area_id INTEGER NOT NULL REFERENCES areas(id),
  target_key TEXT NOT NULL,
  verb_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'generating',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  ruling_json TEXT,
  UNIQUE(area_id, target_key, verb_key)
);

-- Future bolt-on: LLM-generated pixel-art scenes/sprites, cached like all content.
CREATE TABLE IF NOT EXISTS visual_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'generating',
  claimed_at TEXT NOT NULL DEFAULT (datetime('now')),
  asset_json TEXT,
  UNIQUE(kind, ref_id)
);

-- Cost observability: every LLM call.
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  ok INTEGER NOT NULL DEFAULT 1,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created ON llm_calls(created_at);

-- Per-run state --------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  active_run_id INTEGER
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  name TEXT NOT NULL,
  class_id INTEGER NOT NULL REFERENCES classes(id),
  status TEXT NOT NULL DEFAULT 'alive',
  floor_id INTEGER NOT NULL,
  area_id INTEGER NOT NULL,
  hp INTEGER NOT NULL,
  max_hp INTEGER NOT NULL,
  stats_json TEXT NOT NULL,  -- attack/defense/speed, statuses, cooldowns, class resource
  xp INTEGER NOT NULL DEFAULT 0,
  level INTEGER NOT NULL DEFAULT 1,
  kills INTEGER NOT NULL DEFAULT 0,
  gold INTEGER NOT NULL DEFAULT 0,
  rng_seed INTEGER NOT NULL,
  rng_counter INTEGER NOT NULL DEFAULT 0,
  in_combat_json TEXT,
  death_area_id INTEGER,
  death_cause TEXT,
  died_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_runs_death_area ON runs(death_area_id);
CREATE INDEX IF NOT EXISTS idx_runs_leaderboard ON runs(status, floor_id, kills);

CREATE TABLE IF NOT EXISTS run_inventory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  item_id INTEGER NOT NULL REFERENCES items(id),
  qty INTEGER NOT NULL DEFAULT 1,
  -- JSON list of occupied physical slots, e.g. ["r_hand","l_hand"]. Empty = not equipped.
  equipped_slots TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_inventory_run ON run_inventory(run_id);

CREATE TABLE IF NOT EXISTS run_area_state (
  run_id INTEGER NOT NULL REFERENCES runs(id),
  area_id INTEGER NOT NULL REFERENCES areas(id),
  visited INTEGER NOT NULL DEFAULT 1,
  killed_keys_json TEXT NOT NULL DEFAULT '[]',
  taken_keys_json TEXT NOT NULL DEFAULT '[]',
  used_ruling_ids_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (run_id, area_id)
);

CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id);
