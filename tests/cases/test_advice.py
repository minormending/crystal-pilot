"""Answering "this grind is slow" with somewhere to go or an hour to wait for.

All of this reads the disassembly, so none of it needs an emulator except the
two that check the advice reaches a caller.

The numbers are the real cartridge's: Route 29's grass is Lv2-3, Route 30's is
Lv3-4, Route 31's is Lv4-5, and Route 27 -- which the graph genuinely reaches in
two maps through New Bark -- is Lv28-32.
"""
from pilot import advice, wild

from ..harness import test


@test("a route's grass has a level range, and a town has none")
def _(t):
    src = str(t.source)
    t.eq(wild.level_range(src, "ROUTE_29"), (2, 3), "Route 29")
    t.eq(wild.level_range(src, "ROUTE_30"), (3, 4), "Route 30")
    t.eq(wild.level_range(src, "ROUTE_31"), (4, 5), "Route 31")
    # None, not (0, 0): "no wild Pokemon here" and "wild Pokemon at level zero"
    # are different claims, and a caller ranking places has to drop the first.
    t.eq(wild.level_range(src, "CHERRYGROVE_CITY"), None, "a town has no grass")


@test("all three hours of the grass are read, not just the one it is now")
def _(t):
    hours = wild.hours(str(t.source), "ROUTE_29")
    t.eq(len(hours), 3, "morning, day, night")
    # The pilot has only ever asked about the hour it is now, so what it knew
    # about the grass it was standing in was one third of what the ROM says.
    t.contains(hours[wild.MORN], "PIDGEY", "PIDGEY in the morning")
    t.contains(hours[wild.DAY], "PIDGEY", "and during the day")
    t.false("PIDGEY" in hours[wild.NITE], "but not after dark")
    t.contains(hours[wild.NITE], "HOOTHOOT", "HOOTHOOT replaces it")
    t.eq(hours[wild.NITE]["HOOTHOOT"], (2, 3), "with its own level range")
    t.eq(wild.hours(str(t.source), "CHERRYGROVE_CITY"), None, "a town has none")


@test("a species the clock has taken away is named, not silently dropped")
def _(t):
    gd = t.gamedata
    # The one change to the offered list that nobody made and nothing explains.
    said = advice.gone_message(gd, "ROUTE_29", wild.NITE, "PIDGEY")
    t.contains(said, "PIDGEY", "names the species being hunted")
    t.contains(said, "not now", "and says it is not here now")
    # Without a species in mind it names two and counts the rest, which is the
    # most a single row can hold.
    generic = advice.gone_message(gd, "ROUTE_29", wild.NITE, None)
    t.contains(generic, "also here", "the general form")
    # Nothing at all where the hours do not differ.
    t.eq(advice.gone_message(gd, "CHERRYGROVE_CITY", wild.DAY, None), None,
         "a town says nothing")


@test("where to go instead has to actually be better than here")
def _(t):
    gd, w = t.gamedata, t.world
    # A Lv2 on Route 29 (Lv2-3) must not be sent to Route 46, which is also
    # Lv2-3: that is a wasted journey dressed as advice. `better_hour` needed
    # the same correction.
    best = advice.better_grind(gd, w, "ROUTE_29", 2)
    t.true(best is not None, "there is somewhere better")
    t.gt(best["high"], 3, "with a higher ceiling than Route 29's")


@test("where to go instead has to be survivable, not just richer")
def _(t):
    gd, w = t.gamedata, t.world
    # The bug this constant exists for: "tops out at or above the lead" alone
    # sent a Lv5 two maps to Route 27, which the graph really does reach through
    # New Bark and which gives Lv28-32. A route that pays is not the same as a
    # route that can be survived.
    best = advice.better_grind(gd, w, "ROUTE_29", 5)
    t.eq(best["map"], "ROUTE_31", "Route 31, which gives Lv4-5")
    t.eq((best["low"], best["high"]), (4, 5), "at the lead's own level")
    t.lte(best["low"], 5 + advice.OVER_LEVEL, "within reach of a Lv5")
    # And the same route *is* right for a Lv30, which is the proof the rule is
    # about the gap and not about Route 27.
    late = advice.better_grind(gd, w, "ROUTE_29", 30)
    t.eq(late["map"], "ROUTE_27", "a Lv30 is sent to Route 27")


@test("silence when nothing reachable is better")
def _(t):
    gd, w = t.gamedata, t.world
    # The common case in the mid-game, and it has to read as silence rather
    # than as a recommendation to stay put.
    t.eq(advice.better_grind(gd, w, "ROUTE_29", 10), None,
         "nothing between Lv5 and Lv28 within three maps")
    t.eq(advice.better_grind(gd, w, "ROUTE_30", 8), None, "nor from Route 30")


@test("an hour that pays has to beat this hour too")
def _(t):
    gd = t.gamedata
    # This is the correction, and it was found by measuring rather than
    # reading: Crystal's three blocks carry the *same* levels on every Johto
    # route, so a version that only asked "does an hour pay the lead" advised
    # coming back in the morning while standing in an indistinguishable
    # afternoon.
    for hour in (wild.MORN, wild.DAY, wild.NITE):
        for level in (2, 3, 4, 5, 10):
            t.eq(advice.better_hour(gd, "ROUTE_29", level, hour), None,
                 f"Route 29 has no better hour for Lv{level} at hour {hour}")
    t.eq(advice.better_hour(gd, "CHERRYGROVE_CITY", 5, wild.DAY), None,
         "and a town has no hours at all")


@test("a slow grind says where to go instead")
def _(t):
    from pilot.session import Budget
    p = t.pilot("route30", timeout=90)
    p.session.set_budget(Budget(max_frames=120_000, max_wall_seconds=60))
    # A Lv14 Quilava on Route 30's Lv3-4 grass is the definition of slow.
    res = p.grind(slot=0, to_level=40, save_when_done=False, on_timeout="none")
    t.ne(res.status, "completed", "it will not reach Lv40 on this budget")
    said = " ".join(res.notes)
    t.contains(said, "slow here", "and says the grind is slow")
    t.contains(said, "away", "naming somewhere else and how far")


@test("status says what the pilot has always been able to work out")
def _(t):
    p = t.pilot("route30")
    out = p.status()
    # Every one of these is a fact the pilot could reach and never said.
    t.contains(out, "wallet", "the wallet")
    t.contains(out, "3000", "and what is in it")
    t.contains(out, "bag", "the bag")
    t.contains(out, "grass    : Lv3-4", "what the grass here gives")
    t.contains(out, "here     :", "and what is lying around")
    t.contains(out, "ANTIDOTE", "including the item ball")
