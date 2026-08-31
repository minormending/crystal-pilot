"""An in-game menu for dispatching pilot tasks.

Opened with Tab while playing. The emulator stops ticking, so the game freezes
exactly where it was and none of the keys pressed in here reach it; input is
read straight from SDL instead of through the game's joypad, and each frame is
drawn over the frozen picture and presented by hand.

Tab is chosen because PyBoy does not bind it -- unlike a Game Boy button
combination, it cannot be confused with something the player meant to do in the
game.
"""
from __future__ import annotations

import time

import sdl2

from .overlay import Overlay
from .wild import species_on

# SDL scancodes. A and S are the same keys PyBoy maps to the Game Boy's A and B,
# so confirm/back feel the same inside the menu as outside it.
KEY_OPEN = sdl2.SDL_SCANCODE_TAB
KEY_UP = sdl2.SDL_SCANCODE_UP
KEY_DOWN = sdl2.SDL_SCANCODE_DOWN
KEY_LEFT = sdl2.SDL_SCANCODE_LEFT
KEY_RIGHT = sdl2.SDL_SCANCODE_RIGHT
KEY_CONFIRM = sdl2.SDL_SCANCODE_A
KEY_BACK = sdl2.SDL_SCANCODE_S
KEY_CLOSE = sdl2.SDL_SCANCODE_ESCAPE

WATCHED = (KEY_OPEN, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_CONFIRM,
           KEY_BACK, KEY_CLOSE)

ROOT_ITEMS = [
    ("grind", "GRIND"),
    ("hunt", "HUNT"),
    ("catch", "CATCH"),
    ("trainers", "TRAINERS"),
    ("status", "STATUS"),
    ("save", "SAVE GAME"),
    ("close", "CLOSE"),
]


class Keys:
    """Edge-triggered keyboard reads, independent of the game's joypad."""

    def __init__(self):
        self._prev = {k: False for k in WATCHED}

    @staticmethod
    def _state():
        sdl2.SDL_PumpEvents()
        return sdl2.SDL_GetKeyboardState(None)

    def poll(self) -> dict:
        """{scancode: just_pressed}. Held keys report once, not every frame."""
        state = self._state()
        pressed = {}
        for k in WATCHED:
            now = bool(state[k])
            pressed[k] = now and not self._prev[k]
            self._prev[k] = now
        return pressed

    def held(self, scancode) -> bool:
        return bool(self._state()[scancode])

    @staticmethod
    def quit_requested() -> bool:
        return bool(sdl2.SDL_QuitRequested())


class ScriptedKeys:
    """A canned key sequence, for driving the menu without a keyboard."""

    def __init__(self, sequence):
        self._seq = list(sequence)

    def poll(self) -> dict:
        if not self._seq:
            return {KEY_CLOSE: True}
        return {self._seq.pop(0): True}

    @staticmethod
    def quit_requested() -> bool:
        return False


class InGameMenu:
    def __init__(self, pilot, runner, source, log=print, keys=None):
        """`runner(fn, title, on_log)` executes fn(pilot) and returns its result.

        `keys` is injectable so the menu can be driven by a scripted sequence in
        tests, where there is no window and no one to press anything.
        """
        self.p = pilot
        self.runner = runner
        self.source = source
        self.log = log
        self.ov = Overlay(pilot.session.pyboy)
        self.keys = keys if keys is not None else Keys()
        self._spinner = 0

    # --- entry point -------------------------------------------------------
    def wants_open(self) -> bool:
        return bool(self.keys.poll().get(KEY_OPEN))

    def open(self) -> None:
        """Run the menu until it is closed. Blocks; the game stays frozen."""
        self.ov.freeze()
        try:
            self._root()
        finally:
            self.ov.thaw()

    # --- screens -----------------------------------------------------------
    def _root(self) -> None:
        idx = 0
        while True:
            choice = self._choose("PILOT", [label for _k, label in ROOT_ITEMS],
                                  idx, footer="A select   S back")
            if choice is None:
                return
            idx = choice
            key = ROOT_ITEMS[idx][0]
            if key == "close":
                return
            if key == "status":
                self._status()
            elif key == "save":
                self._save()
            elif key == "grind":
                self._grind()
            elif key in ("hunt", "catch"):
                self._hunt_or_catch(key)
            elif key == "trainers":
                self._trainers()

    def _status(self) -> None:
        r = self.p.reader
        loc = r.location()
        body = [self.p.gamedata.map_pretty(loc.group, loc.number)
                + ("  [grass]" if r.on_grass() else "")]
        for m in r.party():
            body.append(f"{m.slot + 1} {m.species_name[:9]} Lv{m.level} "
                        f"{m.hp}/{m.max_hp}")
        self._show("STATUS", body or ["(no party)"])

    def _save(self) -> None:
        self._banner("SAVE GAME", "saving...")
        ok, why = self.p.settle_for_save()
        saved = ok and self.p.save()
        self._show("SAVE GAME",
                   ["saved." if saved else "could not save:",
                    "" if saved else why[:26]])

    def _grind(self) -> None:
        party = self.p.reader.party()
        if not party:
            self._show("GRIND", ["the party is empty"])
            return
        labels = [f"{m.slot + 1} {m.species_name[:9]} Lv{m.level}" for m in party]
        pick = self._choose("TRAIN WHICH?", labels, 0, footer="A choose   S back")
        if pick is None:
            return
        mon = party[pick]
        level = self._number("TO WHAT LEVEL?", mon.level + 1, mon.level + 1, 100,
                             footer="up/down 1   left/right 5")
        if level is None:
            return
        self._run(f"GRIND -> Lv{level}",
                  lambda pilot: pilot.grind(slot=mon.slot, to_level=level))

    def _hunt_or_catch(self, kind: str) -> None:
        loc = self.p.reader.location()
        const = self.p.gamedata.map_name(loc.group, loc.number)
        found = species_on(self.source, const)
        title = "HUNT WHAT?" if kind == "hunt" else "CATCH WHAT?"
        if kind == "catch" and not self.p.reader.balls():
            self._show("CATCH", ["no Poke Balls in the bag", "buy some at a Mart"])
            return
        if not found:
            self._show(title, ["nothing wild appears here",
                               "stand on a route with grass"])
            return
        # Offering the route's own encounter table beats typing a name and then
        # hunting for something that was never here.
        labels = ["ANY SHINY"] + found
        pick = self._choose(title, labels, 1,
                            footer=f"on {self.p.gamedata.map_pretty(loc.group, loc.number)}")
        if pick is None:
            return
        shiny = pick == 0
        species = None if shiny else labels[pick]
        name = "a shiny" if shiny else species
        if kind == "hunt":
            self._run(f"HUNT {name[:14]}",
                      lambda pilot: pilot.hunt(species=species, shiny=shiny))
        else:
            self._run(f"CATCH {name[:13]}",
                      lambda pilot: pilot.catch(species=species, shiny=shiny))

    def _trainers(self) -> None:
        loc = self.p.reader.location()
        const = self.p.gamedata.map_name(loc.group, loc.number)
        count = len(self.p.world.trainers.get(const, []))
        if not count:
            self._show("TRAINERS", ["no trainers on this map"])
            return
        ok = self._confirm("TRAINERS", [f"{count} trainer(s) here",
                                        "battle them all?"])
        if ok:
            self._run("TRAINERS", lambda pilot: pilot.trainers())

    # --- running a task ----------------------------------------------------
    def _run(self, title: str, fn) -> None:
        self._banner(title, "starting")
        result = self.runner(fn, title, lambda msg: self._banner(title, msg))
        if result is None:
            self._show(title, ["the task did not run"])
            return
        body = [result.message[:26]]
        stats = getattr(result, "stats", {}) or {}
        for key in ("battles", "encounters", "balls_thrown", "beaten", "heals",
                    "level", "wall"):
            if key in stats:
                body.append(f"{key:<12}{stats[key]}")
        self._show("DONE" if result.ok else "STOPPED", body[:8])

    # --- widgets -----------------------------------------------------------
    def _choose(self, title, items, index, footer=None):
        """A list. Returns the chosen index, or None if backed out."""
        top = 0
        rows = 7 if footer else 8
        while True:
            top = max(min(top, index), index - rows + 1, 0)
            self.ov.present(self.ov.menu(title, items, index, footer, top))
            k = self._wait()
            if k in (KEY_BACK, KEY_CLOSE, KEY_OPEN):
                return None
            if k == KEY_UP:
                index = (index - 1) % len(items)
                top = min(top, index)
            elif k == KEY_DOWN:
                index = (index + 1) % len(items)
                if index == 0:
                    top = 0
            elif k == KEY_CONFIRM:
                return index

    def _number(self, title, value, low, high, footer=None):
        while True:
            self.ov.present(self.ov.lines(title, ["", f"      {value}", ""],
                                          footer))
            k = self._wait()
            if k in (KEY_BACK, KEY_CLOSE, KEY_OPEN):
                return None
            if k == KEY_UP:
                value = min(high, value + 1)
            elif k == KEY_DOWN:
                value = max(low, value - 1)
            elif k == KEY_RIGHT:
                value = min(high, value + 5)
            elif k == KEY_LEFT:
                value = max(low, value - 5)
            elif k == KEY_CONFIRM:
                return value

    def _confirm(self, title, body) -> bool:
        idx = self._choose(title, ["YES", "NO"], 1, footer=body[0][:26])
        return idx == 0

    def _show(self, title, body) -> None:
        self.ov.present(self.ov.lines(title, body, footer="A to close"))
        while True:
            k = self._wait()
            if k in (KEY_CONFIRM, KEY_BACK, KEY_CLOSE, KEY_OPEN):
                return

    def _banner(self, title: str, status: str) -> None:
        self._spinner += 1
        self.ov.present(self.ov.working(title, status, self._spinner))

    def _wait(self, timeout: float = 300.0):
        """Block until one of the watched keys is pressed, then return it.

        The timeout means a wedged event queue closes the menu and gives the
        game back, rather than leaving the window frozen for good.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.keys.quit_requested():
                return KEY_CLOSE
            pressed = self.keys.poll()
            for k in WATCHED:
                if pressed.get(k):
                    return k
            time.sleep(1 / 60)
        return KEY_CLOSE
