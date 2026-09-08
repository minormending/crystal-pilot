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
# Not listed: re-introducing the blind A tap in catch's _watch_throw. It was
# caught until the fixtures were rebuilt, and now no catch test notices it: the
# bug needs a stray A to land in the frames where the battle menu is up, and
# with the current fixture's RNG the three throws it takes to catch a SENTRET
# never line up that way. The guard in _watch_throw stays -- it was verified
# against the run that produced the bug (reported 2, consumed 3) -- but pretending
# the suite still proves it would be worse than saying it does not.
#
# Not listed: removing the settle from _await_menu_cursor. It used to be the
# whole defence, but choose_battle_action now re-reads the live cursor and steps
# toward the target, so dropping the settle self-corrects and no longer
# reproduces a bug. The read-verify loop is the real protection, and the first
# mutation below covers it.
MUTATIONS = [
    (
        # The fix is the *order*: LostBattle fires on the same tick
        # wBattleMode clears, so an "is it over?" test that runs first calls a
        # wipe a win -- and every other signal agrees with it, because a
        # white-out heals the party.
        "a battle the party lost is reported as won",
        "pilot/battle.py",
        '            if "lost_battle" in evs:\n'
        '                return "lost"\n'
        '            if not self.r.in_battle():\n'
        '                return "ended"',
        '            if not self.r.in_battle():\n'
        '                return "ended"\n'
        '            if "lost_battle" in evs:\n'
        '                return "lost"',
        "blacking out is reported as a loss",
    ),
    (
        "a grind collects its counts and drops them",
        "pilot/tasks/grind.py",
        # The comment comes with it: bare `res.stats = stats` at this indent is
        # a substring of the same line at the deeper indent used by an early
        # return, so the anchor would match twice and the mutation would skip.
        "            # correctly, thrown away at the last step.\n"
        "            res.stats = stats\n",
        "",
        "grind that did something reports",
    ),
    (
        # Thirteen structs, not sixteen: index 15 reads its coordinates from
        # inside wMapObjects, so an unspawned placement reads as somebody
        # standing on that tile.
        "the object structs are read past their end",
        "pilot/collision.py",
        "    STRUCT_COUNT = 13",
        "    STRUCT_COUNT = 16",
        "live read stops at NUM_OBJECT_STRUCTS",
    ),
    (
        # `property` can be `CANT_SELECT | CANT_TOSS`, and one word there drops
        # twenty-four rows without a word.
        "the item attribute pattern cannot read a flag expression",
        "pilot/items.py",
        'FIELD = r"[\\w\\s|]+?"',
        'FIELD = r"\\w+"',
        "attribute row in the file is parsed",
    ),
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
        'a tap in a battle is aimed at the 2x2 battle menu again',
        'pilot/webui.py',
        '            if self.p.reader.in_battle():\n                return "that is a battle, not the map", None\n            if self._window_open():',
        '            if self._window_open():',
        'battle is refused',
    ),
    (
        'a tap outside a menu moves its cursor anyway',
        'pilot/webui.py',
        '                if not (left <= text_col <= right and top <= text_row <= bottom):',
        '                if False:',
        'outside an open menu',
    ),
    (
        'the menu entry count trusts a byte the 2D menu reuses',
        'pilot/webui.py',
        '        return max(1, min(fits, self.p.session.rb("wMenuDataItems") or fits))',
        '        return max(1, self.p.session.rb("wMenuDataItems") or fits)',
        'means something else',
    ),
    (
        "a tap on a doorway reports the tile it left from",
        "pilot/webui.py",
        '        if through and (now.x, now.y) == (gx, gy):',
        '        if False and (now.x, now.y) == (gx, gy):',
        "doorway says which room",
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
        "parse_consts(pk, first_block_only=True).items()",
        "parse_consts(pk, first_block_only=False).items()",
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
        # Not a bug that fires today -- `TakeTask.run` calls `things_here()`
        # first and the two reads cannot disagree with no frames between them.
        # It is a shape that only has to be correct because of that, which is
        # why the check is static: no run of this program would find it.
        "take_here drops two keys on its early return",
        "pilot/travel.py",
        '''            return {"ok": True, "took": [], "empty": [], "unreachable": [],
                    "message": f"{here} has nothing left"}''',
        '''            return {"ok": True, "took": [],
                    "message": f"{here} has nothing left"}''',
        "returned on every path",
    ),
    (
        # The guard was correct. The rule is that a `.get` which has to stay
        # beside its subscript to be correct is a thing to keep correct forever.
        "an optional key is read by subscript behind a .get guard",
        "pilot/tasks/shop.py",
        '''        shop = out.get("shop")
        if shop:
            res.stats["shop"] = shop''',
        '''        if out.get("shop"):
            res.stats["shop"] = out["shop"]''',
        "never read by subscript",
    ),
    (
        # The two failures used to be one `False`, and the caller mapped it to
        # a blackout -- so a menu the pilot could not drive was written into the
        # log as a defeat, with the party still standing and the battle running.
        "a party list that will not drive is reported as a blackout",
        "pilot/battle.py",
        '''            out.note("the party list never drew; not pressing anything into it")
            return "stuck"''',
        '''            out.note("the party list never drew; not pressing anything into it")
            return "wiped"''',
        "reported as stuck",
    ),
    (
        # `_switch_to` was fixed to drive the cursor and check that it arrived.
        # `_send_next_mon` kept its own copy of the loop without the check at
        # the end, so a cursor that never got there still received an A -- and
        # sent out whatever happened to be highlighted.
        "a forced switch presses A without checking the cursor arrived",
        "pilot/battle.py",
        '''        if not self.c.drive_menu_cursor(slot + 1, len(party)):
            out.note(f"could not reach party slot {slot + 1} in the switch list")
            return "stuck"''',
        '''        for _ in range(len(party) + 2):
            if self.s.rb("wMenuCursorY") == slot + 1:
                break
            self.s.tap("down", hold=4, gap=6)''',
        "stops the switch before",
    ),
    (
        # The `speed` command printed the new speed and then had it reset on
        # the very next line of the loop, so it changed nothing and said
        # otherwise. `interactive.py` had no tests at all when this was found;
        # it was the top row of `tools/coverage`'s table.
        "the speed command is undone by the reset after every command",
        "pilot/interactive.py",
        "        self.p.session.pyboy.set_emulation_speed(self._play_speed)",
        "        self.p.session.pyboy.set_emulation_speed(1)",
        "survives the reset",
    ),
    (
        # Not a bug in the program -- a hole in the gate that guards it. Both
        # tools are Python with a shebang and no extension, and ruff lints such
        # a file when it is named but will not discover it from a directory. So
        # `ruff check .` passed for months without opening either, and naming
        # them found a real error in one on the first run.
        "the linter stops seeing the tools it never used to see",
        "ruff.toml",
        'extend-include = ["tools/coverage", "tools/docs-check"]',
        "extend-include = []",
        "covers every Python file",
    ),
    (
        # The narrower scope this replaced. It passes every rule and misses the
        # two tools, which is what makes it worth a mutation: the failure is
        # silent and reads as a clean run.
        "CI goes back to linting two directories instead of the repository",
        ".github/workflows/tests.yml",
        "        run: ruff check .",
        "        run: ruff check pilot tests",
        "lint the same scope",
    ),
    (
        # The bug the mobile port measured and this one shared: DARK CAVE is
        # two legs away through a route the pilot cannot climb, and every
        # refusal spent one of the fourteen legs until the walk gave up
        # somewhere it had never needed to be.
        "a refused leg is charged against the walk's arrival budget again",
        "pilot/travel.py",
        """            refusals += 1""",
        """            arrivals += 1""",
        "arrival budget",
    ),
    (
        # The words are on the screen until something presses them away, and
        # `walk_hop` has already run scripts by this point -- so reading late
        # reads nothing, which is how the first version of this failed.
        "a refusal stops being read, so nobody says who turned the walk back",
        "pilot/travel.py",
        """            said = self.c.screen_said(2)""",
        '''            said = ""''',
        "turned the walk back",
    ),
    (
        # A badge is the thing that opens one of these gates, and nothing
        # records which badge opened which. Keeping the write-offs across one
        # makes a route shut forever.
        "a won badge no longer re-opens the legs the game used to refuse",
        "pilot/travel.py",
        """            self.written_off = set()
            self._written_off_at = badges""",
        """            self._written_off_at = badges""",
        "re-opens every written-off leg",
    ),
    (
        # The party was the only evidence for years, and with six carried the
        # game boxes the seventh: the party never moves, one ball leaves the
        # bag, and a boxed catch reads exactly like a getaway.
        "the box stops being evidence, so a boxed catch reads as a getaway",
        "pilot/tasks/catch.py",
        """        now = self.r.box_count()
        if before_box is not None and now is not None and now > before_box:
            return "boxed\"""",
        """        now = self.r.box_count()
        if False:
            return "boxed\"""",
        "full party catches into",
    ),
    (
        # `sBoxCount` is `01:ad10` -- cartridge RAM, which is bank-switched.
        # Unbanked, the read returns whichever bank the game last mapped.
        "cartridge RAM is read unbanked again",
        "pilot/session.py",
        """        if addr in self.SRAM_SWITCHABLE:
            # Bank 0 is a real cartridge RAM bank, so `bank or default` would be
            # wrong here -- take what the symbol says.
            return bank, addr
""",
        "",
        "cartridge RAM bank",
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
