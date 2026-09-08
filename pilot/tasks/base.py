"""Task result shape, and the lifecycle every pilot task shares."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field

from ..nav import DELTA
from ..session import PilotTimeout


@dataclass
class TaskResult:
    status: str = "error"      # completed | timeout | blocked | aborted | error
    message: str = ""
    stats: dict = field(default_factory=dict)
    saved: bool = False
    backup: object | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def render(self) -> str:
        icon = {"completed": "done", "timeout": "gave up (timeout)",
                "blocked": "gave up (blocked)", "aborted": "aborted",
                "error": "error"}.get(self.status, self.status)
        lines = [f"{icon}: {self.message}"]
        if self.stats:
            lines.append("  " + "  ".join(f"{k}={v}" for k, v in self.stats.items()))
        lines.append(f"  saved: {'yes' if self.saved else 'no'}")
        if self.backup is not None:
            lines.append(f"  {self.backup.describe()}")
        for n in self.notes[:12]:
            lines.append(f"  note: {n}")
        if len(self.notes) > 12:
            lines.append(f"  ... and {len(self.notes) - 12} more notes")
        return "\n".join(lines)


# Which way to face to talk to somebody on an adjacent tile, keyed by the
# offset *from where you stand to where they are*.
#
# Derived from `nav.DELTA` rather than written out, because it was written out
# twice and both copies were inverted in all four entries -- `(0, 1): "up"`,
# where `DELTA` says "up" is `(0, -1)`. So the trainer sweep turned its back on
# every trainer and then pressed A.
#
# It got away with it because a trainer *spots* you: Route 30's three have sight
# ranges of 3, 1 and 3, and the tile you have to stand on to talk is inside
# that, so the game starts the battle itself before the press matters. Which is
# this project's signature failure -- the code did the wrong thing and the right
# thing happened anyway.
FACE_FROM = {delta: button for button, delta in DELTA.items()}


class WalksToPeople:
    """Getting across a route to somebody who might walk away.

    Shared by the trainer sweep and the duel rather than copied into both,
    because the last thing these two shared by copy was `_object_at` -- and the
    copy looped to sixteen where `NUM_OBJECT_STRUCTS` is thirteen, reading a
    hundred and twenty bytes past the end of the array. One reader.

    Needs `self.r`, `self.n` and an `self._escape` engine with a flee policy.
    """

    def _clear_wild(self) -> bool:
        """Deal with a battle met on the way. -> is the way clear again.

        A *trainer* battle is left alone -- that is what we came for, and the
        caller checks for it straight after the walk. The battle has to be
        pumped to a decision point first, because `is_trainer` is only
        meaningful once the battle structs are populated.
        """
        if not self.r.in_battle():
            return True
        what = self._escape.next_decision()
        if what in ("ended", "timeout"):
            self.n.settle()
            return not self.r.in_battle()
        state = self.r.battle()
        if state.ready and state.is_trainer:
            return False          # leave it for the caller
        self._escape.run(target_slot=None, max_turns=25,
                         menu_open=(what == "menu"))
        self.n.settle()
        return not self.r.in_battle()

    def _walk_to_verified(self, x: int, y: int, attempts: int = 12) -> str:
        """Walk to (x, y), pushing through wild encounters. -> at | battle | no.

        A single `walk_to` gives up after its replan budget, and crossing a
        route full of grass burns that budget on encounters rather than on
        obstacles -- so the walk is simply retried, each attempt resuming from
        wherever the last one stopped.
        """
        for _ in range(attempts):
            if self.r.in_battle():
                if not self._clear_wild():
                    return "battle"
            self.n.walk_to(x, y, on_battle=self._clear_wild)
            if self.r.in_battle():
                if not self._clear_wild():
                    return "battle"
                continue
            loc = self.r.location()
            if (loc.x, loc.y) == (x, y):
                return "at"
        return "no"


class TaskLifecycle:
    """The scaffolding every task repeated: a backup, and two budget guards.

    Four tasks each opened with `backups.take`, wrapped their work in
    `except PilotTimeout`, logged it, opened the budget reserve, and set a
    timeout status -- and then wrapped the wrap-up in a second guard, because
    tidying up (leaving a battle, saving a state) drives the emulator too and
    can itself run out of budget.

    Three of the four did. `hunt` did not, so a timeout while it put the battle
    away escaped `run()` entirely: the caller got an exception where every other
    task returns a TaskResult, and the CLI printed a traceback. That is the
    thing a rule kept in four places does.

    Mixed in rather than inherited from, so each task keeps its own shape and
    its own reading of what "done" means -- what is shared here is only the
    bookkeeping around it.
    """

    @contextmanager
    def budgeted(self, res: TaskResult, label: str):
        """Snapshot before the work, and report a timeout instead of raising.

        Yields a small object whose `timed_out` the task reads afterwards,
        which is the local flag each of them used to keep by hand.
        """
        res.backup = self.backups.take(self.s, label)

        class _Run:
            timed_out = False

        run = _Run()
        try:
            yield run
        except PilotTimeout as e:
            run.timed_out = True
            self.log(f"{getattr(self, 'name', 'task')}: {e}")
            # Headroom so the wrap-up below can still leave the game tidy and
            # save. Without it the cleanup would time out immediately too.
            self.s.budget.open_reserve()

    @contextmanager
    def wrapping(self, res: TaskResult):
        """Let the wrap-up run out of budget without losing the result.

        The work is already done by this point and the interesting part of the
        answer is already on `res`; losing all of it because putting the battle
        away took a few frames too many is the wrong trade.
        """
        try:
            yield
        except PilotTimeout:
            res.note("ran out of budget during cleanup")
