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


# --- the task wrapper -------------------------------------------------------
#
# Everything above drives `traveler.restock` directly, which is where the work
# happens -- and left `ShopTask` at 20% in `tools/coverage`'s table, the worst
# real module in the project. The wrapper is not nothing: it decides *what* to
# buy when nobody said, answers "no Mart sells that" without making the walk,
# and maps a restock's dict onto the `TaskResult` every front end renders.


@test("shop refuses an item no counter in the game stocks, without walking")
def _(t):
    p = t.pilot("route30")
    where = p.traveler.current_const()
    # Derived, not named: an item that exists and that no mart lists. Hardcoding
    # one would be a second copy of the mart tables.
    unsold = next(name for name in sorted(t.gamedata.items)
                  if not t.world.shops_selling(name))
    res = p.shop(item=unsold, want=1)
    t.note(f"chose {unsold}: {res.message}")
    t.eq(res.status, "blocked", "refused")
    t.contains(res.message, "not something a Mart sells", "and says why")
    t.eq(p.traveler.current_const(), where, "and went nowhere to find out")


@test("shop refuses a name that is not an item at all")
def _(t):
    p = t.pilot("route30")
    res = p.shop(item="NOSUCHITEM", want=1)
    t.eq(res.status, "blocked", "refused")
    t.contains(res.message, "NOSUCHITEM", "naming what was asked for")


def _task(p):
    from pilot.tasks.shop import ShopTask
    return ShopTask(p.session, p.reader, p.control, p.nav, p.world, p.gamedata,
                    p.traveler, p.saver, p.backups, log=lambda *a, **k: None)


@test("shop takes the shorthands a person would actually type")
def _(t):
    from pilot.tasks.shop import BALLS, POTIONS
    st = _task(t.pilot("route30"))
    # A shorthand means "the preference-ordered list", so a refused first
    # choice falls through to the second. `poke-balls` normalises to
    # POKE_BALLS, which is not an item -- it is the plural somebody types.
    for typed in ("balls", "BALL", "poke-balls", "pokeball"):
        t.eq(st._resolve(typed), BALLS, f"{typed!r} means the ball list")
    t.eq(st._resolve("potions"), POTIONS, "and potions means the potion list")
    # An exact item name is taken literally, including one that looks like a
    # shorthand: asking for POKE_BALL means that ball and not its upgrade.
    t.eq(st._resolve("POTION"), ("POTION",), "a real name is literal")
    t.eq(st._resolve("poke ball"), ("POKE_BALL",), "and so is POKE_BALL itself")


@test("shop with nothing named buys balls when there are none, potions when there are")
def _(t):
    from pilot.tasks.shop import BALLS, POTIONS
    p = t.pilot("route30")
    st = _task(p)
    t.eq(p.reader.carrying("POKE_BALL"), 0, "the fixture carries no balls")
    t.eq(st._resolve(None), BALLS, "so the default errand is balls")
    t.give_balls(p, entries=((t.gamedata.item_id("POKE_BALL"), 5),))
    t.eq(p.reader.carrying("POKE_BALL"), 5, "now it carries five")
    t.eq(st._resolve(None), POTIONS, "and the default moves on to potions")


@test("shop reports the wallet and what it spent, not just that it worked")
def _(t):
    p = t.pilot("route30", timeout=900)
    before = p.reader.money()
    res = p.shop(item="POTION", want=3)
    t.note(f"{res.status}: {res.message} / {res.stats}")
    t.eq(res.status, "completed", res.message)
    # The stats dict is what the CLI, the in-game menu and the web UI all
    # render. `grind` built its counts and never assigned them, which is the
    # bug this shape of assertion exists for.
    t.eq(res.stats["bought"], 2, "two Potions")
    t.eq(res.stats["spent"], 600, "at 300 each")
    t.eq(res.stats["wallet"], before - 600, "and the wallet agrees")
    t.eq(res.stats["shop"], "CHERRYGROVE_MART", "naming the counter")
    t.contains(res.stats["wanted"], "POTION", "and what was asked for")


@test("shop will not start while a battle is running")
def _(t):
    p = t.pilot("route30")
    t.into_wild_battle(p)
    res = p.shop(item="POTION", want=1)
    t.eq(res.status, "blocked", "refused")
    t.contains(res.message, "finish the battle first", "and says why")


@test("a purchase leaves the counter, not just closes its box")
def _(t):
    """The most expensive defect the mobile port logged, guarded here.

    Over there `buyFromClerk` ended by pressing B until no window was open,
    which is right for a menu the pilot opened and wrong for a box the *game*
    is holding up: the boxes closed, `wScriptMode` stayed non-zero, and the
    clerk's confirmation came back a moment later. The pilot was then standing
    in front of *"1 POKé BALL will be ¥200. OK?"* — and every later job runs
    `run_scripts`, which presses A through text. A on that box is a purchase.
    ¥3000 became ¥100, four balls at a time.

    This half ends with `advance_text` rather than `close_menus`, so it does not
    have that bug. But `window_open()` alone is what the test above checks, and
    that is exactly the assertion which passed over there while the wallet
    drained. So the claim worth pinning is the one that failed: the script is
    idle, and running scripts again costs nothing.
    """
    p = t.pilot("route30", timeout=900)
    out = p.traveler.restock(["POTION"], want=3)
    t.true(out["ok"], out["message"])
    after = p.reader.money()
    t.false(p.control.window_open(), "no box is up")
    t.false(p.control.script_running(), "and no script is still running")
    t.eq(p.session.rb("wScriptMode"), 0, "wScriptMode is back to zero")

    # What every later job does. If a confirmation were still being held up,
    # this is the press that would buy another one.
    p.control.run_scripts()
    p.nav.settle()
    t.eq(p.reader.money(), after, "and running scripts again spends nothing")
    t.false(p.control.script_running(), "with nothing left running")
