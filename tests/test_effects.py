from pydantic import TypeAdapter

from app.engine.effects_engine import Combatant, apply_effects, tick_statuses
from app.engine.rng import RunRNG
from app.models.effects import Effect

adapter = TypeAdapter(list[Effect])


def _effects(data):
    return adapter.validate_python(data)


def test_apply_damage_and_dot():
    rng = RunRNG(seed=7, counter=0)
    a = Combatant(name="A", hp=20, max_hp=20, attack=5, defense=2, speed=3)
    b = Combatant(name="B", hp=20, max_hp=20, attack=5, defense=2, speed=3)
    apply_effects(_effects([
        {"type": "damage", "amount": "1d4", "damage_type": "physical"},
        {"type": "damage_over_time", "per_turn": "2", "turns": 2, "damage_type": "acid"},
    ]), a, b, rng)
    assert b.hp < 20
    hp = b.hp
    tick_statuses(b, rng)
    assert b.hp == hp - 2
    tick_statuses(b, rng)
    assert not b.statuses


def test_resource_change_routes_through_the_callback():
    """The class resource lives on the run, not on a combatant, so it arrives via a callback."""
    rng = RunRNG(seed=3, counter=0)
    c = Combatant(name="A", hp=10, max_hp=10, attack=1, defense=1, speed=1)
    seen = []

    def resource_fn(amount):
        seen.append(amount)
        return amount, "Rage"

    log = apply_effects(_effects([{"type": "resource_change", "amount": 15}]),
                        c, c, rng, resource_fn=resource_fn)
    assert seen == [15]
    assert "Rage" in log[0]


def test_resource_change_is_silent_without_a_resource():
    rng = RunRNG(seed=3, counter=0)
    c = Combatant(name="A", hp=10, max_hp=10, attack=1, defense=1, speed=1)
    assert apply_effects(_effects([{"type": "resource_change", "amount": 5}]), c, c, rng) == []


def test_the_resource_callback_has_exactly_one_definition():
    """It was copied character for character into combat and loot, because combat imports
    loot and so loot could not import it back. RunContext owns the resource, so it owns
    the callback and the cycle stops mattering."""
    from app.engine import combat, loot
    from app.engine.state import RunContext

    assert callable(RunContext.resource_fn)
    for module in (combat, loot):
        assert not hasattr(module, "_resource_fn"), (
            f"{module.__name__} still defines its own copy; call ctx.resource_fn() instead")

    ctx = RunContext(run={"stats": {"resource": {"name": "Rage", "current": 2, "max": 10}}},
                     rng=RunRNG(seed=1, counter=0))
    assert ctx.resource_fn()(5) == (5, "Rage")
    assert ctx.run["stats"]["resource"]["current"] == 7

    assert RunContext(run={"stats": {}}, rng=RunRNG(seed=1, counter=0)).resource_fn() is None


def test_on_hit_trigger_cannot_nest_another_trigger():
    """The recursion was removed: a self-referential union becomes an unresolvable $ref
    loop under strict structured output."""
    import pytest

    with pytest.raises(Exception):
        adapter.validate_python([{
            "type": "on_hit_trigger", "chance": 1.0,
            "effect": {"type": "on_hit_trigger", "chance": 1.0,
                       "effect": {"type": "damage", "amount": "1d4"}},
        }])


def test_on_hit_trigger_can_fire_a_resource_change():
    """'My attacks build Rage' needs no passive trigger system."""
    rng = RunRNG(seed=1, counter=0)
    c = Combatant(name="A", hp=10, max_hp=10, attack=1, defense=1, speed=1)
    got = []
    apply_effects(_effects([{
        "type": "on_hit_trigger", "chance": 1.0,
        "effect": {"type": "resource_change", "amount": 10},
    }]), c, c, rng, resource_fn=lambda a: (got.append(a) or a, "Rage"))
    assert got == [10]


def test_rng_determinism():
    r1, r2 = RunRNG(seed=99, counter=0), RunRNG(seed=99, counter=0)
    assert [r1.randint(1, 100) for _ in range(10)] == [r2.randint(1, 100) for _ in range(10)]
