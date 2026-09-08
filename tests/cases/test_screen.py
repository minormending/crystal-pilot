"""Reading the words on the screen.

Two kinds of test, and the split is the point. The ones that paint a screen and
read it back hold the *logic* honest -- matching, folding, the cursor row -- and
they paint through the reader's own charmap, so they cannot pass against a
second copy of a mistake in it. The ones that read a *live* screen are what hold
the charmap honest, because the only authority on what tile draws which letter
is the game.
"""
from pilot import screen as SC

from ..harness import test


@test("the charmap is parsed from the disassembly and agrees with the port's measurements")
def _(t):
    cm = SC.charmap(str(t.source))
    # The mobile port has only a ROM, so it measured these by dumping the
    # tilemap beside a screenshot. This one parses `constants/charmap.asm`.
    # Two independent readings agreeing is worth more than either.
    for tile, want in ((0x80, "A"), (0x99, "Z"), (0xA0, "a"), (0xB9, "z"),
                       (0xF6, "0"), (0xFF, "9"), (0x7F, " "),
                       (0xEA, "é"), (0xED, SC.CURSOR)):
        t.eq(cm.get(tile), want, f"${tile:02x}")
    t.gt(len(cm), 80, "and there are ~86 readable tiles")


@test("nothing below the font's first tile is treated as a letter")
def _(t):
    cm = SC.charmap(str(t.source))
    # The charmap names ids below $7f and they are not letters: $50 is the
    # string terminator, $60-$6c a bold alphabet from another sheet. Meanwhile
    # the tilemap uses that range for *pictures* -- the pack's item
    # illustration occupies $50-$5e -- so admitting them made a picture decode
    # as `@   #`, and a shop's failure message quoted it back at the reader.
    below = sorted(tile for tile in cm if tile < SC.FONT_FIRST)
    t.eq(below, [], f"nothing below ${SC.FONT_FIRST:02x}: {below}")


@test("the Japanese remap does not win the tiles the English block named")
def _(t):
    cm = SC.charmap(str(t.source))
    # `charmap.asm` re-uses a dozen English ids for kana that were never
    # translated. Read to the end of the file, $ea becomes the katakana small u
    # instead of the accented e in POKeMON and $e8 a Japanese full stop.
    # First definition wins, the same rule `SymbolTable` applies.
    t.eq(cm[0xEA], "é", "$ea is the accented e")
    t.eq(cm[0xE8], ".", "$e8 is a full stop")
    t.eq(cm[0xE6], "?", "$e6 is a question mark")


@test("a phrase matches within one row, never across two")
def _(t):
    p = t.pilot("route30")
    # Two unrelated lines that happen to abut are not a sentence. A box's last
    # line sitting above the text window's first would otherwise match phrases
    # nobody wrote.
    t.paint_screen(p, ["HEAL", "ING"])
    sc = p.reader.screen()
    t.true(sc.says("HEAL"), "the first row")
    t.true(sc.says("ING"), "and the second")
    t.false(sc.says("HEALING"), "but not the two joined")


@test("matching folds to letters and digits, so an accent is not a difference")
def _(t):
    p = t.pilot("route30")
    t.paint_screen(p, ["Restores POKéMON", "HP by 20."])
    sc = p.reader.screen()
    # The screen is not a string: POKeMON's accented letter is one tile and an
    # item count draws its own spaces. Folding makes a phrase match what a
    # person would say is on the screen.
    t.true(sc.says("POKEMON"), "POKéMON matches POKEMON")
    t.true(sc.says("restores pokemon"), "and case does not matter")
    t.true(sc.says("HP by 20"), "punctuation and spacing fold away")
    t.false(sc.says("HP by 21"), "but the digits still have to match")


@test("the arrow's row and the cursor variable agree exactly")
def _(t):
    p = t.pilot("route30")
    c = p.control
    ok, why = c.settle_for_menu()
    t.true(ok, why)
    c.close_menus()
    c.open_start_menu()
    p.session.tick(20)
    # Measured: cursor 1 through 5 put the arrow on tilemap rows 2, 4, 6, 8, 10.
    # That exact agreement is what lets a named row become a cursor value.
    for want in (1, 2, 3, 4, 5):
        t.true(c.drive_menu_cursor(want, 7), f"cursor to {want}")
        sc = p.reader.screen()
        t.eq(sc.cursor_row(), want * 2, f"arrow row for cursor {want}")
    c.close_menus()


@test("a menu entry is found by its name, wherever the menu has put it")
def _(t):
    p = t.pilot("route30")
    c = p.control
    c.settle_for_menu()
    c.close_menus()
    c.open_start_menu()
    p.session.tick(20)
    # The START menu grows as the game progresses -- no POKeDEX or POKeGEAR
    # early on -- so PACK is at a different index depending on how far things
    # have got. Its *name* does not move, which is why this replaces both
    # "try every row" and the claim that SAVE is third from the end.
    t.eq(c.menu_row_named("PACK"), 2, "PACK on this save")
    t.eq(c.menu_row_named("SAVE"), 5, "SAVE on this save")
    t.eq(c.menu_row_named("EXIT"), 7, "EXIT last")
    t.eq(c.menu_row_named("NOSUCHENTRY"), None, "a word that is not there")
    # And it agrees with the rule it replaces, on a save where the rule holds.
    t.eq(c.menu_row_named("SAVE"), c.menu_row_count() - 2, "SAVE is third from the end here")
    c.close_menus()


@test("the pack's real screen reads as the words a person would see")
def _(t):
    p = t.pilot("route30")
    gd = t.gamedata
    from pilot import items as I
    t.give_items(p, ((I.ITEM_POCKET, ((gd.item_id("POTION"), 1),)),))
    t.true(p.control.open_pack(), "pack open")
    sc = p.reader.screen()
    t.true(sc is not None, "the screen is readable")
    # This is the test that holds the *charmap* honest: the only authority on
    # what tile draws which letter is the game.
    t.true(sc.says("POTION"), "the item")
    t.true(sc.says("CANCEL"), "the cancel row")
    t.true(sc.says("Restores POKEMON"), "and the description under it")
    t.eq(sc.row_of("POTION"), sc.cursor_row(), "the arrow is on the item")
    p.control.close_menus()


@test("a failure carries what the game was actually saying")
def _(t):
    p = t.pilot("route30")
    t.paint_screen(p, ["", "", "Awakens sleeping", "POKéMON."])
    said = p.control.saying("could not find it")
    # "The USE box never appeared" says what the pilot expected. It does not say
    # what turned up instead, and that is the difference between a report
    # somebody can act on and one they can only re-run.
    t.contains(said, "could not find it", "the message survives")
    t.contains(said, "Awakens sleeping", "with the screen on the end")


@test("an unreadable screen leaves a message alone rather than decorating it")
def _(t):
    p = t.pilot("route30")
    real = p.reader.screen
    p.reader.screen = lambda: None
    try:
        t.eq(p.control.saying("plain"), "plain", "unchanged")
        t.eq(p.control.screen_said(), "", "and the excerpt is empty")
        t.eq(p.control.menu_row_named("PACK"), None, "and no row is claimed")
    finally:
        p.reader.screen = real


@test("a build whose symbols omit the tilemap reads as cannot-tell")
def _(t):
    # None rather than a blank screen, the same distinction `live_objects` and
    # `event_done` make: "cannot read" and "says nothing" lead somewhere
    # different for a caller appending this to a message.
    t.eq(SC.charmap("/nonexistent/source"), {},
         "no charmap without the disassembly")
    p = t.pilot("route30")
    t.eq(SC.read(p.session, "/nonexistent/source"), None, "and no screen")
