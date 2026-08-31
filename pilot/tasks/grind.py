"""Grind one Pokemon to a target level on the current route."""
from __future__ import annotations

import time

from ..battle import BattleEngine, BattlePolicy
from ..session import PilotTimeout
from .base import TaskResult


class GrindTask:
    name = "grind"

    def __init__(self, session, reader, control, nav, world, gamedata,
                 traveler, saver, backups, log=print):
        self.s = session
        self.r = reader
        self.c = control
        self.n = nav
        self.w = world
        self.gd = gamedata
        self.trav = traveler
        self.saver = saver
        self.backups = backups
        self.log = log

    def run(self, species: str | int | None = None, to_level: int = 10,
            slot: int | None = None, heal_below: float = 0.40,
            flee_below: float = 0.30, allow_evolution: bool = True,
            learn_new_moves: bool = False, save_when_done: bool = True,
            on_timeout: str = "save") -> TaskResult:
        res = TaskResult()
        if not (1 <= to_level <= 100):
            res.status = "error"
            res.message = f"to-level must be between 1 and 100 (got {to_level})"
            return res

        # --- resolve the target -------------------------------------------
        try:
            target_slot = self._resolve_target(species, slot)
        except LookupError as e:
            res.status = "blocked"
            res.message = str(e)
            return res

        mon = self.r.mon(target_slot)
        start_level = mon.level
        # Tracked by SLOT, not species: an evolution mid-grind changes the
        # species but it is still the same Pokemon we were asked to train.
        self.log(f"grind: target slot {target_slot + 1} = {mon.describe(self.gd)}")
        if mon.level >= to_level:
            res.status = "completed"
            res.message = (f"{mon.species_name} is already Lv{mon.level} "
                           f"(target Lv{to_level})")
            res.stats = {"level": mon.level, "battles": 0}
            return res

        loc = self.r.location()
        route = self.gd.map_pretty(loc.group, loc.number)
        route_key = loc.key
        self.log(f"grind: {mon.species_name} Lv{mon.level} -> Lv{to_level} on {route}")

        res.backup = self.backups.take(self.s, f"grind-{mon.species_name}-L{to_level}")

        engine = BattleEngine(
            self.s, self.r, self.c, self.gd,
            BattlePolicy(flee_below=flee_below, allow_evolution=allow_evolution,
                         learn_new_moves=learn_new_moves),
            log=self.log,
        )

        stats = {"battles": 0, "won": 0, "fled": 0, "heals": 0, "encounters": 0}
        t0 = time.monotonic()
        timed_out = False
        blocked_reason = None

        try:
            if not self._ensure_grass(route_key):
                res.status = "blocked"
                res.message = (f"no grass found on {route} -- stand on a route "
                               f"with wild encounters before grinding")
                res.stats = stats
                return res

            while True:
                mon = self.r.mon(target_slot)
                if mon.level >= to_level:
                    break

                # Heal before it becomes an emergency. Running out of PP counts:
                # a Pokemon Center restores PP as well as HP, and the
                # alternative is Struggle, which just hurts the Pokemon we are
                # trying to train.
                dry = not any(
                    self.gd.is_damaging(info["id"])
                    for _i, _pp, info in mon.usable_moves(self.gd)
                )
                if mon.fainted or mon.hp_frac < heal_below or dry:
                    why = ("no PP left on any damaging move" if dry
                           else f"{mon.hp}/{mon.max_hp} HP")
                    self.log(f"grind: healing ({why})")
                    if self.trav.heal_round_trip():
                        stats["heals"] += 1
                        if not self._ensure_grass(route_key):
                            blocked_reason = "lost the grass patch after healing"
                            break
                    else:
                        blocked_reason = "could not reach a Pokemon Center to heal"
                        break
                    continue

                step = self.n.pace_until_battle(max_steps=500)
                if not self.r.in_battle():
                    if step.map_changed:
                        # Wandered off the route; walk back on.
                        if not self._ensure_grass(route_key):
                            blocked_reason = "wandered off the route and could not return"
                            break
                        continue
                    blocked_reason = "paced the grass but no wild Pokemon appeared"
                    break

                stats["encounters"] += 1
                outcome = engine.run(target_slot=target_slot)
                stats["battles"] += 1
                if outcome.result == "won":
                    stats["won"] += 1
                elif outcome.result == "fled":
                    stats["fled"] += 1
                for note in outcome.notes:
                    res.note(note)
                if outcome.result == "timeout":
                    blocked_reason = "a battle stopped responding"
                    break

        except PilotTimeout as e:
            timed_out = True
            self.log(f"grind: {e}")
            # Grant headroom so we can still exit cleanly and save.
            self.s.budget.open_reserve()

        # --- wrap up -------------------------------------------------------
        try:
            self._leave_battle_if_any(engine)
            mon = self.r.mon(target_slot)
            stats["level"] = mon.level
            stats["levels_gained"] = mon.level - start_level
            stats["frames"] = f"{self.s.budget.frames_used:,}"
            stats["wall"] = f"{time.monotonic() - t0:.1f}s"

            if mon.level >= to_level:
                res.status = "completed"
                res.message = (f"{mon.species_name} reached Lv{mon.level} "
                               f"(from Lv{start_level}) on {route}")
            elif timed_out:
                res.status = "timeout"
                res.message = (f"gave up at Lv{mon.level} (target Lv{to_level}); "
                               f"gained {mon.level - start_level} level(s)")
            else:
                res.status = "blocked"
                res.message = (f"stopped at Lv{mon.level} (target Lv{to_level}): "
                               f"{blocked_reason or 'unknown reason'}")

            should_save = save_when_done and (
                res.status == "completed" or on_timeout == "save"
            )
            if should_save:
                res.saved = self.saver.save_in_game()
                if not res.saved:
                    res.note("in-game save did not commit; the backup is still intact")
            elif timed_out and on_timeout == "revert":
                self.backups.restore(self.s, res.backup)
                res.note("reverted to the pre-task backup as requested")
        except PilotTimeout:
            res.note("ran out of budget during cleanup; nothing was saved")
        res.stats = stats
        return res

    # --- helpers -----------------------------------------------------------
    def _resolve_target(self, species, slot) -> int:
        party = self.r.party()
        if not party:
            raise LookupError("the party is empty -- nothing to grind")
        if slot is not None:
            if not (0 <= slot < len(party)):
                raise LookupError(
                    f"slot {slot} is out of range (party has {len(party)})"
                )
            return slot
        if species is None:
            return 0
        sid = self.gd.species_id(species)
        for m in party:
            if m.species == sid:
                return m.slot
        have = ", ".join(f"{m.species_name} (slot {m.slot + 1})" for m in party)
        raise LookupError(
            f"{self.gd.species_name(sid)} is not in the party. Party: {have}"
        )

    def _ensure_grass(self, route_key) -> bool:
        """Get back onto an encounter tile on the route we started on."""
        if self.r.in_battle():
            return True
        if self.r.location().key != route_key:
            here = self.gd.map_name(*self.r.location().key)
            want = self.gd.map_name(*route_key)
            self.log(f"grind: back to {want} from {here}")
            if not self.trav.travel_to(want):
                return False
        return self.n.find_grass()

    def _leave_battle_if_any(self, engine) -> None:
        if self.r.in_battle():
            self.log("grind: finishing the battle in progress")
            engine.p.flee_below = 1.0   # get out rather than risk a faint
            engine.run(target_slot=None, max_turns=25)
        self.n.settle()
