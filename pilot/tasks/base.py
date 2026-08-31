"""Task result shape shared by every pilot task."""
from __future__ import annotations

from dataclasses import dataclass, field


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
