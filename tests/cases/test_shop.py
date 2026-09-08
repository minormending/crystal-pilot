"""Buying things: the mart tables, and driving a real counter.

The data half needs no emulator. The two that drive a counter walk from Route 30
to Cherrygrove Mart, which is the errand this repository spent its whole life
telling people to run by hand.
"""
from pilot import items as I

from ..harness import test


@test("mart stock joins the pointer table to the MART_ constants by position")
def _(t):
    stock = I.mart_stock(str(t.source))
    t.gt(len(stock), 30, "there are ~34 counters")
    # `Marts:` is a table of `dw MartCherrygrove` pointers in MART_* constant
    # order, so index 0 of the table is the first constant. Getting that
    # off-by-one wrong sells Cherrygrove's stock at Violet.
    t.eq(stock["MART_CHERRYGROVE"],
         ("POTION", "ANTIDOTE", "PARLYZ_HEAL", "AWAKENING"), "Cherrygrove")
    t.contains(stock["MART_VIOLET"], "POKE_BALL", "Violet sells balls")


@test("a map with two counters is offered as the union of both")
def _(t):
    # Cherrygrove's clerk has two stock lists behind an event flag, and which
    # one a visit gets depends on story progress nothing here can read. So the
    # union chooses the destination and the counter itself is the authority.
    both = I.sold_at(str(t.source), ("MART_CHERRYGROVE", "MART_CHERRYGROVE_DEX"))
    t.contains(both, "POTION", "in both lists, listed once")
    t.contains(both, "POKE_BALL", "only in the second")
    t.eq(len(both), len(set(both)), "and nothing is listed twice")


@test("shops are found in the world with a clerk to talk to")
def _(t):
    w = t.world
    t.gt(len(w.shops), 20, "there are ~22 standard counters")
    cherrygrove = w.shops["CHERRYGROVE_MART"]
    t.eq(cherrygrove["clerk"], (1, 3), "Cherrygrove's clerk")
    t.contains(cherrygrove["marts"], "MART_CHERRYGROVE", "and what it stocks")
    # A `pokemart` line with no clerk is not somewhere the pilot can shop, and
    # a clerk with no `pokemart` sells nothing. Both halves or neither.
    for const, shop in w.shops.items():
        t.true(shop["clerk"] is not None, f"{const} has a clerk")
        t.true(len(shop["marts"]) > 0, f"{const} stocks something")


@test("only counters the pilot can actually drive are recorded")
def _(t):
    # A bargain shop sells one of each and a pharmacy has its own quantity box,
    # so `buy_from_clerk`'s five-box sequence does not describe them. A shop
    # the pilot cannot drive is worse than no shop, because it is a place it
    # will walk to.
    w = t.world
    t.false("GOLDENROD_UNDERGROUND_WAREHOUSE" in w.shops,
            "the bargain shop is not offered")
    for const in w.shops:
        t.true(w.shops_selling("POTION") is not None, f"{const} is queryable")


@test("several destinations cost one graph search, not one each")
def _(t):
    w = t.world
    targets = ["VIOLET_MART", "CHERRYGROVE_MART", "AZALEA_MART"]
    batch = w.routes_from("ROUTE_30", targets)
    for target in targets:
        single = w.route_to("ROUTE_30", lambda c, x=target: c == x, max_depth=8)
        t.eq(len(batch[target]), len(single), f"{target} agrees with route_to")


@test("the map you are standing on is never a destination")
def _(t):
    w = t.world
    # An absent key means unreachable; a zero-length route would mean "already
    # here", which is not a journey and would send `travel_to` nowhere.
    t.false("ROUTE_30" in w.routes_from("ROUTE_30", ["ROUTE_30"]),
            "not a journey")
    t.eq(w.routes_from("ROUTE_30", []), {}, "and nothing at all walks no graph")


@test("the nearest counter stocking something wins on legs")
def _(t):
    w = t.world
    found = w.nearest_shop_for("ROUTE_30", ["POTION"])
    t.true(found is not None, "somewhere sells a Potion")
    shop, item, route = found
    t.eq(shop, "CHERRYGROVE_MART", "and it is the nearest one")
    t.eq(item, "POTION", "for the thing asked about")
    # Standing in a shop that stocks it is a zero-leg answer, not a walk.
    t.eq(w.nearest_shop_for("CHERRYGROVE_MART", ["POTION"])[2], [],
         "already there")
    t.eq(w.nearest_shop_for("ROUTE_30", ["MASTER_BALL"]), None,
         "nothing sells a Master Ball")


@test("the wallet is checked before the walk, not at the counter")
def _(t):
    p = t.pilot("route30")
    base = p.session.sym.addr("wMoney")
    for i in range(3):
        p.session.wb(base + i, 0)
    where = p.traveler.current_const()
    out = p.traveler.restock(["POTION"], want=3)
    t.false(out["ok"], "refused")
    t.contains(out["message"], "300", "and says what it costs")
    # Walking four maps to find out you cannot afford one Potion is the same
    # amount of walking as affording it, and a worse answer.
    t.eq(p.traveler.current_const(), where, "without going anywhere")


@test("an errand that is already done is done, not an error")
def _(t):
    p = t.pilot("route30")
    gd = t.gamedata
    t.give_items(p, ((I.ITEM_POCKET, ((gd.item_id("POTION"), 9),)),))
    where = p.traveler.current_const()
    out = p.traveler.restock(["POTION"], want=5)
    t.true(out["ok"], "reported as done")
    t.eq(out["bought"], 0, "having bought nothing")
    t.eq(p.traveler.current_const(), where, "and gone nowhere")


@test("walking to a Mart and buying two Potions moves the wallet")
def _(t):
    p = t.pilot("route30", timeout=900)
    t.eq(p.reader.money(), 3000, "this fixture starts with 3000")
    t.eq(p.reader.carrying("POTION"), 1, "and one Potion")
    out = p.traveler.restock(["POTION"], want=3)
    t.true(out["ok"], out["message"])
    # **The money is the evidence.** The pocket lags a purchase the way it lags
    # a use, so a test that only checked the bag would be racing the game.
    t.eq(out["spent"], 600, "two Potions at 300 each")
    t.eq(p.reader.money(), 2400, "and the wallet says so")
    t.eq(p.reader.carrying("POTION"), 3, "three Potions now")
    t.eq(p.traveler.current_const(), "CHERRYGROVE_MART", "at the counter")
    t.false(p.control.window_open(), "with the menus shut behind it")


@test("a counter not stocking something says where else to try")
def _(t):
    p = t.pilot("route30", timeout=900)
    # Cherrygrove keeps Poké Balls behind the Mystery Egg flag, so its *listed*
    # stock and its real stock differ for the whole early game. "The counter
    # took nothing" sends somebody looking for a driving bug.
    out = p.traveler.restock(["POKE_BALL"], want=4)
    t.false(out["ok"], "it could not buy any")
    t.contains(out["message"], "not stocking it today", "and says why")
    t.contains(out["message"], "also lists it", "and names somewhere else")
