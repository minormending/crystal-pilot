"""Catch: find a particular wild Pokemon on the current route and catch it."""
from __future__ import annotations

import time

from ..control import FIGHT
from ..session import PilotTimeout
from .base import TaskResult
from .search import SearchStats, WildSearch

# Cheapest first, so a routine catch does not burn the good balls. MASTER_BALL
# is deliberately absent: it is one-per-game and is never spent automatically.
BALL_PREFERENCE = ("POKE_BALL", "GREAT_BALL", "ULTRA_BALL")
MASTER_BALL = "MASTER_BALL"


class _Sink:
    """Swallows the notes the battle engine emits during post-battle cleanup."""

    def note(self, _msg: str) -> None:
        pass


class CatchTask:
    name = "catch"

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
            ball: str | None = None, weaken_to: float | None = None,
            max_encounters: int = 500, max_balls: int = 40,
            heal_below: float = 0.30, save_when_done: bool = True) -> TaskResult:
        res = TaskResult()
        if species is None and not shiny:
            res.status = "error"
            res.message = "say what to catch: --species NAME, --shiny, or both"
            return res
        if self.r.party_count() >= 6:
            res.status = "blocked"
            res.message = ("the party is full -- a caught Pokemon would go to the "
                           "PC, which this task does not handle. Free a slot first.")
            return res

        want_id = None
        if species is not None:
            try:
                want_id = self.gd.species_id(species)
            except KeyError as e:
                res.status = "error"
                res.message = str(e)
                return res

        try:
            ball_id, ball_name = self._pick_ball(ball)
        except LookupError as e:
            res.status = "blocked"
            res.message = str(e)
            return res

        route_key = self.r.location().key
        route = self.gd.map_pretty(*route_key)
        target = self.gd.species_name(want_id) if want_id is not None else "anything"
        if shiny:
            target = f"a shiny {target}" if want_id is not None else "a shiny"
        self.log(f"catch: after {target} on {route}, throwing {ball_name}s")
        res.backup = self.backups.take(self.s, f"catch-{target.replace(' ', '-')}")

        stats = SearchStats()
        thrown = 0
        t0 = time.monotonic()
        caught = None
        blocked = None
        timed_out = False

        try:
            if not self.search.ensure_grass(route_key):
                res.status = "blocked"
                res.message = f"no grass found on {route}"
                return res

            while stats.encounters < max_encounters and thrown < max_balls:
                if self.search.party_needs_heal(heal_below):
                    self.log("catch: healing before carrying on")
                    if not self.search.heal(route_key):
                        blocked = "could not reach a Pokemon Center to heal"
                        break
                    continue

                battle = self.search.next_encounter(route_key, stats)
                if battle is None:
                    if self.r.location().key != route_key:
                        continue
                    blocked = "paced the grass but no wild Pokemon appeared"
                    break

                if not self._matches(battle, want_id, shiny):
                    self.search.leave(stats)
                    continue

                self.log(f"catch: found {battle.enemy_name} Lv{battle.enemy_level}"
                         f"{' (shiny)' if battle.enemy_shiny else ''}")
                outcome, used = self._try_capture(battle, ball_id, ball_name,
                                                  weaken_to, max_balls - thrown,
                                                  res)
                thrown += used
                if outcome == "caught":
                    caught = battle
                    break
                if outcome == "no_balls":
                    blocked = f"ran out of {ball_name}s"
                    break
                res.note(f"{battle.enemy_name} {outcome.replace('_', ' ')}; "
                         f"carrying on")
                if self.r.in_battle():
                    self.search.leave(stats)

        except PilotTimeout as e:
            timed_out = True
            self.log(f"catch: {e}")
            self.s.budget.open_reserve()

        # --- wrap up --------------------------------------------------------
        try:
            if self.r.in_battle():
                self.search.leave(stats)
            stats_out = {
                "encounters": stats.encounters,
                "balls_thrown": thrown,
                "wall": f"{time.monotonic() - t0:.1f}s",
            }
            if caught is not None:
                mon = self.r.mon(self.r.party_count() - 1)
                res.status = "completed"
                res.message = (f"caught {mon.species_name} Lv{mon.level} on "
                               f"{route} ({thrown} ball(s), "
                               f"{stats.encounters} encounter(s))")
                stats_out["caught"] = mon.species_name
                stats_out["party"] = self.r.party_count()
                if save_when_done:
                    res.saved = self.saver.save_in_game()
                    if not res.saved:
                        res.note("in-game save did not commit; "
                                 "the backup is still intact")
            elif timed_out:
                res.status = "timeout"
                res.message = (f"gave up after {stats.encounters} encounter(s) "
                               f"and {thrown} ball(s)")
            else:
                res.status = "blocked"
                res.message = (f"did not catch {target}: "
                               f"{blocked or 'ran out of encounters or balls'}")
                res.note(f"seen: {stats.top_seen()}")
            res.stats = stats_out
        except PilotTimeout:
            res.note("ran out of budget during cleanup")
        return res

    # --- capture -----------------------------------------------------------
    def _try_capture(self, battle, ball_id, ball_name, weaken_to, budget,
                     res) -> tuple[str, int]:
        """Returns (outcome, balls_used). Outcome: caught | fainted | got_away |
        gave_up | no_balls."""
        used = 0
        before_party = self.r.party_count()
        while used < budget:
            if not self.r.in_battle():
                if self.r.party_count() > before_party:
                    return "caught", used
                return "got_away", used
            state = self.r.battle()
            if state.ready and state.enemy_hp == 0:
                return "fainted", used
            if self.r.ball_count(ball_id) <= 0:
                return "no_balls", used

            if weaken_to is not None and state.ready and \
                    state.enemy_hp / max(1, state.enemy_max_hp) > weaken_to:
                if not self._chip(res):
                    return "fainted", used
                continue

            if not self.c.throw_ball(ball_id):
                res.note("could not reach the ball in the pack")
                return "gave_up", used
            used += 1
            outcome = self._watch_throw(before_party)
            if outcome is not None:
                return outcome, used
        return "gave_up", used

    def _watch_throw(self, before_party: int) -> str | None:
        """Advance the throw's text. Returns an outcome, or None to throw again.

        This delegates to the battle engine's decision pump rather than tapping
        A blindly: a stray A while the battle menu is up selects FIGHT, which
        leaves the pack out of step and quietly burns a ball on the next throw.
        """
        engine = self.search.fight
        for _ in range(4):
            what = engine.next_decision()
            if what == "ended":
                self.s.tick(90)          # let the "caught!" text settle
                return ("caught" if self.r.party_count() > before_party
                        else "got_away")
            if what == "menu":
                return None              # it broke free; the menu is back
            if what == "timeout":
                return "gave_up"
            # learn/evolve can follow a knockout from weakening; let the engine
            # deal with the prompt and then re-read the situation.
            engine._settle_post_battle(_Sink())
            if not self.r.in_battle():
                return ("caught" if self.r.party_count() > before_party
                        else "got_away")
        return None

    def _chip(self, res) -> bool:
        """Attack once with the weakest damaging move, to avoid a knockout."""
        b = self.r.battle()
        ranked = [(i, info) for i, _pp, info in
                  sorted(self._usable(b), key=lambda t: t[2]["power"])
                  if self.gd.is_damaging(info["id"])]
        if not ranked:
            res.note("no damaging move with PP left to weaken it")
            return False
        idx, info = ranked[0]
        self.s.clear_events()
        if not self.c.choose_battle_action(FIGHT):
            return False
        if self.s.await_event("move_select", timeout=300) is None:
            return False
        n_moves = sum(1 for m in b.active_moves if m)
        self.c.choose_move(idx, n_moves=n_moves)
        # Let the turn resolve, using the engine's pump for the same reason as
        # _watch_throw: a stray A on the battle menu desyncs everything after it.
        what = self.search.fight.next_decision()
        return what == "menu" and self.r.in_battle()

    def _usable(self, b):
        out = []
        for i, (mid, pp) in enumerate(zip(b.active_moves, b.active_pp)):
            if mid and (pp & 0x3F) > 0:
                out.append((i, pp & 0x3F, self.gd.move(mid)))
        return out

    # --- helpers -----------------------------------------------------------
    def _matches(self, battle, want_id, shiny) -> bool:
        if want_id is not None and battle.enemy_species != want_id:
            return False
        if shiny and not battle.enemy_shiny:
            return False
        return True

    def _pick_ball(self, requested: str | None) -> tuple[int, str]:
        have = {i: q for i, q in self.r.balls() if q > 0}
        if not have:
            raise LookupError(
                "there are no Poke Balls in the bag -- buy some at a Mart first"
            )
        if requested:
            try:
                want = self.gd.item_id(requested)
            except KeyError as e:
                raise LookupError(str(e)) from None
            if have.get(want, 0) <= 0:
                names = ", ".join(f"{self.gd.item_name(i)} x{q}"
                                  for i, q in have.items())
                raise LookupError(
                    f"no {self.gd.item_name(want)} in the bag. Carrying: {names}"
                )
            return want, self.gd.item_name(want)
        for name in BALL_PREFERENCE:
            try:
                item = self.gd.item_id(name)
            except KeyError:
                continue
            if have.get(item, 0) > 0:
                return item, name
        # Only a Master Ball (or something exotic) left: make the user ask.
        names = ", ".join(f"{self.gd.item_name(i)} x{q}" for i, q in have.items())
        raise LookupError(
            f"no ordinary Poke Balls left (carrying: {names}). "
            f"Pass --ball explicitly if you really want to use one of those."
        )
