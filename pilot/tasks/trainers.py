"""Sweep: battle every trainer on the current route."""
from __future__ import annotations

import time

from ..battle import BattleEngine, BattlePolicy
from .base import FACE_FROM, TaskLifecycle, TaskResult, WalksToPeople


class TrainerSweepTask(TaskLifecycle, WalksToPeople):
    name = "trainers"

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
        # Walking to a trainer crosses grass, so wild encounters interrupt. They
        # get fled rather than fought: HP is for the trainer.
        self._escape = BattleEngine(session, reader, control, gamedata,
                                    BattlePolicy(always_flee=True), log=log)

    def run(self, heal_below: float = 0.60, max_trainers: int | None = None,
            allow_evolution: bool = True, learn_new_moves: bool = False,
            save_when_done: bool = True) -> TaskResult:
        res = TaskResult()
        route_key = self.r.location().key
        route_const = self.gd.map_name(*route_key)
        route = self.gd.map_pretty(*route_key)
        trainers = list(self.w.trainers.get(route_const, []))
        if not trainers:
            res.status = "blocked"
            res.message = f"{route} has no trainers on it"
            return res
        if max_trainers:
            trainers = trainers[:max_trainers]

        self.log(f"trainers: {len(trainers)} on {route}")

        # Trainer battles cannot be run from, so the policy never tries.
        engine = BattleEngine(
            self.s, self.r, self.c, self.gd,
            BattlePolicy(flee_below=0.0, switch_to_target=False,
                         allow_evolution=allow_evolution,
                         learn_new_moves=learn_new_moves),
            log=self.log,
        )

        stats = {"trainers": len(trainers), "beaten": 0, "already_beaten": 0,
                 "not_present": 0, "unreachable": 0}
        t0 = time.monotonic()
        lost = False

        with self.budgeted(res, f"trainers-{route_const}") as run:
            for i, t in enumerate(trainers, 1):
                mon = self._lead()
                if mon is None or mon.hp_frac < heal_below:
                    self.log("trainers: healing before the next fight")
                    if not self.trav.heal_up():
                        res.note("could not reach a Pokemon Center to heal")
                        break
                    if self.r.location().key != route_key:
                        self.trav.travel_to(route_const)

                label = f"{t['sprite'].replace('SPRITE_', '').title()} at ({t['x']},{t['y']})"
                self.log(f"trainers: [{i}/{len(trainers)}] {label}")
                self._await_out_of_battle()
                engaged = self._engage(t)
                if engaged == "unreachable":
                    stats["unreachable"] += 1
                    res.note(f"could not reach {label}")
                    continue
                if engaged == "absent":
                    stats["not_present"] += 1
                    res.note(f"{label} is not on the route yet "
                             f"(gated behind story progress)")
                    continue
                if engaged == "no_battle":
                    stats["already_beaten"] += 1
                    continue

                out = engine.run(target_slot=None, max_turns=80)
                for n in out.notes:
                    res.note(n)
                if out.result == "lost":
                    lost = True
                    res.note(f"lost to {label}")
                    break
                if out.turns == 0:
                    # No turn was ever played, so nothing was actually fought.
                    res.note(f"{label} did not start a battle")
                    stats["already_beaten"] += 1
                    self._await_out_of_battle()
                    continue
                stats["beaten"] += 1
                self.n.settle()
                self.c.run_scripts()      # post-battle chat
                self._await_out_of_battle()

        # --- wrap up --------------------------------------------------------
        with self.wrapping(res):
            if self.r.in_battle():
                engine.run(target_slot=None, max_turns=40)
            self.n.settle()
            stats["wall"] = f"{time.monotonic() - t0:.1f}s"
            done = (stats["beaten"] + stats["already_beaten"]
                    + stats["not_present"])
            if lost:
                res.status = "blocked"
                res.message = (f"blacked out on {route} after beating "
                               f"{stats['beaten']} trainer(s)")
            elif run.timed_out:
                res.status = "timeout"
                res.message = (f"gave up after beating {stats['beaten']} of "
                               f"{len(trainers)} trainer(s) on {route}")
            elif done >= len(trainers):
                res.status = "completed"
                bits = [f"beat {stats['beaten']}"]
                if stats["already_beaten"]:
                    bits.append(f"{stats['already_beaten']} already done")
                if stats["not_present"]:
                    bits.append(f"{stats['not_present']} not on the route yet")
                res.message = f"cleared {route}: " + ", ".join(bits)
            else:
                res.status = "blocked"
                res.message = (f"beat {stats['beaten']} of {len(trainers)} "
                               f"trainer(s) on {route}")
            if save_when_done and not self.r.in_battle():
                res.saved = self.saver.save_in_game()
            res.stats = stats
        return res

    # --- helpers -----------------------------------------------------------
    def _await_out_of_battle(self, rounds: int = 40) -> bool:
        """wBattleMode stays set for a while after a fight ends.

        Without waiting, the next trainer looks like it is already being fought
        and gets counted as beaten without a single turn being played.
        """
        for _ in range(rounds):
            if not self.r.in_battle():
                self.s.tick(20)
                if not self.r.in_battle():
                    return True
            self.s.tick(20)
            if self.c.script_running():
                self.c.run_scripts()
        return not self.r.in_battle()

    def _object_at(self, tx: int, ty: int) -> bool:
        """Is a loaded map object standing on this tile?

        `wObjectStructs` holds the live instances, and `wMapObjects` lists the
        map's objects whether or not they are present -- so the structs are the
        only array that can answer this. Both are now read by
        `CollisionMap.live_objects`, which this used to walk itself with its own
        copy of the stride, the +4 origin and the slot count. One reader, and
        it also fixed a real slip in the copy: this looped to 16 where
        `NUM_OBJECT_STRUCTS` is 13, so three iterations read 120 bytes past the
        end of the array and could match a tile on whatever follows it.
        """
        live = self.n.collision.live_objects()
        if not live:
            return False
        return any((o["x"], o["y"]) == (tx, ty) for o in live)

    def _lead(self):
        party = self.r.party()
        return next((m for m in party if not m.fainted), None)

    def _engage(self, trainer: dict) -> str:
        """Walk up to a trainer and start the fight.

        -> fought | no_battle | unreachable. `no_battle` is only reported after
        actually standing next to them and talking, so a failed approach is
        never mistaken for a trainer who has already been beaten.
        """
        tx, ty = trainer["x"], trainer["y"]
        cm = self.n.collision
        spots = [(tx, ty + 1), (tx, ty - 1), (tx + 1, ty), (tx - 1, ty)]
        if cm is not None and cm.calibrated:
            walkable = [p for p in spots if cm.walkable(*p)]
            spots = walkable or spots
        reached = False
        for sx, sy in spots:
            outcome = self._walk_to_verified(sx, sy)
            if outcome == "battle":
                return "fought"          # spotted us on the way over
            if outcome != "at":
                continue
            reached = True
            if not self._object_at(tx, ty):
                # Some trainers only exist after story progress (their
                # object_event is gated on an event flag), so an empty tile is
                # "not here yet", not "already beaten".
                return "absent"
            facing = FACE_FROM.get((tx - sx, ty - sy))
            if facing:
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
