"""Recording and checkpoints: the artefacts a run leaves behind."""
import shutil
import subprocess
import tempfile
from pathlib import Path

from pilot.timeline import Timeline

from ..harness import test


def _short_run(t, tmp, record=True, checkpoints=True):
    """A brief grind that produces a video and/or a checkpoint timeline."""
    p = t.pilot_on(t.rom_copy("rec"), "grass_cyndaquil")
    rec = None
    if record:
        rec = p.start_recording(tmp / "run.mp4", speed=90, fps=30, scale=2,
                                title="test run")
    if checkpoints:
        p.start_checkpoints(tmp / "run.timeline", every_frames=600,
                            task="test run")
    start = p.reader.mon(0).level
    p.grind(slot=0, to_level=start + 2, save_when_done=False)
    if record:
        p.stop_recording()
    cps = p.stop_checkpoints() if checkpoints else None
    return p, rec, cps


@test("a recorded run writes a real video")
def _(t):
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p, rec, _ = _short_run(t, tmp, checkpoints=False)
        t.true(rec.path.exists(), "video file written")
        t.gt(rec.frames, 10, "frames captured")
        t.gt(rec.path.stat().st_size, 10_000, "file has real content")
        t.note(f"{rec.frames} frames, {rec.stats()['seconds']:.0f}s at "
               f"{rec.actual_speed:.0f}x, {rec.path.stat().st_size / 1024:.0f} KB")
        if shutil.which("ffprobe"):
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v",
                 "-show_entries", "stream=width,height,nb_frames",
                 "-of", "csv=p=0", str(rec.path)],
                capture_output=True, text=True).stdout.strip()
            t.contains(out, "320", "video width (160 * scale 2)")


@test("checkpoints index against the video's clock")
def _(t):
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p, rec, cps = _short_run(t, tmp)
        t.gt(cps.stats()["count"], 2, "checkpoints written")
        tl = Timeline(tmp / "run.timeline")
        t.eq(len(tl), cps.stats()["count"], "manifest matches what was written")
        t.true(tl.video is not None, "video details recorded")
        first, last = tl.checkpoints[0], tl.checkpoints[-1]
        t.eq(first.video_seconds, 0.0, "first checkpoint is at 0:00")
        t.gt(last.frame, first.frame, "frames advance")
        t.gt(last.video_seconds, first.video_seconds, "video time advances")
        # Levels are what make `--at level:N` work.
        t.true(all(c.level is not None for c in tl.checkpoints), "levels recorded")


@test("resuming a checkpoint restores that exact moment")
def _(t):
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p, rec, cps = _short_run(t, tmp)
        tl = Timeline(tmp / "run.timeline")
        first, last = tl.checkpoints[0], tl.checkpoints[-1]
        t.ne(first.level, last.level, "the run should have gained a level")

        fresh = t.pilot_on(t.rom_copy("resume"))
        fresh.resume_from(tl, first)
        t.eq(fresh.reader.mon(0).level, first.level, "level at the first point")
        fresh.resume_from(tl, last)
        t.eq(fresh.reader.mon(0).level, last.level, "level at the last point")


@test("a savable checkpoint can be committed to the .sav")
def _(t):
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p, rec, cps = _short_run(t, tmp)
        tl = Timeline(tmp / "run.timeline")
        savable = [c for c in tl.checkpoints if c.savable]
        t.gt(len(savable), 0, "some checkpoints should be savable")
        target = savable[0]

        rom = t.rom_copy("commit")
        fresh = t.pilot_on(rom)
        fresh.resume_from(tl, target)
        ok, why = fresh.settle_for_save()
        t.true(ok, f"a savable checkpoint should save: {why}")
        t.true(fresh.save(), "in-game save should commit")

        again = t.pilot_on(rom)
        t.true(again.continue_game(), "the committed save should load")
        t.eq(again.reader.mon(0).level, target.level,
             "the .sav continues from that point")


@test("mid-battle checkpoints are flagged, and a savable one is suggested")
def _(t):
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p, rec, cps = _short_run(t, tmp)
        tl = Timeline(tmp / "run.timeline")
        unsavable = [c for c in tl.checkpoints if not c.savable]
        t.note(f"{len(tl) - len(unsavable)} savable of {len(tl)}")
        # Most of a grind is spent in battles, where Gen 2 refuses to save.
        t.gt(len(unsavable), 0, "some checkpoints should be mid-battle")
        near = tl.nearest_savable(unsavable[0])
        t.true(near is not None and near.savable, "a savable point is suggested")
