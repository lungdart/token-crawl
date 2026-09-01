# Design review — decisions and status

**Status: all 14 decisions implemented.** 144 offline tests + 8 live tests are collected.
The offline ones pass. The live ones are only *counted* — `addopts` deselects them and
they need an API key, so nothing here claims they pass; two of their bodies are exercised
offline against the fixture backend in `tests/test_meta_live_bodies.py`.

Running notes from reviewing the build against the actual intent.
Terminology: the per-floor `.md` file is a **floor plan**. (I had been calling it a
"brief" — my word, not a real one, now retired.)

## Decided

- **Narrated description style stays.** Sierra-style: describe the place, don't have a
  host address the player. "Sierra" was about the typed-command interaction model, not
  a voice; the game-show host register came from over-fusing that with the DCC premise.
- **No narrator entity exists or should.** Voice is a tone instruction in the generation
  prompt, not a character the engine tracks.
- **One exception is intentional:** the gatekeeper's rejection quip addresses the player
  directly, because it responds to something the player did *as a player* (injection,
  out-of-game input) rather than as a character. This was an explicit request.

## Fixes to make

### 1. Hardcoded jokes in the engine → generate and cache per floor

**Problem.** ~40 hand-written comic lines are string literals in Python — e.g.
`"The dungeon golf-claps."`, `"It pretends that was a feint."`, `"Your inventory
contains hopes, dreams, and pocket lint."` They live in `app/engine/*.py`,
`app/security/quips.py`, and `app/engine/resolver.py`.

This contradicts the premise (nothing pre-programmed in the data). Worse, they fire on
*mechanical* events — misses, blocked exits, empty inventory — which happen far more
often than entering a new room, so they're likely a larger share of text the player
reads than the generated descriptions are. And they're identical on every floor forever,
in a voice picked by whoever wrote the engine.

**Why it happened.** Scaffolding. The mechanical engine was built before any model was
wired in; every code path needed placeholder text, and writing it in-voice made offline
fixture mode read like a game instead of like error messages. It was never removed.

**Fix.** A per-floor *response bank*: generated once when a floor is first entered,
cached forever, themed to that floor — same JIT-and-cache pattern as areas and enemies,
one extra generation per floor. Categories needed: attack miss (player + enemy), blocked
direction, nothing-to-take, item-not-held, not-equippable, no-shop-here, insufficient
gold, empty inventory, ability on cooldown, gatekeeper rejection quips, rate-limit and
spend-cap refusals. Engine picks from the bank via the run's seeded RNG.

Then an ice floor doesn't golf-clap.

### 2. Local theme doesn't reach enemy and item generation

**Confirmed working:** all 8 generators (area, enemy, drop table, item, shop, class,
ability, adjudication) receive the full floor plan text, so floor-level coherence is
solid — nothing is generated without knowing it's the sewers.

**The gap is local.** "Don't fight a fire beast on a beach," one level down:

- `enemy_prompt` receives the area's **name only** (`"first encountered in 'The Flooded
  Cathedral'"`), never its description. The generator knows the floor, not that *this
  room is waist-deep in water*.
- `item_prompt` receives a rarity, a one-line hint, and `"dropped by Shank Goblin"` —
  not the enemy's flavor, not its abilities, not the room. A fire-themed enemy can drop
  a frost trinket with nothing to catch it.

**Fix.** Pass the area description into enemy generation, and the enemy's flavor +
abilities into item generation. Both are already loaded at those call sites; they simply
aren't handed over. Small change to `app/gen/prompts.py` and the two call sites in
`app/gen/services.py`.

### 7. The AI owns character numbers; code supplies a baseline to reference

**Decided.** The AI decides starting stats, XP required for the next level, starting
abilities/spells, and what happens on each level-up. In every case code passes a
**baseline hint** — what a generic balanced, classless, raceless crawler would have at
that point — purely as a reference to balance the decision against. Same pattern as #4:
code advises, AI decides.

**What this replaces.** Three lookup tables in `app/models/chargen.py`:
- `SPREADS` — five preset stat lines (`glass_cannon`, `tank`, …) that the AI picked from
  by label while code supplied the numbers. Becomes a single baseline reference.
- `LEVEL_GAINS` — fixed per-level stat increases keyed off that label.
- `XP_PER_LEVEL` — a global XP curve with a hard cap at level 6.

Today the prose is infinite but the character is a dropdown: every concept resolves to one
of five identical stat lines. After this, a concept that reads as fragile-and-deadly gets
genuinely low HP and high attack rather than the nearest preset.

**AMENDED — the XP curve stays global.** Level-up thresholds are the same for every
crawler, in code, not AI-decided. Per-class curves would have made XP meaningless as a
ranking axis (one class hitting level 5 at 200 XP while another needs 900), and XP is
wanted on the leaderboard. So `XP_PER_LEVEL` survives; `SPREADS` and `LEVEL_GAINS` do not.

The AI still decides **starting stats, starting abilities/spells, and what happens on each
level-up** — just not what a level *costs*.

**Storage implication.** Level-up behavior becomes a property of the *class*, generated once
per concept and cached like everything else. Every Pyromancer levels the same way.

**Integrity rails** (per #4 — runnable, not balanced): stats must be positive integers, and
a level-up must never reduce max HP to zero or below.

### 7a. Leaderboard ranks floor → rooms → XP → kills

**Decided.** In that order. Depth is the headline; rooms explored rewards thorough play over
lucky stair placement (with stairs scattered, two crawlers can both reach floor 3 having seen
8 rooms or 60); XP is comparable again now that the curve is global; kills breaks ties.

Rooms explored is already tracked — one `run_area_state` row per visited area, so it's a
count with no new state.

Drops the current `(status='won') DESC` primary sort, which was an artifact of floor 1 being
the only floor.

### 8. Safe rooms replace the per-floor shop

**Decided.** A single room type that is the rest stop: **no monsters, shop, inn.**
Scattered pseudo-randomly on the same seeded per-coordinate roll as stairs (#3), rather
than one code-placed cell per floor.

**Why the old model breaks.** Shop placement currently picks one cell 1–2 rooms from the
landing out of the finite grid — the same candidate-set problem infinite floors created for
stairs. And on an endless floor a single shop is either stumbled into at once or never
found, which would make gold worthless for that crawl since it's the only sink.

Each safe room generates its own stock, so scattered shops get distinct inventories for free.

**Resting: free, unlimited, automatic on entry.** Walk in, you're topped up.

*Known consequence (accepted):* a known safe room behind you means fights are never
attritional — retreat, heal, return. The only real threat becomes dying inside a single
fight. Recorded because it interacts with #5: entry is safe, flight is contested, and
healing is free, so contested flight is now the *only* thing keeping a bad room dangerous.

### 9. One generic class resource; resource change is an effect

**Decided.** Casters and brawlers currently play identically — abilities and spells are the
same thing, governed only by a cooldown timer, with no pool to spend. Fix: a single generic
resource the class defines and themes.

**Shape:**
- **One optional resource per class** — name, max, starts full or empty, optional per-turn
  drift. All AI-set. A class that declares none is cooldown-only.
- **One new effect verb: `resource_change: ±N`.** Resource change is an *effect*, so a spell
  can fill the bar the same way another spell heals. This reuses existing machinery rather
  than needing a passive trigger system.
- **Two optional fields on an ability** — a resource cost and an HP cost. Cooldown already
  exists and stays independent of both.

**Everything themes off the name.** `MP`, `Rage`, `Charge`, `Sanity`, `Grease Reserves` —
the engine tracks a named number and never needs to know what the word means.

**Cases covered:** mage consumes MP on cast (cost); spell with or without a cooldown
(independent fields); cooldown-only class (declares no resource); spell that fills the bar
(`resource_change: +N`); spell that costs life (HP cost); one skill fills while another
drains (both, on different abilities). Rage-on-hit works via the existing `on_hit_trigger`
wrapping a `resource_change` — a one-line widening of `SimpleEffect`.

**Explicitly excluded: passive triggers.** No resources that fill from being hit, from
kills, or from time-based world events. Resources change only through effects the player's
actions produce. This is the one case the design doesn't cover, and it's deliberate — passive
triggers were the complex part and are not worth the machinery.

**Engine hooks needed:** spend on use (and refuse if insufficient), apply `resource_change`
in `effects_engine`, refill on safe-room entry, tick per-turn drift.

### 10. Equipment: a slot *list* per item, and consumables aren't a slot

**Problem.** Items carry one slot from a fixed enum (`weapon`/`armor`/`trinket`/
`utility`/`consumable`/`none`), so there is exactly one armor slot — a helmet, a
breastplate, and boots all evict each other. The whole gear game is three items, which
undercuts loot as a reward no matter how specific the AI's inventions are.

**Decided — slots are a list.** An item declares which slots it occupies; equipping it
clears anything in any of them.

| item | slots |
|---|---|
| sword | `["hand"]` |
| greatsword / bow | `["hand", "hand"]` |
| bunny costume | `["body", "legs"]` |
| full plate | `["head", "body", "legs", "feet"]` |
| ring | `["accessory"]` |
| potion | `[]` — not equippable |

**Capacities:** 2 hand, 1 head, 1 body, 1 legs, 1 feet, 2 accessory.

**Consumable stops being a slot.** The current `slot` field does double duty — it says where
an item is worn *and* whether it's usable, since `use_item` works by checking
`slot == "consumable"`. Split into what they actually are:
- `slots` — where it's worn, possibly empty
- `use_effects` — what happens when used

A potion has no slots and has use effects. A sword has slots and none. A wand can have both:
wield it and zap it. "Drink potion" and "unlock door with key" then work straight from
inventory with no equipping step.

**Engine consequence:** equipping can now unequip *several* items at once (a greatsword
removes both sword and shield), so the "what you just took off" message carries more weight.

### 10a. Hands: item declares a count, engine assigns the side

Slots are named uniquely (`r_hand`, `l_hand`, `head`, `body`, `legs`, `feet`,
`accessory_1`, `accessory_2`) so each has capacity 1 and equipping is a set operation
rather than counting duplicates.

But the **item declares how many hands it needs, not which** — otherwise generation has to
pick a side for every sword, which is arbitrary and produces two swords both claiming
`r_hand`. The engine assigns a free side; the UI still shows "R hand: sword / L hand:
shield." A two-handed weapon claims both and locks out an offhand.

### 12. Speed means dodge (plus fleeing and agility checks)

**Problem.** `speed` is loaded into the combatant object and then never read — combat
resolves purely on attack vs defense. It sits on the character sheet, the AI sets it at
chargen, gear modifies it, and it changes nothing in a fight.

**Rejected: action points / battle timer.** "A slow enemy doesn't respond every turn" needs
accumulated time per combatant, persisted between actions, plus rules for partial turns when
you leave a room. Real system, poor payoff.

**Rejected: initiative.** Dead on arrival given #5 — rooms are safe to enter, so the player
always opens the fight by construction. There's no initiative to win.

**Decided: dodge, relative to both speeds.**

```
hit chance = 0.6 + 0.05 × (attacker.attack − defender.defense)
                 + 0.03 × (attacker.speed  − defender.speed)
```
still clamped to 10–95%. One term in an existing formula — no new state, and it works
symmetrically for players and enemies since both run through the same function.

Keying off both speeds (not the defender's alone) means a fast attacker lands hits on slow
targets, and stacking speed can't make anything untouchable because a fast enemy cancels it.

Gives speed a clean identity beside defense: **defense reduces how hard you're hit, speed
reduces whether you're hit at all.**

*Tuning note:* speed now contributes on offense and defense both, so per point it's worth
slightly more than attack or defense. That's why its coefficient is lower (0.03 vs 0.05). If
it becomes the obvious dump stat once the AI sets stat lines freely, drop it to 0.02.

**Speed's three uses:** dodge in combat, escape when fleeing (#5), agility in adjudication
checks.

### 11. `@item` references with autocomplete

Typing `@` in the command box offers inventory items and completes them.

Not just convenience — a **reliability fix**. Item matching currently does fuzzy substring
comparison in `RunContext.find_item`, so "use potion" with three potions in the bag picks
whichever sorts first. An `@` reference resolves to a specific inventory row, removing the
ambiguity entirely and taking load off the parser tier.

### 13. A floor is one flavour; the floor plan file changes shape

**Decided.** One coherent theme per floor. The AI varies rooms *within* it at generation
time, but there are no sub-regions. Distance bands were considered and rejected — a floor
reads as a single place.

**Frontmatter changes:**
- **Removed:** `grid` (width/height/landing) — floors are infinite.
- **Replaced:** `stairs.min_distance_from_landing` → the chance ramp + forced ceiling (#3).
- **Replaced:** `shop` → safe-room frequency (#8).
- **Repurposed:** `power_budget` survives but inverts meaning — advisory guidance the AI
  references, no longer clamps that reject its work (#4).

**Body changes:** the current *Layout Intent* section ("west half flooded galleries, east
half dry maintenance tunnels") is dead. It was written for a 7×7 grid; in unbounded space
there is no west half. Replace with flavour that doesn't assume geography.

**Floor 1's theme is wrong and should be rewritten.** The current file is a glowing
alchemical sewer — my invention. Floor 1 should read like DCC's first floor: passages and
tunnels hewn out of rock below the surface, largely generic stone corridors, a tutorial
floor meant to thin the herd. Goblins and slimes, monster neighborhoods, safe rooms, and
the first signs the crawl is being staged for an audience.

*Note:* the source describes that floor as "enormous and loosely connected," with
"stairwells difficult to find because the floor is not built as a neat dungeon corridor" —
which is independently what #3 and #8 arrived at. Infinite floors with scattered stairs and
scattered safe rooms is faithful, not a departure.

### 14. No carry limit — delete `bulk`

**Decided.** Inventory is infinite. If it isn't bolted down, you can take it.

Remove the `bulk` field from `Item`, the might-check plumbing that references it in
`GrantsItemSpec`, and its display in the inventory panel. A ruling can still require a
strength check to *lift* something absurd — that's the adjudicator's call at the moment
of the attempt, not a number carried around forever.

## Stretch goals (not in scope now)

- **Fetch quests.** No quest state, objective tracking, or completion conditions exist.
- **Boss battles.** No notion of a boss beyond "an enemy with larger numbers" — no
  arena/lock, no phases, no guaranteed placement.
- **Accomplishments + lootboxes.** The AI decides whether an action earns an
  accomplishment. Definitions are **cached like all world content**, so the first crawler to
  do a thing defines that accomplishment and every later crawler who does the same thing
  earns the same one — consistent experience, and it fits the existing JIT-and-cache pattern
  exactly (an accomplishment is just another thing the world learns once). Each one grants a
  lootbox, **openable only in safe rooms**, which gives safe rooms a second purpose beyond
  shop-and-heal and creates a reason to seek them out rather than only passing through.
- **Theme affecting mechanics.** Rejected for now: theme is a *coherence constraint on
  generation*, not a source of floor-wide rules. A floor's identity comes from everything
  on it being consistent, not from special-case combat rules.

## Confirmed out of scope

Not a MUD. Players never see each other; no shared world events, no raids, no realtime
presence. The only cross-player elements are passive: the leaderboard, and corpses of
dead crawlers appearing in the rooms where they died.

### 3. Floors are infinite; stairs are pseudo-randomly distributed

**Decided.** Remove the fixed grid entirely — no `width`/`height`, no clamped edges, no
map boundary. Coordinates are unbounded; the cache key `(floor, x, y)` already works that
way, so this is mostly deleting the edge-clamping code in `area_gen` and the grid fields
in the floor plan frontmatter.

Stairs down are **distributed pseudo-randomly at interval**, replacing the current
"one fixed cell chosen from a finite candidate set."

**Who decides what:** code holds the dice, the AI holds the shovel. Models don't honor
stated probabilities reliably — tell one "8% chance" a hundred times and the distribution
drifts badly — so a *seeded roll per coordinate* decides whether a room contains stairs.
Seeded on `(floor, x, y)` so it's deterministic: every crawler gets the same world, which
the permanent cache requires anyway. The AI then decides what the stairs look like and how
they're built into the room.

**Escape hatch:** the AI may also place stairs unprompted when it has a good reason. That
kind of crazy is wanted.

**Ramp:** chance climbs with distance from the landing — near zero next to spawn, rising
as you push out. Gives the floor a rough shape without hard bounds. (Assumed default; not
explicitly confirmed.)

**Ceiling: confirmed.** Past a threshold distance, stairs are forced in the next room, so
a bad seed can't produce an endless floor.

### 4. Frontmatter is guidance; the AI has final say

The AI decides *what*, code guarantees the result is *runnable*. Split the current clamps:

- **Balance → advisory.** Stat bands, damage caps, rarity weights become typical ranges
  stated in the prompt. The AI may exceed them when it wants something crazy — an
  out-of-depth horror on floor 1 is the fun, not a bug. Violations are logged, not rejected.
- **Integrity → hard.** Dice strings must parse, HP must be a positive integer, effect
  lists must be finite, recursion must terminate. Breaking these throws mid-combat, which
  isn't crazy, it's broken.

### 5. Rooms are safe to enter; fleeing is contested

Directly downstream of #4: once the dungeon can place anything anywhere, arrival can't be
lethal on its own.

**A room is a prompt.** You enter, you see the situation, you decide. Entering no longer
grants enemies a free turn — today `move` calls `enemy_turns()` immediately, so you eat a
hit on arrival before any input. That goes away.

**There is no combat mode.** Freeform text throughout — "attack goblin," "cast magic
missile," and "launch a bomb and hide behind the rock" all enter the same pipeline; the
first two hit the keyword fast path, the third goes to adjudication. (Already true in the
build; recorded because it's a deliberate property, not an accident.)

**Fleeing is contested.** Currently `do_flee` always succeeds — it picks an open exit and
walks you out. It becomes a speed-based roll against what's in the room, with the AI able
to override when the situation dictates (cornered in a dead end, something already holding
your leg).

These two are load-bearing together: if entry is safe *and* escape is free, nothing can
ever hurt a player who doesn't choose to engage. Contested flight is what makes walking
into a bad room a real risk.

### 6. Keep the six effect verbs — and tell the model it's constrained by them

**Decided.** The mechanical vocabulary stays small and fixed: `damage`, `heal`,
`damage_over_time`, `stat_modifier`, `resource_change`, `on_hit_trigger`. No open-ended
state verb, no expanded list. Every spell, item, enemy power, and adjudicated outcome
composes from these. (`resource_change` is the one addition since this was written —
decision 9 introduced it along with the class resource, which amends the list here.)

**The schema already enforces this** — the discriminated union means the model physically
cannot emit a seventh effect type.

**The gap is narration outrunning mechanics.** Nothing currently stops an adjudication from
writing "the goblin is now your ally" while returning an empty effects list. The text claims
an outcome the engine never applied, and the goblin keeps attacking. The game contradicts
itself.

**Fix (prompt-side, not schema-side):** state the vocabulary and its boundary explicitly in
the generation prompts, and require that narration never assert an outcome the effects don't
produce. When a player attempts something outside the six verbs, the ruling must either map
it onto what *is* expressible, or return the `impossible` verdict the adjudicator already
supports. It must not fake it.


---

## Implementation notes

Things that only emerged while building:

**Schema size is a hard provider limit.** Gemini rejected the class schema outright —
*"produces a constraint that has too many states for serving"*. Two causes: the strict
converter was echoing kept numeric bounds into `description` (pure duplication), and the
`Effect` union nested inside ability arrays multiplies decoder states fast. Fixed by only
prosifying *stripped* constraints, capping descriptions at 180 chars, and trimming
`starting_abilities`/`starting_items` to 2 and ability `effects` to 2. Watch this when
adding fields to any generated model.

**`properties` is not a schema node.** The converter originally walked it as one, so a field
literally named `description` got sliced as a string, and a field named `pattern` would have
been silently stripped. `properties`/`$defs` are now walked as name→schema maps.

**The fast path must decline ambiguity.** `use X` matched any text beginning with "use", so
*"use the ropes to trip the goblin"* became a failed inventory lookup instead of reaching
adjudication — quietly defeating the freeform premise. Verb patterns now only fast-path when
the argument really names an inventory item or known ability. Also `take off X` was being
eaten by the `take` pattern.

**Assistant-speak leaks into rejection lines.** The first generated response bank contained
*"I'm sorry, I cannot respond to that."* — a chatbot breaking character. Both the
response-bank prompt and the parser gatekeeper now explicitly ban that register. Re-check
whenever those prompts change.

**The landing room needs marking visited explicitly.** `create_run` doesn't go through
`enter_area`, so without it the starting room never appeared on the minimap and
rooms-explored started at 0.
