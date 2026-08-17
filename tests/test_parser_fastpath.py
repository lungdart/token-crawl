from app.engine.parser import fast_parse

KEYS = ["tunnel_goblin", "frayed_ropes"]
INV = ["Healing Draught", "Sharpened Pry-Bar"]
ABILITIES = ["Blood Bolt"]


def fp(text, **kw):
    return fast_parse(text, KEYS, kw.pop("in_shop", False),
                      kw.pop("inventory", INV), kw.pop("abilities", ABILITIES))


def test_directions():
    assert fp("north").type == "move"
    assert fp("n").direction == "n"
    assert fp("go west").direction == "w"


def test_attack_matches_entity():
    a = fp("attack goblin")
    assert a.type == "attack" and a.target_key == "tunnel_goblin"


def test_attack_unknown_falls_through():
    assert fp("attack the concept of debt") is None


def test_take_and_use():
    assert fp("take ropes").target_key == "frayed_ropes"
    assert fp("use healing draught").item_name == "healing draught"
    assert fp("equip pry-bar").item_name == "pry-bar"


def test_use_only_matches_a_real_item():
    """Otherwise freeform text starting with a verb never reaches adjudication —
    'use the ropes to trip the goblin' is a novel action, not an item lookup."""
    assert fp("use the frayed ropes to trip the goblin") is None
    assert fp("use my considerable charm on it") is None


def test_cast_only_matches_a_known_ability():
    assert fp("cast blood bolt").type == "use_ability"
    assert fp("cast a wide net over the pit") is None


def test_at_reference_always_fast_paths():
    """An @reference comes from the autocomplete, so it names a real inventory row."""
    a = fp("use @Healing Draught")
    assert a.type == "use_item" and a.item_name.lstrip("@") == "healing draught"


def test_unequip():
    assert fp("unequip pry-bar").type == "unequip"
    assert fp("take off pry-bar").type == "unequip"


def test_shop_commands_only_in_shop():
    assert fp("buy 2") is None
    a = fp("buy 2", in_shop=True)
    assert a.type == "shop_buy" and a.slot_index == 1


def test_misc():
    assert fp("inventory").type == "inventory"
    assert fp("flee").type == "flee"
    assert fp("descend").type == "descend"
    # "look" is not a command any more: the room is always on screen, and "look at the
    # tally marks" is an examine that belongs with the adjudicator, not a keyword.
    assert fp("look") is None
    assert fp("look at the tally marks") is None


def test_freeform_falls_through_to_llm_tier():
    assert fp("pry the gem out of the altar") is None
    assert fp("launch a bomb and hide behind the rock") is None
