"""Hunt: search the current route for a particular wild Pokemon."""
from __future__ import annotations

import time

from ..session import PilotTimeout
from .base import TaskResult
from .search import SearchStats, WildSearch


class HuntTask:
    name = "hunt"

    def __init__(self, session, reader, control, nav, world, gamedata,
                 traveler, saver, backups, log=print):
        self.s = session
        self.r = reader
        self.c = control
        self.n = nav
        self.gd = gamedata
        self.trav = traveler
        self.saver = saver
        self.backups = backups
        self.log = log
        self.search = WildSearch(session, reader, control, nav, gamedata,
                                 traveler, log=log)

    def run(self, species: str | int | None = None, shiny: bool = False,
            min_level: int | None = None, max_encounters: int = 500,
            heal_below: float = 0.30, keep_battle: bool = True) -> TaskResult:
        res = TaskResult()
        if species is None and not shiny:
            res.status = "error"
            res.message = "say what to hunt: --species NAME, --shiny, or both"
            return res

        want_id = None
        if species is not None:
            try:
                want_id = self.gd.species_id(species)
            except KeyError as e:
                res.status = "error"
                res.message = str(e)
                return res

        loc = self.r.location()
        route_key = loc.key
        route = self.gd.map_pretty(*route_key)
        wanted = self._describe_target(want_id, shiny, min_level)
        self.log(f"hunt: looking for {wanted} on {route}")
        res.backup = self.backups.take(self.s, f"hunt-{self._slug(want_id, shiny)}")

        stats = SearchStats()
        t0 = time.monotonic()
        found = None
        blocked = None
        timed_out = False

        try:
            if not self.search.ensure_grass(route_key):
                res.status = "blocked"
                res.message = (f"no grass found on {route} -- stand on a route "
                               f"with wild encounters before hunting")
                return res

            while stats.encounters < max_encounters:
                if self.search.party_needs_heal(heal_below):
                    self.log("hunt: healing before carrying on")
                    if not self.search.heal(route_key):
                        blocked = "could not reach a Pokemon Center to heal"
                        break
                    continue

                battle = self.search.next_encounter(route_key, stats)
                if battle is None:
                    if self.r.location().key != route_key:
                        continue          # wandered off; ensure_grass will fix it
                    blocked = "paced the grass but no wild Pokemon appeared"
                    break

                if self._matches(battle, want_id, shiny, min_level):
                    found = battle
                    break
                self.search.leave(stats)

        except PilotTimeout as e:
            timed_out = True
            self.log(f"hunt: {e}")
            self.s.budget.open_reserve()

        stats_out = {
            "encounters": stats.encounters,
            "fled": stats.fled,
            "fought": stats.fought,
            "wall": f"{time.monotonic() - t0:.1f}s",
        }

        if found is not None:
            tag = "shiny " if found.enemy_shiny else ""
            res.status = "completed"
            res.message = (f"found {tag}{found.enemy_name} Lv{found.enemy_level} "
                           f"on {route} after {stats.encounters} encounter(s)")
            stats_out["found"] = found.enemy_name
            stats_out["level"] = found.enemy_level
            if found.enemy_shiny:
                stats_out["shiny"] = True
            if keep_battle:
                # Leave the battle on screen and save the exact moment, so it can
                # be picked up in `play` or with `resume`.
                state = self.backups.dir / f"found-{found.enemy_name}.state"
                self.s.save_state_to(state)
                res.note(f"battle left in progress; state saved to {state.name}")
                res.note("pick it up with `crystal-pilot play` "
                         "(the battle is still live in the save state)")
            else:
                self.search.leave(stats)
        else:
            if timed_out:
                res.status = "timeout"
                res.message = (f"gave up after {stats.encounters} encounter(s) "
                               f"without finding {wanted}")
            elif stats.encounters >= max_encounters:
                res.status = "blocked"
                res.message = (f"saw {stats.encounters} encounters on {route} "
                               f"without finding {wanted}")
            else:
                res.status = "blocked"
                res.message = (f"stopped after {stats.encounters} encounter(s): "
                               f"{blocked or 'unknown reason'}")
            res.note(f"seen: {stats.top_seen()}")
        res.stats = stats_out
        return res

    # --- helpers -----------------------------------------------------------
    def _matches(self, battle, want_id, shiny, min_level) -> bool:
        if want_id is not None and battle.enemy_species != want_id:
            return False
        if shiny and not battle.enemy_shiny:
            return False
        if min_level is not None and battle.enemy_level < min_level:
            return False
        return True

    def _describe_target(self, want_id, shiny, min_level) -> str:
        name = self.gd.species_name(want_id) if want_id is not None else None
        if shiny:
            what = f"a shiny {name}" if name else "a shiny (any species)"
        else:
            what = name or "anything"
        if min_level is not None:
            what += f" at Lv{min_level}+"
        return what

    def _slug(self, want_id, shiny) -> str:
        name = self.gd.species_name(want_id) if want_id is not None else "any"
        return f"{'shiny-' if shiny else ''}{name}"
