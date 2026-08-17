"""Drops (JIT item minting on first slot landing), take, use, equip, and safe-room trade."""
from __future__ import annotations

from app import db
from app.engine import dice
from app.engine.effects_engine import apply_effects
from app.engine.state import RunContext
from app.gen import services
from app.world import repo


def _resource_fn(ctx: RunContext):
    res = ctx.resource
    if not res:
        return None
    return lambda amount: (ctx.change_resource(amount), res["name"])


def roll_drop(ctx: RunContext, name_key: str, block) -> None:
    """First kill of a type generates the slot manifest; a roll landing on an empty slot
    generates that slot's item, which is then identical for everyone."""
    services.ensure_drop_slots(ctx.run["floor_id"], name_key, block.name, block.flavor)
    etid = repo.enemy_type_id(ctx.run["floor_id"], name_key)
    marker, slots = repo.ready_drop_slots(etid)
    total = marker["nothing_weight"] + sum(s["weight"] for s in slots)
    if total <= 0:
        return
    pick = ctx.rng.next().randrange(total)
    if pick < marker["nothing_weight"]:
        return
    pick -= marker["nothing_weight"]
    for s in slots:
        if pick < s["weight"]:
            item_id = services.resolve_drop_slot_item(
                ctx.run["floor_id"], s, block.name, block.flavor)
            qty = max(1, dice.roll(s["qty_dice"], ctx.rng.next()))
            item = repo.get_item(item_id)
            ctx.add_item(item_id, qty)
            suffix = f" x{qty}" if qty > 1 else ""
            ctx.say(f"It drops {item.name}{suffix} ({item.rarity}).", "loot")
            return
        pick -= s["weight"]


def do_take(ctx: RunContext, target_key: str) -> None:
    entity = next((e for e in ctx.visible_entities() if e.key == target_key), None)
    if entity is None:
        ctx.say_line("nothing_there")
        return
    if entity.kind == "item":
        content = ctx.area()
        item_id = services.generate_item(
            ctx.run["floor_id"], f"{entity.name}: {entity.brief}", "common",
            "found in a room",
            name_key=f"world_{ctx.run['area_id']}_{entity.key}",
            context=f"{content.name}: {content.description}",
        )
        item = repo.get_item(item_id)
        ctx.add_item(item_id)
        st = ctx.area_state()
        st["taken"].append(entity.key)
        ctx.save_area_state(st)
        ctx.say(f"You take the {item.name}.", "loot")
    elif entity.kind == "enemy":
        ctx.say(f"{entity.name} is not something you can pick up.")
    else:
        # Taking a fixture is a novel action — route it through adjudication.
        from app.engine import resolver
        resolver.handle_novel(ctx, "pick_up", entity.key, f"take the {entity.name}")


def do_use_item(ctx: RunContext, item_name: str) -> None:
    entry = ctx.find_item(item_name)
    if entry is None:
        ctx.say_line("item_not_held")
        return
    item = entry["item"]
    if not item.usable:
        ctx.say(f"The {item.name} does nothing when used.")
        return
    player = ctx.player_combatant()
    ctx.say(f"You use the {item.name}.", "loot")
    for line in apply_effects(item.use_effects, player, player, ctx.rng,
                              resource_fn=_resource_fn(ctx)):
        ctx.say(line, "combat")
    ctx.store_player(player)
    if item.consumed_on_use:
        ctx.remove_item(entry["inv_id"])
    from app.engine.combat import enemy_turns
    enemy_turns(ctx)


def do_equip(ctx: RunContext, item_name: str) -> None:
    entry = ctx.find_item(item_name)
    if entry is None:
        ctx.say_line("item_not_held")
        return
    item = entry["item"]
    if not item.equippable:
        ctx.say_line("not_equippable")
        return
    if entry["equipped"]:
        ctx.say(f"The {item.name} is already equipped.")
        return

    wanted = ctx.assign_slots(item)
    if wanted is None:
        ctx.say_line("not_equippable")
        return

    # Equipping can displace several things at once — a two-handed weapon takes off both
    # a sword and a shield — so say what came off.
    occupied = ctx.occupied_slots()
    displaced = {occupied[s]["inv_id"]: occupied[s]["item"].name for s in wanted if s in occupied}
    for inv_id in displaced:
        ctx.set_equipped(inv_id, [])
    ctx.set_equipped(entry["inv_id"], wanted)

    ctx.say(f"You equip the {item.name} ({', '.join(wanted)}).", "loot")
    if displaced:
        ctx.say(f"You take off: {', '.join(sorted(displaced.values()))}.", "loot")


def do_unequip(ctx: RunContext, item_name: str) -> None:
    entry = ctx.find_item(item_name)
    if entry is None or not entry["equipped"]:
        ctx.say_line("item_not_held")
        return
    ctx.set_equipped(entry["inv_id"], [])
    ctx.say(f"You take off the {entry['item'].name}.", "loot")


# --- safe-room trade ---------------------------------------------------------

def _safe_room_row(ctx: RunContext):
    row = ctx.area_row()
    return row if row["is_safe_room"] else None


def do_sell(ctx: RunContext, item_name: str) -> None:
    if _safe_room_row(ctx) is None:
        ctx.say_line("no_safe_room")
        return
    entry = ctx.find_item(item_name)
    if entry is not None and entry["equipped"]:
        name_l = entry["item"].name.lower()
        alt = next((e for e in ctx.inventory()
                    if not e["equipped"] and e["item"].name.lower() == name_l), None)
        entry = alt or entry
    if entry is None:
        ctx.say_line("item_not_held")
        return
    if entry["equipped"]:
        ctx.say("Take it off first.")
        return
    price = max(1, entry["item"].value_gold // 2)
    ctx.remove_item(entry["inv_id"])
    ctx.run["gold"] += price
    ctx.say(f"You sell the {entry['item'].name} for {price} gold.", "loot")


def do_buy(ctx: RunContext, slot_index: int) -> None:
    row = _safe_room_row(ctx)
    if row is None:
        ctx.say_line("no_safe_room")
        return
    rows = repo.safe_room_stock(row["id"])
    if slot_index < 0 or slot_index >= len(rows):
        ctx.say("There is nothing on the counter with that number.")
        return
    slot = rows[slot_index]
    if ctx.run["gold"] < slot["price"]:
        ctx.say_line("cannot_afford")
        return
    found = repo.ready_safe_room(row["id"])
    keeper = found[0].keeper_name if found else ""
    item_id = services.resolve_stock_item(ctx.run["floor_id"], slot, keeper)
    item = repo.get_item(item_id)
    ctx.run["gold"] -= slot["price"]
    ctx.add_item(item_id)
    ctx.say(f"You buy the {item.name} for {slot['price']} gold. {item.flavor}", "loot")
