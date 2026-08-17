"""The action pipeline: gate -> parse -> resolve -> enemy response -> persist.

Entering a room does NOT provoke enemies (design review #5). They respond only to something
the crawler actually does, so arriving somewhere lethal is a decision point rather than an
instant death.
"""
from __future__ import annotations

from app import logs
from app.config import settings
from app.engine import combat, loot, movement
from app.engine.parser import parse
from app.engine.state import RunContext, one_turn_at_a_time
from app.gen.llm import GenerationError
from app.gen.locks import GenerationPending
from app.models.actions import Rejected
from app.security import limits
from app.world import repo

log = logs.get(__name__)


def handle_action(run_id: int, raw_text: str, *, session_id: str, ip: str) -> RunContext:
    """One turn. Held one at a time per crawler — see state.one_turn_at_a_time."""
    with one_turn_at_a_time(run_id):
        return _handle_action(run_id, raw_text, session_id=session_id, ip=ip)


def _handle_action(run_id: int, raw_text: str, *, session_id: str, ip: str) -> RunContext:
    ctx = RunContext.load(run_id)
    if ctx.run["status"] != "alive":
        ctx.say("This crawl is over. Re-roll to start another.", "system")
        return ctx
    try:
        limits.check_action(session_id)
    except limits.RateLimited:
        ctx.say_line("rate_limited", "system")
        return ctx
    limits.begin_action(session_id)

    raw_text = raw_text.replace("\x00", "").strip()[: settings.max_action_chars]
    if not raw_text:
        return ctx  # nothing typed; never worth a model call
    content = ctx.area()
    entities = ctx.visible_entities()
    inv = ctx.inventory()

    try:
        action = parse(
            raw_text,
            entity_keys=[e.key for e in entities],
            entity_briefs={e.key: e.brief for e in entities},
            inventory_names=[i["item"].name for i in inv],
            ability_names=[a.name for a in combat.known_abilities(ctx)],
            exits=content.exits.open_dirs(),
            in_combat=any(e.kind == "enemy" for e in entities),
            in_shop=bool(ctx.area_row()["is_safe_room"]),
        )
    except Exception:
        log.exception("parsing failed for run %s on input %r", run_id, raw_text[:120])
        ctx.say(logs.FAULT + " That input could not be read. Try phrasing it differently.",
                "system")
        ctx.persist()
        return ctx

    try:
        _dispatch(ctx, action, session_id)
    except (GenerationError, GenerationPending):
        log.exception("generation failed while resolving %r for run %s",
                      getattr(action, "type", "?"), run_id)
        ctx.say(logs.FAULT + " The dungeon could not finish building that. Try again.",
                "system")
    finally:
        limits.end_action()

    ctx.persist()
    return ctx


def _dispatch(ctx: RunContext, action, session_id: str) -> None:
    t = action.type
    if t == "rejected":
        # The one place the game speaks to the player rather than the character.
        ctx.say(action.refusal or ctx.line("rejected"), "system")
    elif t == "move":
        movement.do_move(ctx, action.direction)  # arriving never provokes anything
    elif t == "descend":
        movement.do_descend(ctx)
    elif t == "attack":
        combat.do_attack(ctx, action.target_key)
    elif t == "use_ability":
        combat.do_use_ability(ctx, action.ability_name)
    elif t == "take":
        loot.do_take(ctx, action.target_key)
        combat.enemy_turns(ctx)
    elif t == "use_item":
        loot.do_use_item(ctx, action.item_name)
    elif t == "equip":
        loot.do_equip(ctx, action.item_name)
        combat.enemy_turns(ctx)
    elif t == "unequip":
        loot.do_unequip(ctx, action.item_name)
        combat.enemy_turns(ctx)
    elif t == "flee":
        combat.do_flee(ctx)
    elif t == "inventory":
        _show_inventory(ctx)
    elif t == "shop_buy":
        loot.do_buy(ctx, action.slot_index)
    elif t == "shop_sell":
        loot.do_sell(ctx, action.item_name)
    elif t == "novel":
        handle_novel(ctx, action.verb_key, action.target_key, action.raw_text)
        combat.enemy_turns(ctx)


def _show_inventory(ctx: RunContext) -> None:
    inv = ctx.inventory()
    ctx.say(f"Gold: {ctx.run['gold']}.", "system")
    res = ctx.resource
    if res:
        ctx.say(f"{res['name']}: {res['current']}/{res['max']}.", "system")
    if not inv:
        ctx.say_line("empty_inventory", "system")
        return
    for entry in inv:
        item = entry["item"]
        mark = f" [{', '.join(entry['slots'])}]" if entry["equipped"] else ""
        qty = f" x{entry['qty']}" if entry["qty"] > 1 else ""
        ctx.say(f"• {item.name}{qty} ({item.rarity}){mark}", "system")


def handle_novel(ctx: RunContext, verb_key: str, target_key: str, raw_text: str) -> None:
    """Tier 3: adjudication. Ruling cached per (area, target, verb); rolls per-crawler."""
    from app.engine.effects_engine import apply_effects
    from app.gen import services

    entities = {e.key: e for e in ctx.visible_entities()}
    if target_key != "_area" and target_key not in entities:
        ctx.say_line("nothing_there")
        return
    target_brief = entities[target_key].brief if target_key in entities else "the room itself"
    content = ctx.area()

    ruling = services.ensure_ruling(
        ctx.run["floor_id"], ctx.run["area_id"], content.description,
        target_key, target_brief, verb_key, raw_text,
    )
    if ruling.rejected:
        ctx.say(ruling.rejection_quip or ctx.line("rejected"), "system")
        return

    rid = repo.ruling_id(ctx.run["area_id"], target_key, verb_key)
    st = ctx.area_state()
    if not ruling.repeatable and rid in st["used_rulings"]:
        ctx.say("You have already done that here.", "system")
        return

    if ruling.success_kind == "impossible":
        ctx.say(ruling.narration_failure or "That cannot be done here.", "narration")
        return

    player = ctx.player_combatant()
    success = True
    if ruling.success_kind == "stat_check":
        stat = ruling.check_stat or "attack"
        roll = ctx.rng.next().randint(1, 20)
        success = roll + player.eff(stat) >= (ruling.difficulty or 10)
        ctx.say(f"[{stat.upper()}: d20({roll}) + {player.eff(stat)} vs {ruling.difficulty}]", "system")

    from app.engine.combat import _resource_fn

    if success:
        ctx.say(ruling.narration_success or "It works.", "narration")
        for line in apply_effects(ruling.effects_on_success, player, player, ctx.rng,
                                  resource_fn=_resource_fn(ctx)):
            ctx.say(line, "combat")
        if ruling.grants_item_spec is not None:
            _grant_item(ctx, ruling, target_key, entities.get(target_key))
    else:
        ctx.say(ruling.narration_failure or "It doesn't work.", "narration")
        for line in apply_effects(ruling.effects_on_failure, player, player, ctx.rng,
                                  resource_fn=_resource_fn(ctx)):
            ctx.say(line, "combat")

    ctx.store_player(player)
    if not ruling.repeatable and rid is not None:
        st = ctx.area_state()  # re-read: _grant_item may have updated 'taken'
        st["used_rulings"].append(rid)
        ctx.save_area_state(st)
    combat.check_death(ctx)


def _grant_item(ctx: RunContext, ruling, target_key: str, entity) -> None:
    from app.gen import services

    spec = ruling.grants_item_spec
    if spec.might_check_difficulty is not None:
        player = ctx.player_combatant()
        roll = ctx.rng.next().randint(1, 20)
        if roll + player.eff("attack") < spec.might_check_difficulty:
            ctx.say("It is too heavy to shift.", "narration")
            return
    name = entity.name if entity else target_key
    content = ctx.area()
    item_id = services.generate_item(
        ctx.run["floor_id"],
        f"{name} — taken out of the room by a crawler. {spec.hint}",
        "common", "claimed from the dungeon itself",
        name_key=f"claimed_{ctx.run['area_id']}_{target_key}",
        context=content.description,
    )
    item = repo.get_item(item_id)
    ctx.add_item(item_id)
    if entity is not None:
        st = ctx.area_state()
        st["taken"].append(target_key)
        ctx.save_area_state(st)
    ctx.say(f"You take the {item.name}.", "loot")
