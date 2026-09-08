"""The bag: what the disassembly says items do, and what the pockets hold.

Most of this needs no emulator -- it is the item tables read out of
data/items/*.asm. The two that do need one read the live pockets and the
wallet, which is the half that cannot be checked by parsing anything.
"""
from pilot import items as I

from ..harness import test


@test("item attributes carry the price, the pocket and where it can be used")
def _(t):
    a = I.attributes(str(t.source))
    t.gt(len(a), 200, "there are ~250 items")
    potion = a["POTION"]
    t.eq(potion["price"], 300, "a Potion is 300")
    t.eq(potion["pocket"], I.ITEM_POCKET, "a Potion is in the item pocket")
    t.eq(potion["field_menu"], I.MENU_PARTY, "usable on a party member outside battle")
    t.eq(potion["battle_menu"], I.MENU_PARTY, "and inside one")
    t.eq(a["POKE_BALL"]["pocket"], I.BALL_POCKET, "a Poke Ball is in the ball pocket")
    t.eq(a["BICYCLE"]["pocket"], I.KEY_ITEM_POCKET, "a Bicycle is a key item")


@test("the two prices that are not prices are not treated as prices")
def _(t):
    a = I.attributes(str(t.source))
    # A Master Ball has no price at all; the Town Map carries the $9999
    # sentinel, which read naively makes it the costliest item in the game.
    t.eq(a["MASTER_BALL"]["price"], 0, "Master Ball price")
    t.false(a["MASTER_BALL"]["for_sale"], "no counter sells a Master Ball")
    t.eq(a["TOWN_MAP"]["price"], I.NOT_FOR_SALE, "the sentinel is read as-is")
    t.false(a["TOWN_MAP"]["for_sale"], "but it does not count as for sale")
    t.eq(I.price(str(t.source), "TOWN_MAP"), 0, "and its price reports as zero")


@test("how much each thing heals, including the two that heal everything")
def _(t):
    h = I.heals_hp(str(t.source))
    t.eq(h["POTION"], 20, "a Potion is 20HP")
    t.eq(h["SUPER_POTION"], 50, "a Super Potion is 50HP")
    t.eq(h["HYPER_POTION"], 200, "a Hyper Potion is 200HP")
    # MAX_STAT_VALUE in the table, not a number. Read as 999 it would make a
    # Max Potion look *worse* than a Hyper Potion on anything under 999 max HP,
    # which is every Pokemon in the game.
    t.eq(h["MAX_POTION"], I.FULL, "a Max Potion heals all of it")
    t.eq(h["FULL_RESTORE"], I.FULL, "so does a Full Restore")


@test("which status each cure clears, sleep included")
def _(t):
    c = I.cures_status(str(t.source))
    t.eq(sorted(c["ANTIDOTE"]), ["PSN"], "an Antidote is poison only")
    t.eq(sorted(c["ICE_HEAL"]), ["FRZ"], "an Ice Heal is freeze only")
    # Sleep is a three-bit counter rather than a bit, so it appears in the
    # source as SLP_MASK and never as `1 << SLP`. Matching only shift
    # expressions would leave Awakening curing nothing.
    t.eq(sorted(c["AWAKENING"]), ["SLP"], "an Awakening wakes something up")
    t.eq(len(c["FULL_HEAL"]), 5, "a Full Heal clears all five")


@test("a narrow cure is spent before the one that fixes everything")
def _(t):
    # Sorting these by price alone puts MIRACLEBERRY first, because it is
    # cheaper than an Antidote -- and then the one item that answers all five
    # statuses gets spent on the status with five other answers.
    psn = I.cures(str(t.source), "PSN")
    t.true("ANTIDOTE" in psn, "an Antidote cures poison")
    t.lt(psn.index("ANTIDOTE"), psn.index("MIRACLEBERRY"),
         "the Antidote comes before the cure-all")
    t.lt(psn.index("ANTIDOTE"), psn.index("FULL_HEAL"),
         "and before the Full Heal")
    t.eq(psn[-1], "FULL_RESTORE", "the thing that also heals HP goes last")


@test("only what can actually be used where you are standing is offered")
def _(t):
    src = str(t.source)
    field = I.healers(src)
    battle = I.healers(src, in_battle=True)
    t.true("POTION" in field, "a Potion works in the field")
    t.true("POTION" in battle, "and in a battle")
    # A Berry heals HP but is ITEMMENU_NOUSE in the field, so offering it
    # outside a battle is offering a press that silently does nothing.
    t.false("REVIVE" in field, "a Revive is not an HP heal for a live mon")
    for name in field:
        t.eq(I.attributes(src)[name]["field_menu"], I.MENU_PARTY,
             f"{name} is field-usable")


@test("mart stock lists do not begin with their own count byte")
def _(t):
    m = I.mart_items(str(t.source))
    t.gt(len(m), 30, "there are ~34 mart inventories")
    # Every list opens `db 4` before the items. A pattern that allows a leading
    # digit reads that as an item called "4", and the shopping errand's first
    # candidate is then a name no item table has.
    t.eq(m["MartCherrygrove"],
         ("POTION", "ANTIDOTE", "PARLYZ_HEAL", "AWAKENING"),
         "Cherrygrove's four")
    numeric = [n for stock in m.values() for n in stock if n[0].isdigit()]
    t.eq(numeric, [], "no stock entry is a number")


@test("the pocket reader is only pointed at pockets it can decode")
def _(t):
    # A key item has no quantity byte -- wKeyItems is d8bd and wNumBalls is
    # d8d7, twenty-six bytes later, which is MAX_KEY_ITEMS plus a terminator
    # rather than twice that. Handing it to the pair reader would report half
    # the pocket as quantities of the other half, in plausible-looking numbers.
    t.true(I.ITEM_POCKET in I.POCKET_SYMBOLS, "the item pocket is readable")
    t.true(I.BALL_POCKET in I.POCKET_SYMBOLS, "so is the ball pocket")
    t.false(I.KEY_ITEM_POCKET in I.POCKET_SYMBOLS, "key items are not pairs")
    t.false(I.TM_HM_POCKET in I.POCKET_SYMBOLS, "and TM/HM is a bitfield")
    for which in I.POCKET_SYMBOLS:
        t.true(which in I.POCKET_LIMITS, f"pocket {which} has a limit")


@test("the wallet reads as binary, not as the BCD Gen 1 used")
def _(t):
    p = t.pilot("route30")
    money = p.reader.money()
    t.gte(money, 0, "money is not negative")
    t.lte(money, 999_999, "and not above MAX_MONEY")
    # Written big-endian binary; decoded as packed BCD, 3000 reads as 3000's
    # nibbles and comes out wrong. Write a known value and read it back.
    for value in (0, 1, 300, 3000, 123_456, 999_999):
        hi, mid, lo = (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF
        base = p.session.sym.addr("wMoney")
        p.session.wb(base, hi)
        p.session.wb(base + 1, mid)
        p.session.wb(base + 2, lo)
        t.eq(p.reader.money(), value, f"reads back {value}")


@test("the item pocket is read, not just the ball pocket beside it")
def _(t):
    p = t.pilot("route30")
    gd = t.gamedata
    potion, super_potion = gd.item_id("POTION"), gd.item_id("SUPER_POTION")
    poke_ball = gd.item_id("POKE_BALL")
    t.give_items(p, ((I.ITEM_POCKET, ((potion, 3), (super_potion, 2))),
                     (I.BALL_POCKET, ((poke_ball, 7),))))
    items = {gd.item_name(i): q for i, q in p.reader.items()}
    balls = {gd.item_name(i): q for i, q in p.reader.balls()}
    t.eq(items.get("POTION"), 3, "three Potions")
    t.eq(items.get("SUPER_POTION"), 2, "two Super Potions")
    t.eq(balls.get("POKE_BALL"), 7, "seven Poke Balls")
    t.eq(p.reader.item_count(potion), 3, "item_count finds the Potions")
    t.eq(p.reader.ball_count(poke_ball), 7, "ball_count finds the balls")
    # The point of `carrying`: the caller does not have to know which pocket a
    # name lives in, which is the one genuinely arbitrary fact about the bag.
    t.eq(p.reader.carrying("POTION"), 3, "carrying finds an item by name")
    t.eq(p.reader.carrying("POKE_BALL"), 7, "and a ball by name")
    t.eq(p.reader.carrying("ULTRA_BALL"), 0, "and says zero for what is absent")


@test("a pocket stops at its own end rather than reading into the next")
def _(t):
    p = t.pilot("route30")
    # A corrupt or mid-write count byte is the case that matters: wNumBalls
    # reading 20 (the item pocket's limit) would take the reader eight entries
    # past wBalls and report key items as balls.
    p.session.wb("wNumBalls", 20)
    base = p.session.sym.addr("wBalls")
    for i in range(12):
        p.session.wb(base + i * 2, 5)
        p.session.wb(base + i * 2 + 1, 1)
    t.lte(len(p.reader.balls()), I.POCKET_LIMITS[I.BALL_POCKET],
          "never more entries than the pocket holds")
