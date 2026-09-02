"""Save backups and in-game saving.

Two different things are backed up before any task, because they protect against
different failures:
  * the .sav (battery SRAM)  -- what the real game and other emulators read
  * a PyBoy save state       -- exact machine state, so a task can be rewound
                                even mid-battle
Saving in-game is driven through the actual START -> SAVE -> YES menu and
confirmed by the SaveGameData hook, so the pilot never claims a save that the
game did not commit.
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BackupSet:
    label: str
    sav: Path | None
    state: Path | None
    when: str

    def describe(self) -> str:
        bits = [f"backup '{self.label}' at {self.when}"]
        if self.sav:
            bits.append(f"sav={self.sav.name}")
        if self.state:
            bits.append(f"state={self.state.name}")
        return " ".join(bits)


class BackupManager:
    def __init__(self, backup_dir: str | Path, sav_path: str | Path | None = None,
                 log=print, keep: int = 40):
        self.dir = Path(backup_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sav_path = Path(sav_path) if sav_path else None
        self.log = log
        self.keep = keep

    def _stamp(self) -> str:
        return time.strftime("%Y%m%d-%H%M%S")

    def take(self, session, label: str) -> BackupSet:
        """Snapshot both SRAM and machine state before a task runs."""
        stamp = self._stamp()
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label)
        sav_copy = None
        if self.sav_path and self.sav_path.exists():
            sav_copy = self.dir / f"{stamp}-{safe}.sav"
            shutil.copy2(self.sav_path, sav_copy)
        state_copy = self.dir / f"{stamp}-{safe}.state"
        session.save_state_to(state_copy)
        bs = BackupSet(label=safe, sav=sav_copy, state=state_copy, when=stamp)
        self.log(f"backup: {bs.describe()}")
        self._prune()
        return bs

    def _prune(self) -> None:
        """Drop the oldest backup *sets*, keeping the newest `keep`.

        Ordered by the timestamp in the name, not by mtime. `take` copies the
        .sav with copy2, which preserves the *source* save's modification time
        -- so a .sav backup taken today can carry an mtime from last week, and
        mtime order has nothing to do with when the backup was made. Pruning the
        two suffixes independently by mtime therefore deleted the .sav half of
        the newest set while keeping .savs from much older ones. Measured: a
        restore then silently fell back to the .state, because the .sav it named
        had been pruned minutes after being written.

        Sets are pruned whole, so a surviving .state always has its .sav beside
        it -- which is what a restore needs to be exact rather than approximate.
        """
        stamps = sorted({p.stem for p in self.dir.glob("*.state")}
                        | {p.stem for p in self.dir.glob("*.sav")},
                        reverse=True)
        for stem in stamps[self.keep:]:
            for suffix in (".sav", ".state"):
                (self.dir / f"{stem}{suffix}").unlink(missing_ok=True)

    def restore(self, session, backup: BackupSet) -> bool:
        """Put the machine state and the .sav back. Returns whether the .sav was.

        Order matters, and it is the opposite of the obvious one. Loading the
        machine state brings that moment's SRAM with it, so anything that
        flushes SRAM afterwards writes *the state's* bytes over the .sav that
        was just copied in. The caller does flush, to keep the two consistent --
        so the .sav copy goes last, and the flush is asked for before it.

        Without this the .sav half was never restored at all: the file ended up
        holding the state's SRAM while the log claimed it came from the backup's
        .sav. Close enough to look right -- the same party, the same map -- and
        not the same bytes.
        """
        if backup.state and backup.state.exists():
            session.load_state_from(backup.state)
            self.log(f"restored machine state from {backup.state.name}")
            session.flush_sram()
        if backup.sav and self.sav_path and backup.sav.exists():
            shutil.copy2(backup.sav, self.sav_path)
            self.log(f"restored SRAM from {backup.sav.name}")
            return True
        if backup.state and backup.state.exists():
            self.log("no .sav in that backup set; the save now holds the "
                     "machine state's SRAM, which may differ byte for byte")
        return False

    def list(self) -> list[Path]:
        return sorted(self.dir.glob("*.state"), key=lambda p: p.stat().st_mtime)


class GameSaver:
    """Drives the in-game save menu and confirms the game actually committed.

    Saving is done through the real START -> SAVE -> YES flow rather than by
    poking SRAM, and success is taken from the SaveGameData hook firing -- so the
    pilot never reports a save the game did not make.
    """

    def __init__(self, session, reader, control, log=print):
        self.s = session
        self.r = reader
        self.c = control
        self.log = log

    def save_in_game(self, attempts: int = 3) -> bool:
        if self.r.in_battle():
            self.log("save: refusing to save during a battle")
            return False
        for attempt in range(1, attempts + 1):
            if self._one_attempt():
                self.log("save: committed (game wrote its save data)")
                self.s.flush_sram()
                return True
            self.log(f"save: attempt {attempt} did not commit")
            self.c.close_menus(6)
        return False

    def _one_attempt(self) -> bool:
        self.c.close_menus(3)
        self.c.open_start_menu()
        if not self._menu_is_open():
            return False
        count = self._entry_count()
        if count < 3:
            return False
        # The last three START-menu entries are always SAVE, OPTION, EXIT, so
        # SAVE is count-2 regardless of whether the POKeDEX entry exists yet.
        order = [count - 2] + [i for i in range(1, count + 1) if i != count - 2]
        for row in order:
            if self._try_row(row, count):
                return True
        return False

    def _try_row(self, row: int, count: int) -> bool:
        if not self._menu_is_open():
            self.c.open_start_menu()
            if not self._menu_is_open():
                return False
        self._drive_cursor(row, count)
        self.s.clear_events()
        self.s.tap("a")
        self.s.tick(30)
        if self.s.has_event("saved_ok", "save_write"):
            self.c.advance_text(max_taps=60, quiet_frames=90)
            return True
        if self.s.has_event("save_menu", "save_confirm"):
            # "Would you like to save the game?" -- YES is preselected. This is
            # not a YesNoBox, so it has its own hook. The prompt takes ~100
            # frames to become interactive and any A pressed before that is
            # swallowed, so wait for its cursor before confirming.
            self._await_confirm_box()
            self.s.clear_events()
            for _ in range(3):
                self.s.tap("a")
                if self.s.await_event("saved_ok", "save_write", timeout=300):
                    self.c.advance_text(max_taps=80, quiet_frames=120)
                    return True
        # Wrong entry (party/bag/gear/options opened, or the menu closed).
        self.c.close_menus(4)
        self.c.open_start_menu()
        return False

    def _menu_is_open(self, tries: int = 25) -> bool:
        """Confirm the START menu is really up before pressing anything.

        This matters more than it looks: navigating the menu presses DOWN, and
        if the menu never opened those presses walk the player through the grass
        instead -- which starts a wild battle and makes saving impossible.
        """
        for _ in range(tries):
            if self.s.rb("wMenuCursorY") != 0:
                return True
            self.s.tick(4)
        return False

    def _await_confirm_box(self, tries: int = 60) -> bool:
        """Wait for the save confirm box's own cursor (YES == row 1)."""
        for _ in range(tries):
            self.s.tick(4)
            if self.s.rb("wMenuCursorY") == 1:
                self.s.tick(8)
                return True
        return False

    def _await_cursor(self, tries: int = 25) -> bool:
        for _ in range(tries):
            if self.s.rb("wMenuCursorY") != 0:
                return True
            self.s.tick(4)
        return False

    def _entry_count(self, limit: int = 12) -> int:
        """Count menu rows by stepping until the cursor wraps.

        Bails out if the player turns out to be walking -- that means the menu
        was not open and these DOWN presses are moving us through the world.
        """
        loc0 = self.r.location()
        seen = []
        for _ in range(limit):
            cur = self.s.rb("wMenuCursorY")
            if cur in seen:
                break
            seen.append(cur)
            self.s.tap("down", hold=4, gap=6)
            if (self.r.location().x, self.r.location().y) != (loc0.x, loc0.y):
                self.log("save: the START menu was not open (the player moved)")
                return 0
        return max(seen) if seen else 0

    def _drive_cursor(self, target: int, count: int) -> bool:
        for _ in range(count + 2):
            if self.s.rb("wMenuCursorY") == target:
                return True
            self.s.tap("down", hold=4, gap=6)
        return self.s.rb("wMenuCursorY") == target
