"""Every parser over the disassembly, counted against its own source.

This file exists because of one failure mode shared by every regex in this
project: **a pattern that stops matching is invisible.** Nothing crashes, no
value is wrong, there is simply less of it -- and the symptom arrives much
later, as an item the pilot cannot see or a map it cannot leave.

Two were found this way and neither had any other sign:

  * `item_attribute`'s `property` field can be `CANT_SELECT | CANT_TOSS`, and a
    pattern expecting one word there matched 232 of 256 rows. Twenty-two key
    items vanished, and `carrying` silently reported them as item-pocket items.
  * `warp_event`'s destination index can be `-1`, and `(\\d+)` refuses it. Six
    warps vanished, taking four real edges out of the Celadon and Goldenrod
    elevators with them -- a dropped edge is a place the router cannot leave.

So each parser is checked against a count taken from the files themselves. The
counts are deliberately derived rather than written down: a hardcoded total
would need updating whenever the disassembly moves, and would then be updated
to whatever the parser currently produces, which is not a check at all.
"""
import re
from pathlib import Path

from pilot import items as I
from pilot import world as W

from ..harness import test


def _lines(t, rel):
    return (Path(t.source) / rel).read_text(errors="replace").splitlines()


def _count(t, rel, pattern):
    return sum(1 for line in _lines(t, rel) if re.match(pattern, line))


@test("every move row reaches the move table")
def _(t):
    rows = _count(t, "data/moves/moves.asm", r"^\s*move\s")
    t.gt(rows, 200, "there are ~251 moves")
    t.eq(len(t.gamedata.move_info), rows, "and every row is parsed")


@test("every healing and cure row reaches its table, terminators aside")
def _(t):
    src = str(t.source)
    # Both tables end in a `db -1` / `dbw -1` sentinel, which is a row in the
    # file and not an item. One off each, and only one.
    hp_rows = _count(t, "data/items/heal_hp.asm", r"^\s*dbw\s")
    t.eq(len(I.heals_hp(src)), hp_rows - 1, "HP amounts, less the terminator")
    st_rows = _count(t, "data/items/heal_status.asm", r"^\s*db\s")
    t.eq(len(I.cures_status(src)), st_rows - 1, "cures, less the terminator")


@test("every mart label reaches the stock table, the header aside")
def _(t):
    # `Marts:` is the pointer table's own label and matches `^Mart` too, which
    # is why this is off by exactly one and must stay off by exactly one.
    labels = _count(t, "data/items/marts.asm", r"^Mart")
    t.eq(len(I.mart_items(str(t.source))), labels - 1, "34 counters, not 35")


@test("every warp in the game reaches the world graph")
def _(t):
    # The `-1` destination. Six of these, and losing them cost four edges out
    # of two department store elevators.
    rows = 0
    for f in sorted((Path(t.source) / "maps").glob("*.asm")):
        rows += len(re.findall(r"^\s*warp_event\s", f.read_text(errors="replace"),
                               re.MULTILINE))
    parsed = sum(len(v) for v in t.world.warps.values())
    t.gt(rows, 1200, "there are ~1,312 warps")
    t.eq(parsed, rows, f"all of them are parsed, not {parsed} of {rows}")


@test("a script-decided warp destination is kept, and marked as one")
def _(t):
    elevator = t.world.warps["CELADON_DEPT_STORE_ELEVATOR"]
    t.eq(len(elevator), 2, "the elevator has two doors")
    for warp in elevator:
        t.eq(warp["to"], "CELADON_DEPT_STORE_1F", "both lead to the ground floor")
        # None rather than -1, so a caller can tell "come out at warp 3" from
        # "the script decides".
        t.eq(warp["to_warp"], None, "and the script decides where")
    t.gt(len(t.world.neighbours("CELADON_DEPT_STORE_ELEVATOR")), 0,
         "so the elevator is a place the graph can leave")


@test("every trainer, item ball and fruit tree in the game is found")
def _(t):
    text = "".join(f.read_text(errors="replace")
                   for f in sorted((Path(t.source) / "maps").glob("*.asm")))
    w = t.world
    t.eq(sum(len(v) for v in w.trainers.values()),
         len(re.findall(r"OBJECTTYPE_TRAINER", text)), "trainers")
    balls = sum(1 for v in w.takeables.values() for x in v if x["kind"] == "ball")
    t.eq(balls, len(re.findall(r"OBJECTTYPE_ITEMBALL", text)), "item balls")
    trees = sum(1 for v in w.takeables.values() for x in v if x["kind"] == "tree")
    t.eq(trees, len(re.findall(r"SPRITE_FRUIT_TREE", text)), "fruit trees")
    t.eq(len(w.nurses), len(re.findall(r"SPRITE_NURSE", text)), "nurses")


@test("a map file the graph cannot name is not silently ignored")
def _(t):
    # Warps are found by joining a file name to a map constant, so a file whose
    # CamelCase does not fold onto a constant contributes nothing at all --
    # which would look exactly like a map with no warps.
    by_camel = {c.replace("_", "").lower(): c for c in t.gamedata.maps_by_name}
    unnamed = []
    for f in sorted((Path(t.source) / "maps").glob("*.asm")):
        if by_camel.get(f.stem.replace("_", "").lower()):
            continue
        if re.search(r"^\s*warp_event\s", f.read_text(errors="replace"), re.MULTILINE):
            unnamed.append(f.name)
    t.eq(unnamed, [], f"map files with warps and no constant: {unnamed}")


@test("the patterns that read a flag expression accept one")
def _(t):
    # The two shapes that broke a parser here, kept as cases rather than only
    # as counts, so a failure says *which* shape stopped matching.
    t.true(I.ITEM_ATTR.match(
        "\titem_attribute 0, HELD_NONE, 0, CANT_SELECT | CANT_TOSS, KEY_ITEM, "
        "ITEMMENU_CLOSE, ITEMMENU_NOUSE"), "a combined property field")
    t.true(I.ITEM_ATTR.match(
        "\titem_attribute 3000, HELD_NONE, -1, CANT_SELECT, ITEM, "
        "ITEMMENU_PARTY, ITEMMENU_PARTY"), "and a negative parameter")
    t.true(W.WARP.match("\twarp_event  1,  3, CELADON_DEPT_STORE_1F, -1"),
           "a script-decided warp destination")
    t.true(W.WARP.match("\twarp_event  2,  7, CHERRYGROVE_CITY, 1"),
           "and an ordinary one")
