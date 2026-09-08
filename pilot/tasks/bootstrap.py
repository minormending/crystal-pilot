"""Bootstrap a brand-new game up to the point where grinding is possible.

Needed because a fresh pokecrystal build has no save at all: without this there
is no party, no route, and nothing for the grind task to act on. Each stage is
verified against game memory (map id, party count) rather than trusted blindly,
so a stage that silently fails is reported instead of cascading.

Every coordinate this used to hardcode now comes from a title profile. That is
not tidying: the tiles are facts about *Crystal's script* rather than about the
Gen 2 engine, and as module constants here nothing could say so -- a hack that
moved Elm's lab got a pilot pressing A at a wall for four hundred taps and then
a report that the intro never finished. A profile that does not describe an
opening says `can_bootstrap = False`, and this refuses by name instead.
"""
from __future__ import annotations

from ..control import TEXT_EVENTS


class BootstrapError(RuntimeError):
    pass


class Bootstrap:
    def __init__(self, session, reader, control, nav, title, log=print):
        self.s = session
        self.r = reader
        self.c = control
        self.n = nav
        self.t = title
        self.log = log

    def require_title(self) -> None:
        """Refuse up front when the cartridge's opening is not described.

        Before any button is pressed, because the alternative is four hundred
        taps of A followed by a message that blames the ROM.
        """
        if not getattr(self.t, "can_bootstrap", False):
            raise BootstrapError(
                f"the {self.t.id} profile does not describe this cartridge's "
                f"opening, so a new game cannot be started on it. Everything "
                f"after the opening still works: point the pilot at an existing "
                f"save and grind, hunt, catch, shop or take as usual."
            )

    # --- stage 1: title -> named character in the bedroom ------------------
    def run_intro(self, max_menu_taps: int = 400) -> None:
        self.require_title()
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
        self.require_title()
        for leg in self.t.intro_legs:
            if self._const() != leg["on"]:
                continue
            self.log(f"route: {leg['on']} -> {leg['expect']}")
            self._goto(*leg["warp"], expect=leg["expect"],
                       talk=leg.get("talk", False), push=leg.get("push"))
        target = self.t.intro_legs[-1]["expect"] if self.t.intro_legs else None
        if target and self._const() != target:
            raise BootstrapError(
                f"could not reach {target}; stopped at {self._where()}")
        self.log(f"route: arrived {self._where()}")

    # --- stage 3: get a starter -------------------------------------------
    def get_starter(self, choice: str = "cyndaquil") -> None:
        self.require_title()
        choice = choice.lower()
        if choice not in self.t.starters:
            raise BootstrapError(
                f"unknown starter {choice!r}; {self.t.id} offers "
                f"{', '.join(sorted(self.t.starters))}"
            )
        if self.r.party_count() > 0:
            self.log("starter: party already non-empty, skipping")
            return
        self.log(f"starter: choosing {choice}")
        # Elm greets you on entry; let that script finish before moving.
        self.c.run_scripts()
        self._talk_to_elm()

        ball_x, ball_y = self.t.starters[choice]["ball"]
        for attempt in range(3):
            self.n.walk_to(ball_x, ball_y)
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
                f"could not pick up a starter in {self.t.lab['map']} -- the lab "
                f"script may have been in an unexpected state"
            )
        mon = self.r.mon(0)
        # The nickname prompt is declined in the text loop, so this should read
        # back as the species name; log it either way rather than assume.
        self.log(f"starter: got {mon.species_name} Lv{mon.level}, "
                 f"called {self.r.nickname(0)}")
        # Elm has follow-up dialogue (nickname prompt, errand) before you can leave.
        self._talk_to_elm()

    # --- helpers -----------------------------------------------------------
    def _talk_to_elm(self) -> None:
        self.n.walk_to(*self.t.lab["stand"])
        self.n.face("up")
        self.s.tap("a")
        self.c.run_scripts()

    def _goto(self, x: int, y: int, expect: str | None = None,
              talk: bool = False, push: str | None = None, tries: int = 6) -> None:
        """Walk to a warp tile, clearing blocking dialogue, until the map changes."""
        for _ in range(tries):
            if self.n.take_warp(x, y, push=push):
                if expect is None or self._const() == expect:
                    return
            if self.r.in_battle():
                raise BootstrapError("unexpected battle during bootstrap navigation")
            if expect and self._const() == expect:
                return
            if talk or self.c.script_running():
                # An NPC script (Mom, Elm's aide) is likely holding us up.
                self.c.run_scripts()
                self.n.settle()
        if expect and self._const() != expect:
            raise BootstrapError(
                f"failed to reach {expect} from {self._where()} "
                f"(target tile {x},{y})"
            )

    def _const(self) -> str:
        loc = self.r.location()
        return self.r.gd.map_name(loc.group, loc.number)

    def _where(self) -> str:
        loc = self.r.location()
        return f"{self.r.gd.map_pretty(loc.group, loc.number)} ({loc.x},{loc.y})"
