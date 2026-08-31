"""Shared wild-encounter loop for the hunt and catch tasks.

Both tasks do the same thing up to a point: pace the grass, look at what
appeared, and decide. The important detail is *when* it is safe to look --
wEnemyMon is only populated once the battle is asking for a decision, so the
species read has to wait for the battle menu rather than for wBattleMode.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..battle import BattleEngine, BattlePolicy


@dataclass
class SearchStats:
    encounters: int = 0
    fled: int = 0
    fought: int = 0
    seen: dict = field(default_factory=dict)

    def note_seen(self, name: str) -> None:
        self.seen[name] = self.seen.get(name, 0) + 1

    def top_seen(self, n: int = 6) -> str:
        pairs = sorted(self.seen.items(), key=lambda kv: -kv[1])[:n]
        return ", ".join(f"{k} x{v}" for k, v in pairs) or "nothing"


class WildSearch:
    """Walks grass and hands each wild encounter to a predicate."""

    def __init__(self, session, reader, control, nav, gamedata, traveler,
                 log=print):
        self.s = session
        self.r = reader
        self.c = control
        self.n = nav
        self.gd = gamedata
        self.trav = traveler
        self.log = log
        # Used to leave encounters we do not want, and to survive the ones we
        # cannot escape from.
        self.escape = BattleEngine(session, reader, control, gamedata,
                                   BattlePolicy(always_flee=True), log=log)
        self.fight = BattleEngine(session, reader, control, gamedata,
                                  BattlePolicy(), log=log)

    def ensure_grass(self, route_key) -> bool:
        if self.r.in_battle():
            return True
        if self.r.location().key != route_key:
            want = self.gd.map_name(*route_key)
            if not self.trav.travel_to(want):
                return False
        return self.n.find_grass()

    def next_encounter(self, route_key, stats: SearchStats):
        """Walk until a wild battle is up and readable. Returns the BattleState."""
        if not self.r.in_battle():
            if not self.ensure_grass(route_key):
                return None
            self.n.pace_until_battle(max_steps=500)
        if not self.r.in_battle():
            return None
        # Wait for the game to ask for a decision: only then are the battle
        # structs populated.
        what = self.fight.next_decision()
        if what in ("ended", "timeout"):
            return None
        battle = self.r.battle()
        if not battle.ready:
            return None
        stats.encounters += 1
        stats.note_seen(battle.enemy_name)
        return battle

    def leave(self, stats: SearchStats, menu_open: bool = True) -> None:
        """Get out of the current encounter, fighting only if escape fails.

        `menu_open` defaults True because callers reach here straight after
        next_encounter(), which has already pumped to the battle menu.
        """
        if not self.r.in_battle():
            return
        out = self.escape.run(target_slot=None, max_turns=25,
                              menu_open=menu_open)
        if out.result == "fled":
            stats.fled += 1
        elif self.r.in_battle():
            # Could not run: win it rather than stand there losing HP.
            self.fight.run(target_slot=None, max_turns=40, menu_open=True)
            stats.fought += 1
        else:
            stats.fought += 1
        self.n.settle()

    def party_needs_heal(self, heal_below: float) -> bool:
        party = self.r.party()
        if not party:
            return False
        lead = next((m for m in party if not m.fainted), None)
        if lead is None:
            return True
        return lead.hp_frac < heal_below

    def heal(self, route_key) -> bool:
        if not self.trav.heal_round_trip():
            return False
        return self.ensure_grass(route_key)
