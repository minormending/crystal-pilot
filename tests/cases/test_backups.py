"""The safety net: taking a backup, and getting the same bytes back.

Both of these guard defects found by using the thing. A backup is what makes a
task that writes the .sav safe to run at all, so "restore" quietly returning
something *close* to the saved bytes is worse than it failing outright -- you
only find out by comparing hashes, and nobody compares hashes.
"""
import hashlib
import shutil
import time
from pathlib import Path

from pilot.backup import BackupManager

from ..harness import test


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@test("a restore puts the saved bytes back, not something close to them")
def _(t):
    """Driven through the CLI, because the defect lived in the ordering there.

    `restore` copies the backup's .sav over the live save, and the CLI used to
    flush SRAM straight afterwards -- which wrote the *machine state's* SRAM on
    top of the bytes just restored. The two are not the same: the .sav on disk
    is whatever the game last committed, while the machine is wherever it is
    now. So a restore reported success, logged the .sav it had used, and left a
    file that had never been in that backup. Same party, same map, different
    bytes, and nobody compares hashes.
    """
    from pilot import cli
    rom = t.rom_copy("backup-restore")
    sav = Path(rom).with_suffix(".sav")

    # A .sav on disk that is deliberately out of step with the running machine
    # -- which is the normal case, not a contrived one: SRAM only changes when
    # the game commits a save.
    known = bytes(range(256)) * 128
    sav.write_bytes(known)
    original = _sha(sav)

    p = t.pilot_on(rom, "grass_cyndaquil")
    backup = p.backups.take(p.session, "under-test")
    t.true(backup.sav is not None and backup.sav.exists(),
           "the backup set should include a .sav")
    t.eq(_sha(backup.sav), original, "the backup captured those bytes")
    p.stop(save_sram=False)

    # Move the file away from the backup, so a no-op would be visible.
    sav.write_bytes(b"\x00" * len(known))
    t.ne(_sha(sav), original, "the save differs before restoring")

    rc = cli.main(["--rom", str(rom), "--source", str(t.source), "--quiet",
                   "backups", "restore", "--name", Path(backup.state).name])
    t.eq(rc, 0, "the restore should succeed")
    t.eq(_sha(sav), original, "restored save is byte-identical to the backup")


@test("pruning drops whole backup sets, oldest first")
def _(t):
    """Ordered by the name's timestamp, because mtime actively lies here.

    `take` copies the .sav with copy2, which preserves the *source* save's
    modification time. So a .sav backup taken today carries whatever mtime the
    live save had -- and if the live save has not been written in a week, every
    backup .sav made this week shares that week-old timestamp. Pruning the two
    suffixes independently by mtime then deletes the .sav half of a recent set
    while keeping .savs from older ones, and a later restore silently falls back
    to the .state. Which is how it was found.

    The setup below makes the mtimes point the wrong way on purpose: the
    earliest backup gets the newest mtime. Ordering by mtime keeps exactly the
    wrong three.
    """
    import os

    # A temp directory is all this needs. It used to ask for a ROM copy,
    # which meant it skipped itself on CI for no reason at all.
    d = t.scratch("backup-prune") / "backups"
    sav = d.parent / "fake.sav"
    sav.parent.mkdir(parents=True, exist_ok=True)

    class _FakeSession:
        def save_state_to(self, path):
            Path(path).write_bytes(b"\x02" * 64)

    m = BackupManager(d, sav_path=sav, log=lambda *a, **k: None, keep=3)
    stems = []
    for i in range(5):
        sav.write_bytes(bytes([i]) * 64)
        # Inverted: backup 0 looks newest by mtime, backup 4 oldest.
        fake = time.time() - i * 86400
        os.utime(sav, (fake, fake))
        stamp = f"2026010{i}-000000"
        m._stamp = lambda s=stamp: s
        stems.append(Path(m.take(_FakeSession(), "case").state).stem)

    kept_states = sorted(p.stem for p in d.glob("*.state"))
    kept_savs = sorted(p.stem for p in d.glob("*.sav"))
    t.note(f"states={kept_states} savs={kept_savs}")
    t.eq(len(kept_states), 3, "three newest .state files kept")
    # A set pruned in halves is worse than no backup: the .state survives, so
    # the restore looks available, and the bytes it would have used are gone.
    t.eq(kept_savs, kept_states, "every surviving .state still has its .sav")
    t.eq(kept_states, sorted(stems)[-3:], "and they are the newest three")


@test("pruning leaves alone files it did not write")
def _(t):
    """The backup directory is shared, not private.

    `hunt --keep-battle` parks a `found-<SPECIES>.state` here so the battle can
    be picked up later in `play` or `resume`. It has no `.sav` by design, which
    made it look exactly like the half-written backup that prune-by-set exists
    to clear away. Sweeping it up would delete a deliberate hand-off.
    """
    d = t.scratch("backup-foreign") / "backups"
    sav = d.parent / "fake.sav"
    sav.parent.mkdir(parents=True, exist_ok=True)
    sav.write_bytes(b"\x01" * 64)

    class _FakeSession:
        def save_state_to(self, path):
            Path(path).write_bytes(b"\x02" * 64)

    m = BackupManager(d, sav_path=sav, log=lambda *a, **k: None, keep=2)
    d.mkdir(parents=True, exist_ok=True)
    # A hunt's hand-off, and something a person might drop in here.
    foreign = d / "found-RATTATA.state"
    foreign.write_bytes(b"\x03" * 64)
    notes = d / "notes.txt"
    notes.write_bytes(b"keep me")
    # `found-...` starts with a letter, so a name-ordered sweep happens to keep
    # it whatever the bug does -- which would make the check above pass for the
    # wrong reason. This one sorts below every real stamp, so only a prune that
    # actually restricts itself to its own files will spare it.
    low = d / "0000-hand-saved.state"
    low.write_bytes(b"\x04" * 64)

    # Enough backups to force several prunes.
    for i in range(5):
        stamp = f"2026010{i}-000000"
        m._stamp = lambda s=stamp: s
        m.take(_FakeSession(), "case")

    t.true(foreign.exists(),
           "hunt's found-*.state must survive an unrelated task's pruning")
    t.true(low.exists(),
           "and so must a foreign file that sorts below every backup stamp")
    t.true(notes.exists(), "and so must anything else in the directory")
    t.eq(len(list(d.glob("*-case.state"))), 2, "its own sets still prune to keep")
