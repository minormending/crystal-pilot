"""Playable window with inline task dispatch.

You play the game in a normal emulator window; typing a command in the terminal
hands control to the pilot, which runs the task at full speed and then gives the
controls back without the game ever being reloaded.
"""
from __future__ import annotations

import queue
import threading

from .session import Budget

BANNER = """
crystal-pilot -- interactive mode
  Play in the emulator window and press TAB for the pilot menu
  (arrows to move, A to choose, S to go back).

  Or type a command here and press enter:

    grind <species> <level>   grind a party member on the current route
    grind <level>             same, for whatever is in slot 1
    status                    party, location, nearest Pokemon Center
    save                      save the game in-game (writes the .sav)
    backup                    take a save backup right now
    speed <n>                 emulation speed (0 = unlimited, 1 = normal)
    help / quit
"""


class InteractiveSession:
    def __init__(self, pilot, default_timeout: float = 900.0, log=print,
                 worker_factory=None, record=None, source=None,
                 in_game_menu: bool = True):
        self.p = pilot
        self.log = log
        self.default_timeout = default_timeout
        self.commands: queue.Queue[str] = queue.Queue()
        self.running = True
        # Tasks run in a second, windowless emulator: an open window pins the
        # emulator to a few hundred fps, which would turn a six-second grind
        # into minutes. State is handed across as a save state (which carries
        # cartridge RAM too), so play continues exactly where the task left off.
        self._worker_factory = worker_factory
        self._worker = None
        # record is a callable(pilot, title, take) -> Recorder|None, supplied
        # by the CLI so interactive tasks honour the same --record flags. The
        # take number is what keeps one file per dispatched task.
        self._record = record
        self._take = 0
        # The speed the *player* asked for, which is what play returns to after
        # a command. Held here because PyBoy has `set_emulation_speed` and no
        # way to read it back, so the only record of the choice is this one.
        self._play_speed = 1
        self._menu = None
        if in_game_menu and source is not None:
            from .ingame import InGameMenu
            self._menu = InGameMenu(pilot, self._run_menu_task, source, log=log)

    def _get_worker(self):
        if self._worker is None and self._worker_factory is not None:
            self._worker = self._worker_factory()
        return self._worker

    def _run_on_worker(self, fn):
        """Run `fn(pilot)` at full speed, then bring the state back."""
        worker = self._get_worker()
        if worker is None:
            return fn(self.p)          # no worker available: run in-window
        worker.session.restore(self.p.session.snapshot())
        worker.collision._calibrated = False
        worker.calibrate()
        try:
            return fn(worker)
        finally:
            self.p.session.restore(worker.session.snapshot())
            self.p.collision._calibrated = False
            self.p.calibrate()

    def _resume_play_speed(self) -> None:
        """Back to the speed the player chose.

        This runs after every command, because a task that had no worker to
        run on ran in this window and left the emulator unthrottled. It used to
        pass a literal 1 -- which meant the `speed` command set the speed and
        then had it reset on the very next line, so it printed the new speed and
        changed nothing. A command that reports what it did not do is the
        failure this repository keeps finding.
        """
        self.p.session.pyboy.set_emulation_speed(self._play_speed)

    # --- stdin reader ------------------------------------------------------
    def _reader(self) -> None:
        while self.running:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                self.commands.put("quit")
                return
            self.commands.put(line.strip())

    # --- main loop ---------------------------------------------------------
    def run(self) -> None:
        print(BANNER)
        self._resume_play_speed()
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()
        while self.running:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                cmd = None
            if cmd is not None:
                self._dispatch(cmd)
                self._resume_play_speed()
            # TAB opens the pilot menu. It is read straight from SDL, so the
            # key never reaches the game, and the menu blocks here -- which is
            # what freezes the game while it is open.
            if self._menu is not None and self._menu.wants_open():
                self._menu.open()
            # Idle frames are ticked directly so interactive play does not eat
            # into any task's frame budget.
            if not self.p.session.pyboy.tick(1, True):
                self.running = False
        self.log("stopping; writing the .sav")
        self.p.stop(save_sram=True)

    def _run_menu_task(self, fn, title: str, on_log):
        """Run a task chosen from the in-game menu, reporting progress on screen."""
        print(f"pilot taking over: {title}")

        def task(pilot):
            pilot.session.set_budget(Budget(
                max_frames=60 * 60 * 60 * 12,
                max_wall_seconds=self.default_timeout))
            previous, pilot.log = pilot.log, lambda *a, **k: (
                on_log(" ".join(str(x) for x in a)), previous(*a, **k))[1]
            try:
                return fn(pilot)
            finally:
                pilot.log = previous

        result = self._run_on_worker(task)
        if result is not None and getattr(result, "saved", False):
            self.p.session.flush_sram()
        if result is not None:
            print(result.render())
        return result

    def _dispatch(self, cmd: str) -> None:
        if not cmd:
            return
        parts = cmd.split()
        verb = parts[0].lower()
        try:
            if verb in ("quit", "exit", "q"):
                self.running = False
            elif verb in ("help", "?"):
                print(BANNER)
            elif verb == "status":
                print(self.p.status())
            elif verb == "save":
                print("saved" if self.p.save() else "save did not commit")
            elif verb == "backup":
                bs = self.p.backups.take(self.p.session, "manual")
                print(bs.describe())
            elif verb == "speed":
                self._speed(parts[1:])
            elif verb == "grind":
                self._grind(parts[1:])
            else:
                print(f"unknown command {verb!r}; type help")
        except Exception as e:  # noqa: BLE001 -- a REPL reports a bad command rather than exiting
            print(f"command failed: {type(e).__name__}: {e}")

    def _speed(self, args: list[str]) -> None:
        """`speed <n>`: 0 is unlimited, 1 is normal play.

        Remembered rather than just applied, so the reset after each command
        returns to this instead of undoing it.
        """
        if args:
            try:
                n = int(args[0])
            except ValueError:
                print(f"'{args[0]}' is not a speed; try 0 (unlimited) or 1")
                return
            if n < 0:
                print("speed cannot be negative; 0 means unlimited")
                return
        else:
            n = 1
        self._play_speed = n
        self._resume_play_speed()
        print(f"emulation speed = {n}" + (" (unlimited)" if n == 0 else ""))

    def _grind(self, args: list[str]) -> None:
        if not args:
            print("usage: grind <species> <level>   or   grind <level>")
            return
        if len(args) == 1:
            species, level = None, args[0]
        else:
            species, level = args[0], args[1]
        try:
            to_level = int(level)
        except ValueError:
            print(f"'{level}' is not a level")
            return
        print(f"pilot taking over: grind {species or 'slot 1'} -> Lv{to_level} "
              f"(timeout {self.default_timeout:.0f}s)")

        def task(pilot):
            pilot.session.set_budget(Budget(
                max_frames=60 * 60 * 60 * 12,
                max_wall_seconds=self.default_timeout))
            # The recorder attaches to whichever emulator actually runs the
            # task, which is the headless worker rather than the window.
            rec = None
            if self._record is not None:
                self._take += 1
                rec = self._record(
                    pilot, f"grind {species or 'slot 1'} -> Lv{to_level}",
                    self._take)
            try:
                return pilot.grind(species=species, to_level=to_level)
            finally:
                if rec is not None:
                    pilot.stop_recording()

        result = self._run_on_worker(task)
        print(result.render())
        if result.saved:
            self.p.session.flush_sram()
        print("controls are yours again.")
