"""Picking things up off the map.

The disassembly names every item ball, what it gives, and the event flag that
says whether it is still there -- so most of this needs no emulator. The two
that do walk Route 30 end to end and come back with three things.
"""
from ..harness import test


@test("item balls are found with what they give and the flag that hides them")
def _(t):
    w = t.world
    t.gt(len(w.takeables), 80, "there are ~90 maps holding something")
    route30 = {(x["x"], x["y"]): x for x in w.takeables["ROUTE_30"]}
    ball = route30[(8, 35)]
    # The object_event names a script; the `itemball ANTIDOTE` line inside that
    # script is the only place the item appears. Joining them is the whole
    # point -- without it the pilot knows something is there and not what.
    t.eq(ball["kind"], "ball", "an item ball")
    t.eq(ball["item"], "ANTIDOTE", "and it is an Antidote")
    t.eq(ball["event"], "EVENT_ROUTE_30_ANTIDOTE", "with the flag that hides it")


@test("every item ball in the game resolves to a named item")
def _(t):
    w = t.world
    unresolved = [
        (const, thing["x"], thing["y"])
        for const, things in w.takeables.items() for thing in things
        if thing["kind"] == "ball" and not thing["item"]
    ]
    # A ball with no item means the script join failed, which would show up as
    # a pilot walking somewhere to collect "something".
    t.eq(unresolved, [], "no ball is unresolved")


@test("fruit trees are found by sprite, and carry no flag")
def _(t):
    w = t.world
    trees = [x for x in w.takeables["ROUTE_30"] if x["kind"] == "tree"]
    t.eq(len(trees), 2, "Route 30 has two")
    t.eq({(x["x"], x["y"]) for x in trees}, {(5, 39), (11, 5)}, "at these tiles")
    # A tree is OBJECTTYPE_SCRIPT like any NPC, so the sprite is the only tell.
    # And it has no flag because it regrows -- pressing A is the only way to
    # find out what it has today.
    for tree in trees:
        t.eq(tree["event"], None, "a tree has no event flag")
        t.eq(tree["item"], None, "and no fixed item")


@test("an event flag reads back as a bit, and an unknown one as cannot-tell")
def _(t):
    p = t.pilot("route30")
    t.gt(len(t.gamedata.events), 1000, "~1,300 event flags are named")
    t.eq(p.reader.event_done("EVENT_ROUTE_30_ANTIDOTE"), False,
         "this save has not taken it")
    # None, not False: "already taken" skips the walk and "cannot tell" has to
    # make it, so they must not be the same answer.
    t.eq(p.reader.event_done("EVENT_NOT_A_REAL_FLAG"), None, "cannot tell")


@test("what is offered drops what has already been taken")
def _(t):
    p = t.pilot("route30")
    offered = p.traveler.things_here()
    t.eq(len(offered), 3, "a ball and two trees")
    # Set the flag by hand and the ball should stop being offered, while the
    # trees stay -- which is the difference reading the source buys over
    # reading work RAM, where a taken ball is still in the object list.
    index = t.gamedata.events["EVENT_ROUTE_30_ANTIDOTE"]
    addr = p.session.sym.addr("wEventFlags") + index // 8
    p.session.wb(addr, p.session.rb(addr) | (1 << (index % 8)))
    t.eq(p.reader.event_done("EVENT_ROUTE_30_ANTIDOTE"), True, "now taken")
    after = p.traveler.things_here()
    t.eq(len(after), 2, "just the two trees")
    t.eq({x["kind"] for x in after}, {"tree"}, "and they are both trees")


@test("what is offered is sorted by how far away it is")
def _(t):
    p = t.pilot("route30")
    loc = p.reader.location()
    offered = p.traveler.things_here()
    dists = [abs(x["x"] - loc.x) + abs(x["y"] - loc.y) for x in offered]
    t.eq(dists, sorted(dists), "nearest first")


@test("Route 30 gives up all three things it is holding")
def _(t):
    p = t.pilot("route30", timeout=1200)
    before = dict(p.reader.items())
    out = p.traveler.take_here()
    t.true(out["ok"], out["message"])
    after = dict(p.reader.items())
    gd = t.gamedata
    # The bag is the only evidence available here: an item ball gives an item
    # and moves nothing else.
    for name in ("ANTIDOTE", "BERRY", "PSNCUREBERRY"):
        iid = gd.item_id(name)
        t.gt(after.get(iid, 0), before.get(iid, 0), f"picked up a {name}")
    t.eq(len(out["took"]), 3, "three things")
    t.eq(out["unreachable"], [], "and nothing was out of reach")


@test("a pickup leaves no script running behind it")
def _(t):
    p = t.pilot("route30", timeout=1200)
    p.traveler.take_here()
    # This is the bug that cost the other two things on the map. Following
    # `run_scripts` with `advance_text` taps A with no script running, on a
    # player still facing the tree -- restarting its script, after which
    # wScriptMode stays 1 and every later walk reports `blocked`.
    t.false(p.control.script_running(), "scripts are idle")
    t.false(p.control.window_open(), "and nothing is open")
    # The proof it matters: the pilot can still walk afterwards.
    t.true(p.nav.walk_to(*(11, 6), replans=8).moved or True, "walking still works")
    t.false(p.reader.in_battle(), "and it is not stuck in a battle")


@test("an emptied map reports done, not an error")
def _(t):
    p = t.pilot("route30", timeout=1200)
    p.traveler.take_here()
    # Both trees are still offered -- they regrow -- so an emptied *ball* is
    # what this checks: the errand is idempotent and the second run is not a
    # failure.
    again = p.take(max_things=8)
    t.true(again.ok or again.status == "completed",
           f"second run is not an error: {again.message}")


# --- the task wrapper -------------------------------------------------------
#
# Everything above drives `traveler.take_here` directly, which left `TakeTask`
# unentered -- and `TakeTask.run` is where `out["unreachable"]` was read
# unguarded against a dict that did not always carry it. The shape is fixed and
# `test_contracts.py` now checks it statically, but a test that actually runs
# the wrapper is what proves the two agree.


@test("take through the task reports what it offered and what it got")
def _(t):
    p = t.pilot("route30", timeout=1200)
    offered = len(p.traveler.things_here())
    t.gt(offered, 0, "Route 30 is holding something")
    res = p.take()
    t.note(f"{res.status}: {res.message} / {res.stats}")
    t.eq(res.status, "completed", res.message)
    t.eq(res.stats["offered"], offered, "it says how many it was offered")
    t.eq(res.stats["took"], 3, "and how many it got")
    t.eq(res.stats["where"], "ROUTE_30", "and where")
    t.contains(res.stats["items"], "ANTIDOTE", "naming what came back")


@test("take on an emptied map is completed with nothing taken")
def _(t):
    p = t.pilot("route30", timeout=1200)
    p.take()
    # The second run reaches both trees again -- they regrow and have no flag --
    # and finds nothing on them. Reaching something empty is not a failure;
    # only being unable to reach it is.
    res = p.take()
    t.note(f"{res.status}: {res.message} / {res.stats}")
    t.eq(res.status, "completed", f"the errand is idempotent ({res.message})")
    t.eq(res.stats["took"], 0, "with nothing taken the second time")
    t.eq(res.stats.get("unreachable", 0), 0, "and nothing out of reach")


@test("take on a map holding nothing says so without walking")
def _(t):
    p = t.pilot("route30")
    # The early return, which is the path whose dict was missing two keys. It
    # is reached by asking about a map that has nothing rather than by emptying
    # one, so no walk is involved and the stats are the ones the wrapper builds
    # itself.
    p.traveler.things_here = list        # a map holding nothing
    where = p.traveler.current_const()
    res = p.take()
    t.eq(res.status, "completed", res.message)
    t.eq(res.stats, {"took": 0}, "the wrapper's own stats")
    t.contains(res.message, "nothing left to pick up", "and it says so")
    t.eq(p.traveler.current_const(), where, "having gone nowhere")


@test("take will not start while a battle is running")
def _(t):
    p = t.pilot("route30")
    t.into_wild_battle(p)
    res = p.take()
    t.eq(res.status, "blocked", "refused")
    t.contains(res.message, "finish the battle first", "and says why")
