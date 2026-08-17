"""Turn-based combat: the crawler acts, living enemies respond, statuses tick, death checks.

Entering a room never triggers enemy turns (design review #5) — a room is a prompt. Enemies
respond only to something the crawler does.

    hit chance = 0.6 + 0.05 * (attack − defense) + 0.03 * (speed − speed), clamped 10–95%

Speed is dodge: defense reduces how hard you're hit, speed reduces whether you're hit at all.
Keyed off both speeds so a fast attacker lands on slow targets and stacking speed can't make
anything untouchable.
"""
from __future__ import annotations

from app.engine import dice, loot, progression
from app.engine.effects_engine import ActiveStatus, Combatant, apply_effects, tick_statuses
from app.engine.state import RunContext
from app.world import repo

ATTACK_WEIGHT = 0.05
SPEED_WEIGHT = 0.03


def hit_chance(attacker: Combatant, defender: Combatant) -> float:
    raw = (0.6
           + ATTACK_WEIGHT * (attacker.eff("attack") - defender.eff("defense"))
           + SPEED_WEIGHT * (attacker.eff("speed") - defender.eff("speed")))
    return max(0.10, min(0.95, raw))


def _hit(attacker: Combatant, defender: Combatant, rng) -> bool:
    return rng.random() <= hit_chance(attacker, defender)


def _resource_fn(ctx: RunContext):
    res = ctx.resource
    if not res:
        return None
    return lambda amount: (ctx.change_resource(amount), res["name"])


def _enemy_combatants(ctx: RunContext) -> list[tuple[dict, Combatant]]:
    """Materialize per-run enemy instances for the current area (lazily created)."""
    combat = ctx.run["combat"]
    if combat is None or combat.get("area_id") != ctx.run["area_id"]:
        combat = {"area_id": ctx.run["area_id"], "enemies": {}}
    out = []
    for e in ctx.visible_entities():
        if e.kind != "enemy":
            continue
        found = repo.ready_enemy(ctx.run["floor_id"], e.key)
        if found is None:
            continue
        block = found[0]
        inst = combat["enemies"].setdefault(e.key, {"hp": block.hp, "statuses": []})
        c = Combatant(
            name=block.name, hp=inst["hp"], max_hp=block.hp,
            attack=block.attack, defense=block.defense, speed=block.speed,
            attack_dice=block.attack_dice,
            statuses=[ActiveStatus(**s) for s in inst["statuses"]],
        )
        out.append(({"key": e.key, "block": block}, c))
    ctx.run["combat"] = combat
    return out


def _store_enemy(ctx: RunContext, key: str, c: Combatant) -> None:
    ctx.run["combat"]["enemies"][key] = {
        "hp": c.hp,
        "statuses": [vars(s) for s in c.statuses if s.turns_left > 0],
    }


def _kill(ctx: RunContext, key: str, block) -> None:
    st = ctx.area_state()
    st["killed"].append(key)
    ctx.save_area_state(st)
    ctx.run["combat"]["enemies"].pop(key, None)
    ctx.run["kills"] += 1
    ctx.say(f"{block.name} goes down.", "combat")
    gold = dice.roll(block.gold, ctx.rng.next())
    if gold > 0:
        ctx.run["gold"] += gold
        ctx.say(f"You collect {gold} gold.", "loot")
    loot.roll_drop(ctx, key, block)
    progression.grant_xp(ctx, block.xp)


def enemy_turns(ctx: RunContext) -> None:
    """Every living enemy in the area acts, then statuses tick, then death checks."""
    if ctx.run["status"] != "alive":
        return
    player = ctx.player_combatant()
    for meta, enemy in _enemy_combatants(ctx):
        if not enemy.alive or not player.alive:
            continue
        block = meta["block"]
        acted = False
        for ab in block.abilities:
            if ctx.rng.random() <= ab.chance:
                ctx.say(f"{block.name}: {ab.name}. {ab.flavor}", "combat")
                for line in apply_effects(ab.effects, enemy, player, ctx.rng):
                    ctx.say(line, "combat")
                acted = True
                break
        if not acted:
            if _hit(enemy, player, ctx.rng.next()):
                dmg = max(1, dice.roll(enemy.attack_dice, ctx.rng.next()))
                player.hp -= dmg
                ctx.say(f"{block.name} hits you for {dmg}.", "combat")
            else:
                ctx.say_line("enemy_miss", "combat")
        for line in tick_statuses(enemy, ctx.rng):
            ctx.say(line, "combat")
        if not enemy.alive:
            _kill(ctx, meta["key"], block)
        else:
            _store_enemy(ctx, meta["key"], enemy)
    for line in tick_statuses(player, ctx.rng):
        ctx.say(line, "combat")
    _tick_turn(ctx, player)
    ctx.store_player(player)
    check_death(ctx)


def _tick_turn(ctx: RunContext, player: Combatant) -> None:
    cds = ctx.run["stats"].get("cooldowns", {})
    ctx.run["stats"]["cooldowns"] = {k: v - 1 for k, v in cds.items() if v > 1}
    res = ctx.resource
    if res and res.get("per_turn"):
        ctx.change_resource(res["per_turn"])


def check_death(ctx: RunContext) -> None:
    if ctx.run["hp"] <= 0 and ctx.run["status"] == "alive":
        enemies = [e.name for e in ctx.visible_entities() if e.kind == "enemy"]
        cause = f"killed by {enemies[0].lower()}" if enemies else "misadventure"
        ctx.run["status"] = "dead"
        ctx.run["death_area_id"] = ctx.run["area_id"]
        ctx.run["death_cause"] = cause
        ctx.say(f"You die. Cause of death: {cause}.", "death")


def do_attack(ctx: RunContext, target_key: str) -> None:
    pairs = {m["key"]: (m, c) for m, c in _enemy_combatants(ctx)}
    if target_key not in pairs:
        ctx.say_line("nothing_there")
        return
    meta, enemy = pairs[target_key]
    block = meta["block"]
    player = ctx.player_combatant()
    if _hit(player, enemy, ctx.rng.next()):
        dmg = max(1, dice.roll(player.attack_dice, ctx.rng.next()))
        enemy.hp -= dmg
        ctx.say(f"You hit {block.name} for {dmg}.", "combat")
        weapon = ctx.equipped_weapon()
        if weapon:
            triggers = [e for e in weapon["item"].equip_effects if e.type == "on_hit_trigger"]
            for line in apply_effects(triggers, player, enemy, ctx.rng, resource_fn=_resource_fn(ctx)):
                ctx.say(line, "combat")
    else:
        ctx.say_line("player_miss", "combat")
    if not enemy.alive:
        _kill(ctx, target_key, block)
    else:
        _store_enemy(ctx, target_key, enemy)
    ctx.store_player(player)
    enemy_turns(ctx)


def known_abilities(ctx: RunContext) -> list:
    cls = repo.get_class(ctx.run["class_id"])
    abilities = list(cls.starting_abilities)
    for lvl in repo.level_ups_up_to(ctx.run["class_id"], ctx.run["level"]):
        if lvl.new_ability:
            abilities.append(lvl.new_ability)
    return abilities


def do_use_ability(ctx: RunContext, ability_name: str) -> None:
    name_l = ability_name.strip().lstrip("@").lower()
    ability = next((a for a in known_abilities(ctx)
                    if a.name.lower() == name_l or name_l in a.name.lower()), None)
    if ability is None:
        ctx.say(f"You don't know anything called '{ability_name}'.")
        return

    cds = ctx.run["stats"].setdefault("cooldowns", {})
    if cds.get(ability.name, 0) > 0:
        ctx.say_line("ability_on_cooldown", "combat")
        return
    if ability.resource_cost and not ctx.spend_resource(ability.resource_cost):
        ctx.say_line("resource_too_low", "combat")
        return
    if ability.hp_cost and ctx.run["hp"] <= ability.hp_cost:
        ctx.say("You don't have the life left to pay for that.", "combat")
        return

    player = ctx.player_combatant()
    if ability.hp_cost:
        player.hp -= ability.hp_cost
        ctx.say(f"It costs you {ability.hp_cost} HP.", "combat")

    enemies = _enemy_combatants(ctx)
    target_meta, target = (enemies[0] if enemies else (None, player))
    ctx.say(f"{ability.name}. {ability.flavor}", "combat")
    for line in apply_effects(ability.effects, player, target, ctx.rng, resource_fn=_resource_fn(ctx)):
        ctx.say(line, "combat")
    if ability.cooldown > 0:
        cds[ability.name] = ability.cooldown + 1
    if target_meta is not None:
        if not target.alive:
            _kill(ctx, target_meta["key"], target_meta["block"])
        else:
            _store_enemy(ctx, target_meta["key"], target)
    ctx.store_player(player)
    enemy_turns(ctx)


def do_flee(ctx: RunContext) -> None:
    """Contested (design review #5). Entry is safe and healing is free, so escape being
    uncertain is the only thing keeping a bad room dangerous."""
    content = ctx.area()
    dirs = content.exits.open_dirs()
    if not dirs:
        ctx.say_line("flee_failed", "combat")
        enemy_turns(ctx)
        return

    enemies = _enemy_combatants(ctx)
    if not enemies:
        d = dirs[ctx.rng.next().randrange(len(dirs))]
        from app.engine.movement import do_move
        do_move(ctx, d)
        return

    player = ctx.player_combatant()
    fastest = max(e.eff("speed") for _, e in enemies)
    chance = max(0.15, min(0.9, 0.5 + 0.07 * (player.eff("speed") - fastest)))
    if ctx.rng.random() <= chance:
        d = dirs[ctx.rng.next().randrange(len(dirs))]
        ctx.say("You break away.", "combat")
        from app.engine.movement import do_move
        do_move(ctx, d)
    else:
        ctx.say_line("flee_failed", "combat")
        enemy_turns(ctx)
