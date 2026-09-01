"""Proves the suite can fail.

A regression suite that passes no matter what is worse than none: it buys
confidence it has not earned. This re-introduces each bug the suite was written
for, one at a time, and checks that the test meant to catch it actually goes
red. Every mutation is reverted afterwards, including on failure.

    ./run-tests --self-check
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (label, file, find, replace, test-filter)
# `find` must appear exactly once, so a mutation cannot silently no-op after the
# code it targets has moved on.
# Not listed: removing the settle from _await_menu_cursor. It used to be the
# whole defence, but choose_battle_action now re-reads the live cursor and steps
# toward the target, so dropping the settle self-corrects and no longer
# reproduces a bug. The read-verify loop is the real protection, and the first
# mutation below covers it.
MUTATIONS = [
    (
        "move choice counts presses instead of reading the cursor",
        "pilot/control.py",
        '''        target = index + 1
        if not self._await_menu_cursor():
            return False
        for _ in range(n_moves + 2):
            if self.s.rb("wMenuCursorY") == target:
                break
            self.s.tap("down", hold=4, gap=6)''',
        '''        target = index + 1
        if not self._await_menu_cursor():
            return False
        self.s.repeat("up", 3, hold=4, gap=4)
        self.s.repeat("down", index, hold=4, gap=4)''',
        "strongest available move",
    ),
    (
        # Without the interception the intro mashes A into the NAME menu,
        # takes NEW NAME, and spells the player's name out of the grid.
        "the intro is left to mash A through the NAME menu",
        "pilot/control.py",
        '        if fired("name_player"):\n            self._name_menu_pending = True',
        '        if False:\n            self._name_menu_pending = True',
        "intro takes one of the game",
    ),
    (
        # The prompt's own default. Answering it the lazy way is not a crash --
        # it produces a party of Pokemon called AAAAA, which only a test that
        # reads the names back can see.
        "nickname prompts are answered with their default of YES",
        "pilot/control.py",
        "        self._nickname_armed = False\n        self.answer_yes_no(False)",
        "        self._nickname_armed = False\n        self.answer_yes_no(True)",
        "keeps the name the game gives it",
    ),
    (
        "battle engine ignores an already-open menu",
        "pilot/battle.py",
        "        pending = \"menu\" if menu_open else None",
        "        pending = None",
        "fled, not fought",
    ),
    (
        "species enum reads past the UNOWN restart",
        "pilot/gamedata.py",
        "_parse_consts(pk, first_block_only=True).items()",
        "_parse_consts(pk, first_block_only=False).items()",
        "UNOWN enum restart",
    ),
    (
        # Changing only the offset is not a real bug: calibrate() searches
        # candidates and finds the right one anyway. Corrupting the quadrant
        # picks the wrong byte within the block, which no offset compensates.
        "collision map reads the wrong quadrant of each block",
        "pilot/collision.py",
        "        quadrant = ((ty + oy) & 1) * 2 + ((tx + ox) & 1)",
        "        quadrant = 0",
        "all four quadrants",
    ),
    (
        "ledge tiles are excluded from pathfinding again",
        "pilot/collision.py",
        "                 allow_ledge: bool = True) -> bool:",
        "                 allow_ledge: bool = False) -> bool:",
        "ledges are standable",
    ),
    (
        "an absent trainer is reported as already beaten",
        "pilot/tasks/trainers.py",
        '''                if engaged == "absent":''',
        '''                if engaged == "never":''',
        "absent apart from unreachable",
    ),
    (
        "catch watches a throw by tapping A blindly",
        "pilot/tasks/catch.py",
        """        engine = self.search.fight
        for _ in range(4):
            what = engine.next_decision()""",
        """        engine = self.search.fight
        for _ in range(120):
            if not self.r.in_battle():
                self.s.tick(90)
                return ("caught" if self.r.party_count() > before_party
                        else "got_away")
            self.s.clear_events()
            self.s.tick(6)
            if self.s.has_event("battle_menu"):
                return None
            self.s.tap("a", hold=4, gap=8)
        for _ in range(0):
            what = engine.next_decision()""",
        "balls it spent",
    ),
]


def _run(filter_: str) -> bool:
    """True if the filtered tests all passed."""
    proc = subprocess.run(
        [sys.executable, "-m", "tests", "-k", filter_],
        cwd=ROOT, capture_output=True, text=True,
    )
    return proc.returncode == 0


def self_check() -> int:
    print("re-introducing known bugs to check the suite notices\n")
    caught, missed, skipped = 0, [], 0

    for label, relpath, find, replace, filt in MUTATIONS:
        path = ROOT / relpath
        original = path.read_text()
        if original.count(find) != 1:
            print(f"  SKIP  {label}\n        (anchor no longer matches "
                  f"{relpath} exactly once -- update selfcheck.py)")
            skipped += 1
            continue
        try:
            path.write_text(original.replace(find, replace, 1))
            still_passing = _run(filt)
        finally:
            path.write_text(original)

        if still_passing:
            missed.append((label, filt))
            print(f"  MISSED  {label}\n          (tests matching {filt!r} "
                  f"still passed)")
        else:
            caught += 1
            print(f"  caught  {label}")

    print(f"\n{caught} caught, {len(missed)} missed"
          + (f", {skipped} skipped" if skipped else ""))
    if missed:
        print("\nA missed mutation means that bug could come back unnoticed.")
    return 0 if not missed else 1


if __name__ == "__main__":
    sys.exit(self_check())
