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

from ..battle import BattleEngine, BattlePolicy
from ..session import PilotTimeout
from .base import TaskResult
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
        if self.r.party_count() >= 6:
            res.status = "blocked"
            res.message = ("the party is full -- a caught Pokemon would go to the "
                           "PC, which this task does not handle. Free a slot first.")
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
        hurt = [m for m in party if m.hp < m.max_hp]
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
            went = self.trav.heal_round_trip()
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
        res.message = (f"healed {len(hurt)} Pokemon and came back to {where}"
                       if hurt else f"healed, back at {where}")
        return res
