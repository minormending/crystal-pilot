"""Task result shape, and the lifecycle every pilot task shares."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field

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
