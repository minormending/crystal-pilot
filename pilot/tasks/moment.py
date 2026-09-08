"""Three tasks that act on the situation you are already in.

Grind, hunt, catch and the sweep all go *looking* for something. These do the
obvious thing with what is in front of you and take no target:

    fight     play out the battle you are in, wild or trainer
    capture   weaken and throw at the wild Pokemon you are facing
    heal      walk to the nearest heal place and come back

None of them contain new game logic. The battle engine, the capture loop and the
Pokemon Center round trip already exist and are used exactly as the searching
tasks use them -- what is here is the guard on the front and the reporting on
the back, so each one refuses precisely instead of pressing buttons hopefully.
"""
from __future__ import annotations

import time

from .. import symbols as S
from ..battle import BattleEngine, BattlePolicy
from ..session import PilotTimeout
from .base import FACE_FROM, TaskLifecycle, TaskResult, WalksToPeople
from .catch import CatchTask


class FightTask:
    """Play out the battle already in progress."""

    name = "battle"

    def __init__(self, session, reader, control, nav, world, gamedata,
                 traveler, saver, backups, log=print):
        self.s = session
        self.r = reader
        self.c = control
        self.gd = gamedata
        self.log = log

    def run(self, target_slot: int | None = None, max_turns: int = 60,
            flee_below: float = 0.0, allow_evolution: bool = True,
            learn_new_moves: bool = False) -> TaskResult:
        res = TaskResult()
        if not self.r.in_battle():
            res.status = "blocked"
            res.message = "not in a battle"
            return res

        # flee_below defaults to 0 rather than the engine's 0.35: you asked for
        # this battle to be played, so bailing out on low HP would be answering
        # a different question. Pass it explicitly to get the escaping policy.
        policy = BattlePolicy(flee_below=flee_below,
                              allow_evolution=allow_evolution,
                              learn_new_moves=learn_new_moves,
                              switch_to_target=target_slot is not None)
        engine = BattleEngine(self.s, self.r, self.c, self.gd, policy,
                              log=self.log)

        started = time.time()
        # The kind comes off the battle state rather than a reader helper: the
        # struct is what carries wBattleMode, and it is also what is populated
        # by the time a decision is wanted.
        opening = self.r.battle()
        kind = "trainer" if opening.ready and opening.is_trainer else "wild"

        # Whether the menu is already up has to be detected, not assumed, and
        # it matters. Invoked by hand you are usually sitting at the menu, and
        # its hook has already fired -- so telling the engine to wait for one
        # means waiting for an event that will not come again. Measured on the
        # same fixture: menu_open=False reported the battle won in 0 turns
        # because it was resolved by the engine's quiet nudge rather than by
        # deliberate play, while True reported the 1 turn it actually took.
        # But it is not always up: run this while "Wild HOPPIP appeared!" is
        # still on screen and there is no menu yet. The same check the engine
        # uses internally answers it either way.
        menu_open = self.c._await_menu_cursor()
        try:
            out = engine.run(target_slot=target_slot, max_turns=max_turns,
                             menu_open=menu_open)
        except PilotTimeout:
            res.status = "timeout"
            res.message = f"the {kind} battle ran out of budget"
            return res

        res.stats = {"kind": kind, "result": out.result, "turns": out.turns,
                     "seconds": f"{time.time() - started:.1f}"}
        for n in out.notes:
            res.note(n)
        if out.result == "won":
            res.status, res.message = "completed", f"won the {kind} battle"
        elif out.result == "fled":
            res.status, res.message = "completed", f"left the {kind} battle"
        elif out.result == "lost":
            res.status, res.message = "blocked", "the whole party fainted"
        elif out.result == "ended":
            res.status, res.message = "completed", f"the {kind} battle ended"
        else:
            res.status, res.message = "timeout", f"the {kind} battle went nowhere"
        return res


class CaptureTask(CatchTask):
    """Catch the wild Pokemon in front of you.

    Subclasses the searching version rather than copying it: `_try_capture`,
    `_pick_ball`, `_chip` and `_watch_throw` are the parts that matter and they
    are identical. The only difference is that nothing is searched for first.
    """

    name = "capture"

    def run(self, ball: str | None = None, weaken_to: float | None = None,
            max_balls: int = 40, save_when_done: bool = False) -> TaskResult:
        res = TaskResult()
        if not self.r.in_battle():
            res.status = "blocked"
            res.message = "not in a battle"
            return res
        opening = self.r.battle()
        if opening.ready and opening.is_trainer:
            res.status = "blocked"
            res.message = "that is a trainer's Pokemon -- it cannot be caught"
            return res
        if self.r.party_count() >= S.MAX_PARTY:
            # `CatchTask` explains this at length: a full party is fine, the
            # game boxes the seventh. What it costs is the evidence, and the
            # box count is that evidence. `CaptureTask` subclasses `CatchTask`,
            # so it reads the box the same way -- only the refusals are here,
            # because this task takes no target and answers up front.
            box = self.r.box_count()
            if box is None:
                res.status = "blocked"
                res.message = ("the party is full and this build does not name "
                               "sBoxCount -- so a catch that went to the PC "
                               "could not be told from one that got away. "
                               "Free a party slot first.")
                return res
            if box >= S.MONS_PER_BOX:
                res.status = "blocked"
                res.message = (f"the party is full and so is the box "
                               f"({box}/{S.MONS_PER_BOX}) -- there is nowhere "
                               f"to put it. Free a slot in either.")
                return res
        try:
            ball_id, ball_name = self._pick_ball(ball)
        except LookupError as e:
            res.status = "blocked"
            res.message = str(e)
            return res

        battle = opening
        name = self.gd.species_name(battle.enemy_species) \
            if battle.ready else "it"
        started = time.time()
        try:
            outcome, used = self._try_capture(battle, ball_id, ball_name,
                                              weaken_to, max_balls, res)
        except PilotTimeout:
            res.status = "timeout"
            res.message = "ran out of budget mid-capture"
            return res

        res.stats = {"balls": used, "seconds": f"{time.time() - started:.1f}"}
        if outcome == "caught":
            res.status = "completed"
            res.message = (f"caught {name} with {used} "
                           f"{ball_name}{'' if used == 1 else 's'}")
            if save_when_done:
                res.saved = self.saver.save()
        elif outcome == "fainted":
            res.status = "blocked"
            res.message = f"knocked the {name} out"
        elif outcome == "got_away":
            res.status = "blocked"
            res.message = f"the {name} got away"
        elif outcome == "no_balls":
            res.status = "blocked"
            res.message = f"ran out of {ball_name}s"
        else:
            res.status = "blocked"
            res.message = (f"used {used} {ball_name}{'' if used == 1 else 's'} "
                           f"without catching the {name}")
        return res


class HealTask:
    """Walk to the nearest heal place, heal, and come back."""

    name = "heal"

    def __init__(self, session, reader, control, nav, world, gamedata,
                 traveler, saver, backups, log=print):
        self.s = session
        self.r = reader
        self.trav = traveler
        self.log = log

    def run(self, force: bool = False) -> TaskResult:
        res = TaskResult()
        if self.r.in_battle():
            res.status = "blocked"
            res.message = "finish the battle first"
            return res
        if self.r.party_count() == 0:
            res.status = "blocked"
            res.message = "no party to heal"
            return res

        party = self.r.party()
        # Status counts as hurt. It did not, and that was a real gap rather
        # than a nicety: a party at full HP and poisoned reported "already at
        # full health" and did nothing at all, which is the exact state a grind
        # leaves behind and the one a cure exists for.
        hurt = [m for m in party if m.hp < m.max_hp or m.status_name != "OK"]
        if not hurt and not force:
            # Reported as done rather than as an error: nothing needed doing,
            # which is the outcome the caller wanted.
            res.status = "completed"
            res.message = "the party is already at full health"
            res.stats = {"healed": 0}
            return res

        started = time.time()
        where = self.trav.current_const()
        try:
            went = self.trav.heal_up(force_walk=force)
        except PilotTimeout:
            res.status = "timeout"
            res.message = "ran out of budget on the way"
            return res

        res.stats = {
            "hurt": len(hurt),
            "from": where,
            "seconds": f"{time.time() - started:.1f}",
            "party": " ".join(f"{m.hp}/{m.max_hp}" for m in self.r.party()),
        }
        if not went:
            res.status = "blocked"
            res.message = ("could not reach somewhere that heals from "
                           f"{where}")
            return res
        res.status = "completed"
        via = self.trav.healed_via
        res.stats["via"] = via or "walk"
        if via == "bag":
            res.message = f"healed {len(hurt)} Pokemon out of the bag, at {where}"
        elif hurt:
            res.message = f"healed {len(hurt)} Pokemon and came back to {where}"
        else:
            res.message = f"healed, back at {where}"
        return res


class DuelTask(TaskLifecycle, WalksToPeople):
    """Fight the trainer nearest you, without being told which one.

    The sweep in `tasks/trainers.py` walks a whole route from the placements the
    disassembly lists. This is the same errand aimed at *one* person, and it
    reads the live object structs instead -- three differences, each of which is
    a fact about people rather than about item balls:

    * **A trainer is only there if the game has spawned it.** Route 30 places
      three; from the south end none of them has a struct. So the list is who is
      *near*, and this never walks at a placement.
    * **A trainer moves**, so the tile is re-read on every attempt. A takeable
      can be read once and walked to twice; a person cannot.
    * **A trainer who refuses is not asked again.** Two in range with the near
      one already beaten means every attempt goes to the nearer, and the whole
      budget is spent on somebody who will never answer. The placement index is
      the identity for that, because after a step the coordinates are not.
    """

    name = "duel"

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
        # Walking over crosses grass, so wild encounters interrupt. They are
        # fled rather than fought: the HP is for the trainer.
        self._escape = BattleEngine(session, reader, control, gamedata,
                                    BattlePolicy(always_flee=True), log=log)

    def _near(self, refused: set[int]) -> list[dict]:
        """Spawned trainers, nearest first, minus the ones already asked."""
        cm = self.n.collision
        if cm is None:
            return []
        here = self.r.location()
        found = [t for t in cm.trainers_here() if t["index"] not in refused]
        found.sort(key=lambda t: abs(t["x"] - here.x) + abs(t["y"] - here.y))
        return found

    def run(self, max_attempts: int = 6, flee_below: float = 0.0,
            allow_evolution: bool = True, learn_new_moves: bool = False,
            save_when_done: bool = False) -> TaskResult:
        res = TaskResult()
        if self.r.in_battle():
            res.status = "blocked"
            res.message = "already in a battle -- `battle` plays that one out"
            return res
        cm = self.n.collision
        if cm is None or cm.live_objects() is None:
            # "Cannot tell" rather than "nobody here": the same distinction
            # `live_objects` is careful about, and here it would cost a walk.
            res.status = "blocked"
            res.message = ("cannot read the object structs on this build, so "
                           "there is no way to tell who is standing nearby")
            return res

        where = self.gd.map_pretty(*self.r.location().key)
        refused: set[int] = set()
        fought = 0
        started = time.time()

        with self.budgeted(res, "duel") as run:
            for _ in range(max_attempts):
                if run.timed_out:
                    break
                # Re-read every attempt: somebody may have walked, and somebody
                # may have been spawned by the last conversation.
                candidates = self._near(refused)
                if not candidates:
                    break
                who = candidates[0]
                outcome = self._engage(who)
                if outcome == "fought":
                    fought += 1
                    got = self._play_out(res, flee_below, allow_evolution,
                                         learn_new_moves)
                    if got == "lost":
                        res.status = "blocked"
                        res.message = "the whole party fainted"
                        res.stats = {"where": where, "fought": fought}
                        return res
                    # One duel is one duel. Stop rather than sweep -- `trainers`
                    # is the command that clears a route.
                    break
                refused.add(who["index"])
                res.note(f"{outcome} at ({who['x']},{who['y']})")

        with self.wrapping(res):
            if self.r.in_battle():
                self._escape.run(max_turns=25)
            res.stats = {
                "where": where,
                "fought": fought,
                "asked": len(refused) + fought,
                "seconds": f"{time.time() - started:.1f}",
            }
            if fought:
                res.status = "completed"
                res.message = f"fought a trainer on {where}"
                if save_when_done:
                    res.saved = self.saver.save_in_game()
            elif run.timed_out:
                res.status = "timeout"
                res.message = "ran out of budget getting to anybody"
            elif refused:
                # Reached and turned away is not the same as nobody being here,
                # and the notes say which of them it was.
                res.status = "blocked"
                res.message = (f"nobody on {where} would fight -- asked "
                               f"{len(refused)}")
            else:
                res.status = "blocked"
                res.message = f"no trainer is standing near you on {where}"
        return res

    def _play_out(self, res, flee_below, allow_evolution,
                  learn_new_moves) -> str:
        policy = BattlePolicy(flee_below=flee_below,
                              allow_evolution=allow_evolution,
                              learn_new_moves=learn_new_moves,
                              switch_to_target=False)
        engine = BattleEngine(self.s, self.r, self.c, self.gd, policy,
                              log=self.log)
        out = engine.run(max_turns=80, menu_open=self.c._await_menu_cursor())
        for n in out.notes:
            res.note(n)
        return out.result

    def _engage(self, who: dict) -> str:
        """Walk up to `who` and talk. -> fought | absent | no_battle | unreachable.

        `no_battle` is only reported after actually standing next to them and
        talking, so a failed approach is never mistaken for somebody who has
        already been beaten -- the same care the sweep's version takes.
        """
        tx, ty = who["x"], who["y"]
        cm = self.n.collision
        spots = [(tx, ty + 1), (tx, ty - 1), (tx + 1, ty), (tx - 1, ty)]
        if cm is not None and cm.calibrated:
            walkable = [p for p in spots if cm.walkable(*p)]
            spots = walkable or spots
        reached = False
        for sx, sy in spots:
            # Pushed through the grass rather than walked once: crossing a route
            # spends a replan budget on encounters, not on obstacles.
            outcome = self._walk_to_verified(sx, sy)
            if outcome == "battle":
                return "fought"          # spotted us on the way over
            if outcome != "at":
                continue
            reached = True
            # Re-read: they may have stepped while we walked.
            still = next((t for t in (cm.trainers_here() or [])
                          if t["index"] == who["index"]), None)
            if still is None:
                return "absent"
            facing = FACE_FROM.get((still["x"] - sx, still["y"] - sy))
            if facing is None:
                return "no_battle"       # they moved out of arm's reach
            self.n.face(facing)
            self.s.clear_events()
            self.s.tap("a")
            self.s.tick(45)
            if self.r.in_battle():
                return "fought"
            self.c.run_scripts()         # a beaten trainer just chats
            if self.r.in_battle():
                return "fought"
            return "no_battle"
        return "no_battle" if reached else "unreachable"
