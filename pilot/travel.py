"""Cross-map travel, and the Pokemon Center round trip that makes unattended
grinding possible.

Routes come from the world graph (edge connections + warps), so this works
anywhere in the game rather than from a hardcoded list of places.
"""
from __future__ import annotations

from .battle import BattleEngine, BattlePolicy


class Traveler:
    def __init__(self, session, reader, control, nav, world, gamedata, log=print):
        self.s = session
        self.r = reader
        self.c = control
        self.n = nav
        self.w = world
        self.gd = gamedata
        self.log = log
        # Anything encountered while travelling is an obstacle, not an
        # opportunity -- we are usually travelling *because* HP is low.
        self._flee = BattleEngine(session, reader, control, gamedata,
                                  BattlePolicy(always_flee=True), log=log)

    def current_const(self) -> str:
        loc = self.r.location()
        return self.gd.map_name(loc.group, loc.number)

    def _handle_battle(self) -> None:
        if self.r.in_battle():
            self._flee.run(target_slot=None, max_turns=25)
            self.n.settle()

    def walk_hop(self, kind: str, target: str, warp: dict | None,
                 attempts: int = 8) -> bool:
        """Execute one BFS hop: an edge crossing or a warp.

        Crossing a route means walking through grass, so wild encounters
        interrupt constantly. Each interruption is fled and the hop retried --
        the walk resumes from wherever it got to, so progress accumulates
        rather than restarting.
        """
        before = self.current_const()
        for _ in range(attempts):
            self._handle_battle()
            if kind == "warp" and warp is not None:
                self.n.take_warp(warp["x"], warp["y"],
                                 on_battle=self._handle_battle)
            else:
                self.n.cross_edge(kind, on_battle=self._handle_battle)
            if self.r.in_battle():
                self._handle_battle()
                if self.current_const() == target:
                    return True
                continue          # retry the hop from where we stopped
            self.n.settle()
            now = self.current_const()
            if now == target:
                return True
            if now != before:
                self.log(f"  travel: ended up in {now}, expected {target}")
                return False
        return self.current_const() == target

    def travel_to(self, dest_const: str, max_hops: int = 14) -> bool:
        """Walk to `dest_const` using the world graph, re-planning after each hop.

        Hops that turn out not to be walkable are remembered and excluded, so the
        search falls back to another way round instead of retrying the same
        impassable link.
        """
        failed: set[tuple[str, str, str]] = set()
        for _ in range(max_hops):
            here = self.current_const()
            if here == dest_const:
                return True
            path = self.w.route_to(here, lambda c: c == dest_const, max_depth=8,
                                   avoid_hops=failed)
            if path is None:
                self.log(f"  travel: no route {here} -> {dest_const}"
                         + (" (after ruling out impassable links)" if failed else ""))
                return False
            kind, target, warp = path[0]
            if not self.walk_hop(kind, target, warp):
                if self.current_const() == here:
                    self.log(f"  travel: {here} -> {target} is not walkable; "
                             f"looking for another way")
                    failed.add((here, kind, target))
        return self.current_const() == dest_const

    # --- healing -----------------------------------------------------------
    def party_needs_healing(self) -> bool:
        return any(m.hp < m.max_hp or m.status_name != "OK" for m in self.r.party())

    def heal_round_trip(self) -> bool:
        """Walk to the nearest Pokemon Center, heal, and come back.

        Returns True only if the party is actually at full HP afterwards.
        """
        origin = self.current_const()
        origin_loc = self.r.location()
        path = self.w.nearest_pokecenter(origin)
        if path is None:
            self.log(f"  heal: no Pokemon Center reachable from {origin}")
            return False
        center = path[-1][1]
        self.log(f"  heal: {origin} -> {center} ({len(path)} hops)")
        if not self.travel_to(center):
            self.log("  heal: could not reach the Center")
            return False
        if not self.talk_to_nurse():
            return False
        self.log(f"  heal: healed, returning to {origin}")
        if not self.travel_to(origin):
            self.log(f"  heal: healed but could not get back to {origin}")
            return False
        # Get back into the grass we were grinding in.
        self.n.walk_to(origin_loc.x, origin_loc.y)
        self._handle_battle()
        return True

    def talk_to_nurse(self) -> bool:
        here = self.current_const()
        nurse = self.w.nurses.get(here)
        if nurse is None:
            self.log(f"  heal: no nurse recorded for {here}")
            return False
        nx, ny = nurse
        for attempt in range(3):
            # Stand directly below the counter and face her.
            self.n.walk_to(nx, ny + 2)
            self.n.walk_to(nx, ny + 1)
            self.n.face("up")
            self.s.tap("a")
            self.c.run_scripts()
            self.n.settle()
            if not self.party_needs_healing():
                return True
            self.log(f"  heal: nurse attempt {attempt + 1} did not heal")
        return not self.party_needs_healing()
