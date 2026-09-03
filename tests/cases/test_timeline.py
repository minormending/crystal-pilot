"""Checkpoint timeline: the --at specs that `resume` accepts."""
import json

from ..harness import test


def _timeline(tmp):
    """A hand-written manifest, so the specs are tested without a full run."""
    d = tmp / "run.timeline"
    d.mkdir(parents=True, exist_ok=True)
    cps = []
    for i in range(5):
        (d / f"{i:06d}.state.gz").write_bytes(b"")
        cps.append({"index": i, "frame": i * 1800, "game_seconds": i * 30.0,
                    "video_seconds": float(i * 10), "file": f"{i:06d}.state.gz",
                    "summary": f"point {i}", "level": 10 + i,
                    "battles": None, "savable": i % 2 == 0})
    (d / "timeline.json").write_text(json.dumps(
        {"version": 1, "task": "test", "rom": "", "created": "",
         "video": {"path": "run.mp4", "fps": 30, "every": 60, "speed": 30.0},
         "checkpoints": cps}))
    return d


@test("--at understands every spec form")
def _(t):
    import tempfile
    from pathlib import Path

    from pilot.timeline import Timeline
    with tempfile.TemporaryDirectory() as tmp:
        tl = Timeline(_timeline(Path(tmp)))
        t.eq(tl.resolve("start").index, 0, "start")
        t.eq(tl.resolve("end").index, 4, "end")
        t.eq(tl.resolve("#3").index, 3, "index")
        t.eq(tl.resolve("20").index, 2, "seconds")
        t.eq(tl.resolve("0:30").index, 3, "m:ss")
        t.eq(tl.resolve("level:13").index, 3, "first checkpoint at that level")
        # A time between checkpoints snaps back to the one before it, so you
        # never resume past the moment you asked for.
        t.eq(tl.resolve("25").index, 2, "between points rounds down")


@test("--at rejects nonsense with a useful message")
def _(t):
    import tempfile
    from pathlib import Path

    from pilot.timeline import Timeline
    with tempfile.TemporaryDirectory() as tmp:
        tl = Timeline(_timeline(Path(tmp)))
        t.raises(LookupError, lambda: tl.resolve("banana"), "unparseable spec")
        t.raises(LookupError, lambda: tl.resolve("#99"), "out of range index")
        t.raises(LookupError, lambda: tl.resolve("level:99"), "unreached level")


@test("nearest_savable skips the mid-battle points")
def _(t):
    import tempfile
    from pathlib import Path

    from pilot.timeline import Timeline
    with tempfile.TemporaryDirectory() as tmp:
        tl = Timeline(_timeline(Path(tmp)))
        odd = tl.resolve("#3")
        t.false(odd.savable, "#3 is mid-battle in this manifest")
        near = tl.nearest_savable(odd)
        t.true(near.savable, "the suggestion must be savable")
        t.eq(abs(near.index - odd.index), 1, "and adjacent")
