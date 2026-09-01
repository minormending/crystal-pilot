"""Bootstrap a brand-new game up to the point where grinding is possible.

Needed because a fresh pokecrystal build has no save at all: without this there
is no party, no route, and nothing for the grind task to act on. Each stage is
verified against game memory (map id, party count) rather than trusted blindly,
so a stage that silently fails is reported instead of cascading.
"""
from __future__ import annotations

from ..control import TEXT_EVENTS

# ELMS_LAB ball positions (see maps/ElmsLab.asm object_events)
STARTER_BALL_X = {"cyndaquil": 6, "totodile": 7, "chikorita": 8}
STARTER_SPECIES = {"cyndaquil": "CYNDAQUIL", "totodile": "TOTODILE",
                   "chikorita": "CHIKORITA"}

NEW_BARK = 24
M_ROUTE_29, M_NEW_BARK_TOWN, M_ELMS_LAB = 3, 4, 5
M_PLAYERS_HOUSE_1F, M_PLAYERS_HOUSE_2F = 6, 7


class BootstrapError(RuntimeError):
    pass


class Bootstrap:
    def __init__(self, session, reader, control, nav, log=print):
        self.s = session
        self.r = reader
        self.c = control
        self.n = nav
        self.log = log

    # --- stage 1: title -> named character in the bedroom ------------------
    def run_intro(self, max_menu_taps: int = 400) -> None:
        self.log("intro: booting through copyright + cinematic")
        self.s.tick(2500)
        # Mash A until the first textbox: this covers title -> main menu ->
        # NEW GAME -> Oak's speech, without needing to know how long each takes.
        self.s.clear_events()
        reached = None
        for i in range(max_menu_taps):
            self.s.tap("a", hold=5, gap=10)
            if self.s.has_event(*TEXT_EVENTS):
                reached = i
                break
        if reached is None:
            raise BootstrapError(
                "never reached the intro dialogue -- the ROM may not have booted "
                "to the main menu (is this a fresh build with no save?)"
            )
        self.log(f"intro: dialogue started after {reached} taps")
        # Oak's speech and the gender prompt are A-confirmable defaults. The
        # NAME menu is not: its default is NEW NAME, which opens the letter
        # grid, and an auto-pilot mashing A through a letter grid ends up
        # called AAAAA. advance_text takes one of the game's own names instead.
        for _ in range(30):
            self.c.advance_text(max_taps=500, quiet_frames=100)
            loc = self.r.location()
            if loc.key != (0, 0):
                self.log(f"intro: world loaded at {self._where()}, "
                         f"player named {self.r.player_name() or '(unnamed)'}")
                return
            self.s.tap("a")
            self.s.tick(30)
        raise BootstrapError("intro finished but no map ever loaded")

    # --- stage 2: bedroom -> Elm's lab ------------------------------------
    def walk_to_lab(self) -> None:
        loc = self.r.location()
        if loc.key == (NEW_BARK, M_PLAYERS_HOUSE_2F):
            self.log("route: bedroom -> downstairs")
            self._goto(7, 0, expect=(NEW_BARK, M_PLAYERS_HOUSE_1F), push="up")
        if self.r.location().key == (NEW_BARK, M_PLAYERS_HOUSE_1F):
            self.log("route: ground floor -> New Bark Town")
            # Mom stops you with a coord_event on the way out.
            self._goto(6, 7, expect=(NEW_BARK, M_NEW_BARK_TOWN), talk=True, push="down")
        if self.r.location().key == (NEW_BARK, M_NEW_BARK_TOWN):
            self.log("route: New Bark Town -> Elm's lab")
            self._goto(6, 3, expect=(NEW_BARK, M_ELMS_LAB), talk=True, push="up")
        if self.r.location().key != (NEW_BARK, M_ELMS_LAB):
            raise BootstrapError(f"could not reach Elm's lab; stopped at {self._where()}")
        self.log(f"route: arrived {self._where()}")

    # --- stage 3: get a starter -------------------------------------------
    def get_starter(self, choice: str = "cyndaquil") -> None:
        choice = choice.lower()
        if choice not in STARTER_BALL_X:
            raise BootstrapError(
                f"unknown starter {choice!r}; pick one of {', '.join(STARTER_BALL_X)}"
            )
        if self.r.party_count() > 0:
            self.log("starter: party already non-empty, skipping")
            return
        self.log(f"starter: choosing {choice}")
        # Elm greets you on entry; let that script finish before moving.
        self.c.run_scripts()
        # Talk to Elm (object at 5,2) from directly below.
        self.n.walk_to(5, 3)
        self.n.face("up")
        self.s.tap("a")
        self.c.run_scripts()

        ball_x = STARTER_BALL_X[choice]
        for attempt in range(3):
            self.n.walk_to(ball_x, 4)
            self.n.face("up")
            self.s.tap("a")
            # The "do you want this one?" prompt defaults to YES, and the whole
            # pickup is a map script, so run it to completion.
            self.c.run_scripts()
            if self.r.party_count() > 0:
                break
            self.log(f"starter: attempt {attempt + 1} did not take, retrying")
        if self.r.party_count() == 0:
            raise BootstrapError(
                "could not pick up a starter in Elm's lab -- the lab script may "
                "have been in an unexpected state"
            )
        mon = self.r.mon(0)
        # The nickname prompt is declined in the text loop, so this should read
        # back as the species name; log it either way rather than assume.
        self.log(f"starter: got {mon.species_name} Lv{mon.level}, "
                 f"called {self.r.nickname(0)}")
        # Elm has follow-up dialogue (nickname prompt, errand) before you can leave.
        self.n.walk_to(5, 3)
        self.n.face("up")
        self.s.tap("a")
        self.c.run_scripts()

    # --- helpers -----------------------------------------------------------
    def _goto(self, x: int, y: int, expect: tuple[int, int] | None = None,
              talk: bool = False, push: str | None = None, tries: int = 6) -> None:
        """Walk to a warp tile, clearing blocking dialogue, until the map changes."""
        for _ in range(tries):
            if self.n.take_warp(x, y, push=push):
                if expect is None or self.r.location().key == expect:
                    return
            if self.r.in_battle():
                raise BootstrapError("unexpected battle during bootstrap navigation")
            if expect and self.r.location().key == expect:
                return
            if talk or self.c.script_running():
                # An NPC script (Mom, Elm's aide) is likely holding us up.
                self.c.run_scripts()
                self.n.settle()
        if expect and self.r.location().key != expect:
            raise BootstrapError(
                f"failed to reach map {expect} from {self._where()} "
                f"(target tile {x},{y})"
            )

    def _where(self) -> str:
        loc = self.r.location()
        return f"{self.r.gd.map_pretty(loc.group, loc.number)} ({loc.x},{loc.y})"
