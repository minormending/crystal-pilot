"""Catch: find a particular wild Pokemon on the current route and catch it."""
from __future__ import annotations

import time

from dataclasses import dataclass

from ..control import FIGHT
from ..session import PilotTimeout
from .base import TaskResult
from .search import SearchStats, WildSearch

# Cheapest first, so a routine catch does not burn the good balls. MASTER_BALL
# is deliberately absent: it is one-per-game and is never spent automatically.
BALL_PREFERENCE = ("POKE_BALL", "GREAT_BALL", "ULTRA_BALL")
MASTER_BALL = "MASTER_BALL"

# Swings at one target before giving up on weakening it any further. Weakening
# spends no ball, so without a bound a move that keeps missing loops here until
# the session budget dies -- the ball budget never moves, because no ball is
# ever thrown.
MAX_CHIPS = 8

# Effects whose damage has nothing to do with the power byte. Gen 2 stores them
# as power 0 or 1, which puts every one of them *ahead* of TACKLE when ranking
# by power -- so the move picked "to avoid a knockout" would be GUILLOTINE.
# They are excluded from weakening rather than ranked, because there is no
# gentle version of a one-hit KO or of halving something's HP.
UNGENTLE_EFFECTS = {
    "EFFECT_OHKO",           # GUILLOTINE, HORN_DRILL, FISSURE: the whole bar
    "EFFECT_SUPER_FANG",     # half of current HP, whatever that is
    "EFFECT_LEVEL_DAMAGE",   # SEISMIC_TOSS, NIGHT_SHADE: your level, in HP
    "EFFECT_PSYWAVE",        # up to 1.5x your level, unpredictably
    "EFFECT_COUNTER",        # twice what you just took
    "EFFECT_MIRROR_COAT",
}


class _Sink:
    """Swallows the notes the battle engine emits during post-battle cleanup."""

    def note(self, _msg: str) -> None:
        pass


@dataclass
class Damage:
    """The biggest hit one swing has been seen to land.

    Weakening is a bet that the next swing leaves the target alive, and the
    only honest evidence for that bet is what previous swings actually did --
    nothing here reads the damage formula. `biggest_hit` is that evidence, and
    the guard is simply: never swing at something with no more HP than this.

    A knockout is a measurement too, and the most valuable one: if the target
    had 11 HP and one swing took all of it, then one swing does *at least* 11.
    That is why a CatchTask keeps one of these across every encounter on the
    route -- the first target it meets is the one swing it cannot guard, and
    losing it teaches the guard enough to protect all the rest.
    """

    biggest_hit: int = 0

    def could_finish(self, hp: int) -> bool:
        return hp <= self.biggest_hit

    def learn(self, dealt: int) -> None:
        self.biggest_hit = max(self.biggest_hit, dealt)


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
        # One guard for the whole hunt, not one per encounter. What the party
        # does to a Lv3 PIDGEY is what it will do to the next one, so a target
        # lost early is the price of protecting every target after it.
        memory = Damage()
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
                                                  res, memory=memory)
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
            if memory.biggest_hit:
                stats_out["hardest_hit"] = memory.biggest_hit
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
                     res, memory=None) -> tuple[str, int]:
        """Returns (outcome, balls_used). Outcome: caught | fainted | got_away |
        gave_up | no_balls.

        `memory` is the learned-damage guard. Pass one in to carry what has been
        learned across encounters; leave it out and this starts cold, knowing
        nothing about how hard the party hits.
        """
        used = 0
        chips = 0
        weakening = weaken_to is not None
        mem = memory if memory is not None else Damage()
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

            if weakening and state.ready and \
                    state.enemy_hp / max(1, state.enemy_max_hp) > weaken_to:
                # The threshold is not a safe stopping point on its own. Against
                # something small, one swing carries it from above the line
                # straight to zero -- and a fainted Pokemon cannot be caught by
                # any ball. Stop while it is still alive to throw at.
                if mem.could_finish(state.enemy_hp):
                    weakening = False
                    res.note(f"stopped weakening at {state.enemy_hp} HP: one "
                             f"more hit has taken {mem.biggest_hit} before now")
                elif chips >= MAX_CHIPS:
                    weakening = False
                    res.note(f"weakening got nowhere in {MAX_CHIPS} turns; "
                             f"throwing as it is")
                else:
                    chips += 1
                    hp_before = state.enemy_hp
                    how = self._chip(res)
                    if how == "fainted":
                        # It had hp_before left and one swing took all of it, so
                        # that is the floor on what a swing does. Worth keeping
                        # even though this target is lost.
                        mem.learn(hp_before)
                        return "fainted", used
                    if how == "ended":
                        # Our own attack ended the battle. With no ball thrown
                        # and the party no bigger, the target fainted -- unless
                        # it was *us* that went down, which the party can still
                        # be asked about after the battle is over.
                        lead = self.r.mon(0)
                        if lead.hp > 0:
                            mem.learn(hp_before)
                            return "fainted", used
                        res.note("the lead went down while weakening it")
                        return "got_away", used
                    if how == "ok":
                        now = self.r.battle()
                        if now.ready:
                            mem.learn(hp_before - now.enemy_hp)
                        continue
                    # nomove or stuck: no gentle attack left, or the turn got
                    # away from us. Take the odds as they are rather than
                    # stalling -- a worse throw still beats no throw.
                    #
                    # Back out of any menu first. A chip that ended badly can
                    # leave the move list open, and _await_menu_cursor cannot
                    # tell that from the battle menu -- both drive the same
                    # cursor -- so the throw below would step through the moves
                    # and confirm one. throw_ball does notice and refuse, but by
                    # then the turn is spent, and spending it on an unchosen
                    # move is how the target gets knocked out.
                    if how == "stuck":
                        self.c.close_menus(4)
                        self.s.tick(30)
                    weakening = False

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

    def _chip(self, res) -> str:
        """Attack once with the gentlest move available.

        Returns what happened, because the caller needs to tell the cases apart
        rather than just "worked / did not": ok | fainted | ended | nomove |
        stuck. A knockout is a measurement the guard wants, and "no move to use"
        is a reason to throw the ball anyway -- neither is a failure.
        """
        b = self.r.battle()
        ranked = sorted((t for t in self._usable(b) if self._is_gentle(t[2])),
                        key=lambda t: t[2]["power"])
        if not ranked:
            res.note("no move gentle enough to weaken it with")
            return "nomove"
        idx, _pp, info = ranked[0]
        self.s.clear_events()
        if not self.c.choose_battle_action(FIGHT):
            return "stuck"
        if self.s.await_event("move_select", timeout=300) is None:
            return "stuck"
        n_moves = sum(1 for m in b.active_moves if m)
        self.c.choose_move(idx, n_moves=n_moves)
        # Let the turn resolve, using the engine's pump for the same reason as
        # _watch_throw: a stray A on the battle menu desyncs everything after it.
        what = self.search.fight.next_decision()
        if what == "ended" or not self.r.in_battle():
            # A knockout ends the battle, so "ended" is the *usual* way a chip
            # that went too far reports back -- the enemy struct is gone by the
            # time anyone could read a zero out of it. The caller tells the two
            # apart by asking whether our own lead is still standing.
            return "ended"
        if what != "menu":
            return "stuck"
        after = self.r.battle()
        if after.ready and after.enemy_hp == 0:
            return "fainted"
        return "ok"

    def _is_gentle(self, info) -> bool:
        """Can this move take HP off without deciding the battle by itself?

        Status moves are out because they weaken nothing -- rank LEER as the
        gentlest attack and it spends every turn achieving nothing. The
        fixed-damage and one-hit-KO effects are out for the opposite reason:
        their power byte says 0 or 1 while the move itself takes half the bar,
        your level in HP, or all of it.
        """
        if info["effect"] in UNGENTLE_EFFECTS:
            return False
        return info["power"] > 0 and self.gd.is_damaging(info["id"])

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
