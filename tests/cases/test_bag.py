"""Driving the pack: using an item on the map and in a battle.

Every one of these needs the emulator, because the thing being tested is a
sequence of presses against boxes that only exist while the game is running.
They are also the tests that found the four measurements now written down in
`symbols.py` -- the field pack's button hold, the battle pack's different one,
the settle each party list needs, and the fact that the bag's count lags a use.

The standard throughout is **HP and the status byte, never the presses**. At
full HP the game takes every press, says the item would have no effect, spends
nothing and drops back to the pack; a press-counting test calls that a heal.
"""
from pilot import items as I
from pilot import symbols as S

from ..harness import test


def _hurt(p, hp: int, slot: int = 0) -> None:
    """Put a party member on `hp`, so an item has something to do."""
    base = p.session.sym.addr("wPartyMon1") + slot * S.PARTY_STRUCT_LEN
    p.session.wb(base + S.MON_HP, hp >> 8)
    p.session.wb(base + S.MON_HP + 1, hp & 0xFF)


def _afflict(p, status: str, slot: int = 0) -> None:
    base = p.session.sym.addr("wPartyMon1") + slot * S.PARTY_STRUCT_LEN
    p.session.wb(base + S.MON_STATUS, S.STATUS_BITS[status])


@test("a box is identified by its shape, not by something being open")
def _(t):
    p = t.pilot("route30")
    c = p.control
    t.false(c.window_open(), "nothing is open on the overworld")
    c.open_start_menu()
    t.true(c.window_open(), "the START menu is open")
    # The cursor keeps its value between boxes, so "a window is open" and "the
    # cursor is somewhere" are both true of the wrong box.
    t.false(c.is_box(S.BOX_PACK), "the START menu is not the pack")
    t.true(c.is_box((None, 0)), "a wildcard matches on the half it names")
    c.close_menus()


@test("the pack is found by checking, because its START row is not fixed")
def _(t):
    p = t.pilot("route30")
    # The START menu grows as the game progresses -- no POKeDEX or POKeGEAR
    # early on -- so PACK sits at a different index depending on how far things
    # have got. Measured on this save it is row 2 of 7; the point is that
    # nothing here believes that.
    t.true(p.control.open_pack(), "the pack opens")
    t.true(p.control.is_box(S.BOX_PACK), "and it really is the pack")
    p.control.close_menus()


@test("closing menus presses until they are shut, not a fixed number of times")
def _(t):
    p = t.pilot("route30")
    c = p.control
    t.true(c.open_pack(), "pack open")
    # A box swallows presses while it animates, so no count is right. Measured
    # after a heal -- party list inside the pack inside the START menu -- it
    # takes ten presses, which is why the old six left menus open.
    t.true(c.close_menus(), "closed")
    t.false(c.window_open(), "nothing left open")


@test("a Potion out of the bag heals, and the bag catches up afterwards")
def _(t):
    p = t.pilot("route30")
    gd = t.gamedata
    potion = gd.item_id("POTION")
    t.give_items(p, ((I.ITEM_POCKET, ((potion, 3),)),))
    _hurt(p, 9)
    before = p.reader.mon(0)
    t.true(p.control.use_item_on(potion, 0), "the presses landed")
    after = p.reader.mon(0)
    t.eq(after.hp - before.hp, 20, "a Potion is exactly 20HP")
    # The count is corroboration and it lags: measured, `wItems` still listed
    # the Potion while the HP had already moved, and only settled once the pack
    # closed. Which is the same warning this repo carries about wBalls.
    t.eq(p.reader.item_count(potion), 2, "and one was spent")
    t.false(p.control.window_open(), "and the pack is shut again")


@test("at full HP the game spends nothing, and that is not a heal")
def _(t):
    p = t.pilot("route30")
    gd = t.gamedata
    potion = gd.item_id("POTION")
    t.give_items(p, ((I.ITEM_POCKET, ((potion, 3),)),))
    before = p.reader.mon(0)
    t.eq(before.hp, before.max_hp, "this fixture starts at full HP")
    p.control.use_item_on(potion, 0)
    t.eq(p.reader.mon(0).hp, before.hp, "no HP moved")
    # The presses all land. The item is not spent, because the game refuses --
    # which is exactly why the caller has to ask about HP rather than presses.
    t.eq(p.reader.item_count(potion), 3, "nothing was spent")


@test("a cure clears the status byte, which is the only thing it changes")
def _(t):
    p = t.pilot("route30")
    gd = t.gamedata
    for item, status in (("ANTIDOTE", "PSN"), ("AWAKENING", "SLP"),
                         ("FULL_HEAL", "PAR")):
        iid = gd.item_id(item)
        t.give_items(p, ((I.ITEM_POCKET, ((iid, 2),)),))
        _afflict(p, status)
        t.eq(p.reader.mon(0).status_name, status, f"afflicted with {status}")
        t.true(p.control.use_item_on(iid, 0), f"{item} drove")
        t.eq(p.reader.mon(0).status_name, "OK", f"{item} cleared {status}")


@test("a Potion mid-battle heals the thing on the field")
def _(t):
    p = t.pilot("route30")
    gd = t.gamedata
    potion = gd.item_id("POTION")
    t.give_items(p, ((I.ITEM_POCKET, ((potion, 3),)),
                     (I.BALL_POCKET, ((gd.item_id("POKE_BALL"), 5),))))
    t.into_wild_battle(p)
    # Both copies: wBattleMonHP is what the battle draws from, the party struct
    # is what survives it.
    p.session.wb("wBattleMonHP", 0)
    p.session.wb(p.session.sym.addr("wBattleMonHP") + 1, 9)
    _hurt(p, 9)
    t.true(p.control.use_item_in_battle(potion, 0), "the presses landed")
    p.session.tick(60)
    t.eq(p.reader.battle().active_hp, 29, "healed on the field")
    t.true(p.reader.in_battle(), "and still in the battle")


@test("the battle pack's boxes are not the field pack's boxes")
def _(t):
    from pilot.control import PACK
    p = t.pilot("route30")
    gd = t.gamedata
    potion = gd.item_id("POTION")
    t.give_items(p, ((I.ITEM_POCKET, ((potion, 3),)),
                     (I.BALL_POCKET, ((gd.item_id("POKE_BALL"), 5),))))
    t.into_wild_battle(p)
    c = p.control
    t.true(c.choose_battle_action(PACK), "the battle pack opens")
    p.session.tick(60)
    t.true(c.reach_pocket(c.ITEM_POCKET), "reaches the item pocket")
    t.true(c.reach_item(potion), "and the Potion")
    p.session.tap("a")
    # The field pack's item submenu is four rows at row 3 (USE/GIVE/TOSS/QUIT).
    # The battle pack's is two at row 7 (USE/QUIT) -- a different box for the
    # same press, which is why one routine cannot drive both.
    t.true(c.await_box(S.BOX_BATTLE_ITEM, tries=20), "USE / QUIT, two rows at 7")
    t.false(c.is_box(S.BOX_ITEM_USE), "not the field pack's four-row box")
    c.close_menus()


@test("walking past an item in the pack is reported, not pressed through")
def _(t):
    p = t.pilot("route30")
    gd = t.gamedata
    potion = gd.item_id("POTION")
    t.give_items(p, ((I.ITEM_POCKET, ((potion, 1),)),))
    t.true(p.control.open_pack(), "pack open")
    t.true(p.control.reach_pocket(p.control.ITEM_POCKET), "item pocket")
    # DOWN past the last entry lands on CANCEL and *stays* there -- the list
    # does not wrap -- so asking for something absent must not spin forever or
    # confirm whatever the cursor is on.
    absent = gd.item_id("MAX_POTION")
    t.false(p.control.reach_item(absent, tries=6),
            "an item that is not in the bag cannot be reached")
    p.control.close_menus()


@test("a use is driven, not counted, all the way down to the party list")
def _(t):
    p = t.pilot("route30")
    gd = t.gamedata
    potion = gd.item_id("POTION")
    t.give_items(p, ((I.ITEM_POCKET, ((potion, 2),)),))
    _hurt(p, 9)
    c = p.control
    t.true(c.open_pack(), "pack")
    t.true(c.reach_pocket(c.ITEM_POCKET), "pocket")
    t.true(c.reach_item(potion), "item")
    c._pack_confirm()
    t.true(c.await_box(S.BOX_ITEM_USE), "USE / GIVE / TOSS / QUIT")
    t.true(c.drive_menu_cursor(S.PACK_USE_ROW, 4), "cursor on USE")
    c._pack_confirm()
    # The party list's row count is not a constant -- four rows here, two in a
    # battle, same one-Pokemon party -- so it is matched on where it starts.
    t.true(c.await_box(S.BOX_PARTY_PICK), "the party list came up")
    t.true(c.drive_menu_cursor(1, S.MAX_PARTY), "cursor on slot 1")
    p.session.tap("a")
    p.session.tick(90)
    t.eq(p.reader.mon(0).hp, 29, "and that is what heals it")
    c.close_menus()


@test("a running map script is settled, not reported as the pack refusing")
def _(t):
    # START is ignored while wScriptMode is non-zero. The `grass_cyndaquil`
    # fixture sits with a script running, and before this four consecutive
    # START presses did nothing at all -- then `menu_row_count` walked the
    # player through grass looking for rows that were not there, and the caller
    # was told the pack would not open.
    p = t.pilot("grass_cyndaquil", timeout=300)
    t.true(p.control.script_running(), "this fixture has a script running")
    ok, why = p.control.settle_for_menu()
    t.true(ok, f"it settles: {why}")
    t.false(p.control.script_running(), "and the script is finished")
    t.give_items(p, ((I.ITEM_POCKET, ((t.gamedata.item_id("POTION"), 3),)),))
    t.true(p.control.open_pack(), "so the pack opens")
    t.true(p.control.is_box(S.BOX_PACK), "and it really is the pack")
    p.control.close_menus()


@test("the in-game save settles first rather than failing three times")
def _(t):
    # Three attempts against a running script all fail identically and report
    # "did not commit", which blames the save menu for a script that had not
    # finished. The settle was already written, in `Pilot.settle_for_save`, and
    # wired only to the three front ends -- not to the saver itself.
    p = t.pilot("grass_cyndaquil", timeout=300)
    t.true(p.control.script_running(), "a script is running")
    t.true(p.saver.save_in_game(), "the save commits anyway")


@test("settling refuses a battle by name instead of waiting it out")
def _(t):
    p = t.pilot("route30", timeout=600)
    t.give_balls(p)
    t.into_wild_battle(p)
    ok, why = p.control.settle_for_menu(rounds=2)
    t.false(ok, "it cannot settle mid-battle")
    t.contains(why, "battle", "and says that is why")
