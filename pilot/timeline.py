"""Periodic save states, so a recorded run can be scrubbed and resumed.

The video shows what the pilot did; these make it actionable. A checkpoint is a
real PyBoy save state -- exact machine state, not an approximation -- written at
intervals during a task and indexed against the video's timeline, so a moment
you spot at 0:42 in the recording maps to a state you can load and play from.

States are ~196 KB raw but gzip to about 7%, so hundreds of them cost a few MB.
The expensive part is taking one (~10 ms), which is why the interval defaults to
roughly one per second of video rather than something much finer.
"""
from __future__ import annotations

import gzip
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

TIMELINE_NAME = "timeline.json"
VERSION = 1


@dataclass
class Checkpoint:
    index: int
    frame: int
    game_seconds: float
    video_seconds: float | None
    file: str
    summary: str
    level: int | None = None
    battles: int | None = None
    savable: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> Checkpoint:
        return cls(
            index=d["index"], frame=d["frame"], game_seconds=d["game_seconds"],
            video_seconds=d.get("video_seconds"), file=d["file"],
            summary=d.get("summary", ""), level=d.get("level"),
            battles=d.get("battles"), savable=d.get("savable", True),
        )

    def to_dict(self) -> dict:
        return {
            "index": self.index, "frame": self.frame,
            "game_seconds": round(self.game_seconds, 2),
            "video_seconds": (round(self.video_seconds, 2)
                              if self.video_seconds is not None else None),
            "file": self.file, "summary": self.summary,
            "level": self.level, "battles": self.battles,
            "savable": self.savable,
        }

    def label(self) -> str:
        when = (f"video {_mmss(self.video_seconds)}"
                if self.video_seconds is not None
                else f"game {_mmss(self.game_seconds)}")
        mark = " " if self.savable else "*"
        return f"#{self.index:<4} {when:>16} {mark} {self.summary}"


def _mmss(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


class CheckpointWriter:
    """Writes checkpoints during a task and the manifest that indexes them."""

    def __init__(self, directory: str | Path, every_frames: int,
                 summary_fn=None, task: str = "", video: dict | None = None,
                 rom: str = "", compress: int = 6, log=print):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.every = max(1, int(every_frames))
        self.summary_fn = summary_fn
        self.task = task
        self.video = video
        self.rom = rom
        self.compress = compress
        self.log = log
        self.checkpoints: list[Checkpoint] = []
        self._closed = False
        # Clear any states from a previous run in this directory so the manifest
        # can never point at a checkpoint that belongs to a different session.
        for old in self.dir.glob("*.state.gz"):
            old.unlink()

    def should_capture(self, frame_index: int) -> bool:
        return frame_index % self.every == 0

    def capture(self, session, frame_index: int, force: bool = False) -> None:
        if self._closed:
            return
        i = len(self.checkpoints)
        name = f"{i:06d}.state.gz"
        blob = session.snapshot()
        (self.dir / name).write_bytes(gzip.compress(blob, self.compress))
        video_seconds = None
        if self.video:
            every, fps = self.video.get("every"), self.video.get("fps")
            if every and fps:
                video_seconds = (frame_index / every) / fps
        info = {}
        if self.summary_fn is not None:
            try:
                info = self.summary_fn() or {}
            except Exception:  # noqa: BLE001 -- a summary failure must not lose the checkpoint
                info = {}
        self.checkpoints.append(Checkpoint(
            index=i, frame=frame_index, game_seconds=frame_index / 60.0,
            video_seconds=video_seconds, file=name,
            summary=info.get("text", ""), level=info.get("level"),
            battles=info.get("battles"), savable=info.get("savable", True),
        ))

    def close(self) -> dict:
        if self._closed:
            return self.stats()
        self._closed = True
        manifest = {
            "version": VERSION,
            "task": self.task,
            "rom": self.rom,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "video": self.video,
            "checkpoints": [c.to_dict() for c in self.checkpoints],
        }
        (self.dir / TIMELINE_NAME).write_text(json.dumps(manifest, indent=1))
        return self.stats()

    def stats(self) -> dict:
        total = sum(f.stat().st_size for f in self.dir.glob("*.state.gz"))
        return {"dir": str(self.dir), "count": len(self.checkpoints),
                "bytes": total}

    def describe(self) -> str:
        s = self.stats()
        mb = s["bytes"] / 1_048_576
        return (f"wrote {s['count']} checkpoints to {s['dir']} ({mb:.1f} MB) -- "
                f"resume with: crystal-pilot resume {s['dir']} --at <point>")


class Timeline:
    """Reads a checkpoint directory and resolves `--at` specs against it."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        manifest = self.dir / TIMELINE_NAME
        if not manifest.exists():
            # Tolerate being pointed at the video instead of the directory.
            alt = self.dir.with_suffix(".timeline")
            if (alt / TIMELINE_NAME).exists():
                self.dir, manifest = alt, alt / TIMELINE_NAME
            else:
                raise FileNotFoundError(
                    f"no {TIMELINE_NAME} in {self.dir}. Record a run with "
                    f"--checkpoints first."
                )
        data = json.loads(manifest.read_text())
        self.task = data.get("task", "")
        self.rom = data.get("rom", "")
        self.created = data.get("created", "")
        self.video = data.get("video")
        self.checkpoints = [Checkpoint.from_dict(d) for d in data["checkpoints"]]
        if not self.checkpoints:
            raise ValueError(f"{manifest} contains no checkpoints")

    def __len__(self) -> int:
        return len(self.checkpoints)

    def load_blob(self, cp: Checkpoint) -> bytes:
        return gzip.decompress((self.dir / cp.file).read_bytes())

    def resolve(self, spec: str) -> Checkpoint:
        """Interpret an `--at` value.

        Accepts: `start`, `end`, `#7`, `1:05` or `42` (video time when the run
        was recorded, otherwise game time), and `level:14`.
        """
        s = str(spec).strip().lower()
        if s in ("start", "first", "0"):
            return self.checkpoints[0]
        if s in ("end", "last"):
            return self.checkpoints[-1]
        if s.startswith("#"):
            return self._by_index(s[1:])
        if s.startswith(("level:", "lv")):
            want = int(re.sub(r"[^0-9]", "", s))
            reached = [c for c in self.checkpoints
                       if c.level is not None and c.level >= want]
            if not reached:
                have = [c.level for c in self.checkpoints if c.level is not None]
                top = max(have) if have else "unknown"
                raise LookupError(
                    f"no checkpoint reached level {want} (highest recorded: {top})"
                )
            return reached[0]
        m = re.fullmatch(r"(\d+):([0-5]?\d(?:\.\d+)?)", s)
        if m:
            return self._by_time(int(m.group(1)) * 60 + float(m.group(2)))
        try:
            return self._by_time(float(s))
        except ValueError:
            raise LookupError(
                f"could not read {spec!r} as a point. Use seconds (42), m:ss "
                f"(1:05), an index (#7), a level (level:14), or start/end."
            ) from None

    def _by_index(self, raw: str) -> Checkpoint:
        try:
            i = int(raw)
        except ValueError:
            raise LookupError(f"{raw!r} is not a checkpoint index") from None
        if not (0 <= i < len(self.checkpoints)):
            raise LookupError(
                f"checkpoint #{i} is out of range (0..{len(self.checkpoints) - 1})"
            )
        return self.checkpoints[i]

    def _by_time(self, seconds: float) -> Checkpoint:
        """Nearest checkpoint at or before `seconds`, on whichever clock exists."""
        def clock(c: Checkpoint) -> float:
            return c.video_seconds if c.video_seconds is not None else c.game_seconds
        earlier = [c for c in self.checkpoints if clock(c) <= seconds + 1e-6]
        return earlier[-1] if earlier else self.checkpoints[0]

    def nearest_savable(self, cp: Checkpoint) -> Checkpoint | None:
        """Closest checkpoint to `cp` that the game could save at."""
        candidates = [c for c in self.checkpoints if c.savable]
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(c.index - cp.index))

    def render(self, limit: int | None = None) -> str:
        lines = [f"{len(self.checkpoints)} checkpoints -- {self.task or 'run'}"]
        if self.created:
            lines.append(f"recorded {self.created}")
        if self.video:
            lines.append(f"video: {self.video.get('path')} "
                         f"({self.video.get('speed'):.0f}x)")
        lines.append("")
        if any(not c.savable for c in self.checkpoints):
            lines.append("(* = mid-battle: resumable and playable, "
                         "but the game cannot write a save there)")
            lines.append("")
        shown = self.checkpoints if limit is None else self.checkpoints[:limit]
        lines.extend(c.label() for c in shown)
        if limit is not None and len(self.checkpoints) > limit:
            lines.append(f"... and {len(self.checkpoints) - limit} more")
        return "\n".join(lines)
