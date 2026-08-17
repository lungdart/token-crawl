"""The sole interpreter of Effect lists. Data in, state deltas out. No eval, ever."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.engine import dice
from app.engine.rng import RunRNG


@dataclass
class ActiveStatus:
    """A DoT or timed stat modifier attached to a combatant."""
    kind: str  # "dot" | "stat_mod"
    per_turn: str = "0"
    damage_type: str = "physical"
    stat: str = ""
    amount: int = 0
    turns_left: int = 0


@dataclass
class Combatant:
    """Runtime view of anything that can fight. Built from run rows / stat blocks."""
    name: str
    hp: int
    max_hp: int
    attack: int
    defense: int
    speed: int
    attack_dice: str = "1d4"
    statuses: list[ActiveStatus] = field(default_factory=list)

    def eff(self, stat: str) -> int:
        base = getattr(self, stat)
        bonus = sum(s.amount for s in self.statuses if s.kind == "stat_mod" and s.stat == stat)
        return max(0, base + bonus)

    @property
    def alive(self) -> bool:
        return self.hp > 0


def apply_effects(effects: list, source: Combatant, target: Combatant, rng: RunRNG,
                  depth: int = 0, resource_fn=None) -> list[str]:
    """Apply a validated Effect list; returns narration fragments.

    `resource_fn(amount) -> (delta, name)` applies a change to the class resource, which
    lives on the run rather than on a combatant. Absent for enemies, which have none.
    """
    log: list[str] = []
    if depth > 2:
        return log
    for e in effects:
        t = e.type
        if t == "damage":
            dmg = max(1, dice.roll(e.amount, rng.next()))
            target.hp -= dmg
            log.append(f"{target.name} takes {dmg} {e.damage_type} damage.")
        elif t == "heal":
            amt = dice.roll(e.amount, rng.next())
            healed = min(amt, source.max_hp - source.hp)
            source.hp += healed
            log.append(f"{source.name} recovers {healed} HP.")
        elif t == "damage_over_time":
            target.statuses.append(ActiveStatus(
                kind="dot", per_turn=e.per_turn, damage_type=e.damage_type, turns_left=e.turns,
            ))
            log.append(f"{target.name} is afflicted ({e.damage_type}, {e.turns} turns).")
        elif t == "stat_modifier":
            who = source if e.amount > 0 else target
            who.statuses.append(ActiveStatus(
                kind="stat_mod", stat=e.stat, amount=e.amount,
                turns_left=e.turns if e.turns is not None else 999,
            ))
            sign = "+" if e.amount > 0 else ""
            log.append(f"{who.name}: {sign}{e.amount} {e.stat}.")
        elif t == "resource_change":
            if resource_fn is not None:
                delta, name = resource_fn(e.amount)
                if delta:
                    log.append(f"{name} {'+' if delta > 0 else ''}{delta}.")
        elif t == "on_hit_trigger":
            if rng.random() <= e.chance:
                log.extend(apply_effects([e.effect], source, target, rng, depth + 1, resource_fn))
    return log


def tick_statuses(c: Combatant, rng: RunRNG) -> list[str]:
    """End-of-round tick: DoT damage, status expiry."""
    log: list[str] = []
    remaining: list[ActiveStatus] = []
    for s in c.statuses:
        if s.kind == "dot":
            dmg = max(1, dice.roll(s.per_turn, rng.next()))
            c.hp -= dmg
            log.append(f"{c.name} suffers {dmg} {s.damage_type} damage.")
        s.turns_left -= 1
        if s.turns_left > 0:
            remaining.append(s)
    c.statuses = remaining
    return log
