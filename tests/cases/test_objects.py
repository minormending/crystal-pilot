"""Who is standing where, and the two arrays that answer it differently.

The collision map is terrain. It has nothing to say about the Youngster in a
one-tile corridor, so a planner reading only terrain routes straight through
people. These read the game's own object arrays, and every offset in them is
checked against the disassembly's constants rather than remembered.
"""
from pilot.collision import OBJECTTYPE_TRAINER

from ..harness import test


@test("the placements match what the disassembly says the map holds")
def _(t):
    p = t.pilot("route30")
    placed = {(o["x"], o["y"]): o for o in p.collision.placed_objects()}
    # The source is the other half of this fact, parsed by world.py. If the
    # WRAM decode and the .asm disagree, one of them is wrong -- and this is
    # how the +4 origin gets checked without believing anything.
    for trainer in t.world.trainers["ROUTE_30"]:
        here = placed.get((trainer["x"], trainer["y"]))
        t.true(here is not None,
               f"a trainer at ({trainer['x']},{trainer['y']}) is placed")
        t.eq(here["type"], OBJECTTYPE_TRAINER, "and its type byte says trainer")
    for thing in t.world.takeables["ROUTE_30"]:
        t.true((thing["x"], thing["y"]) in placed,
               f"{thing['kind']} at ({thing['x']},{thing['y']}) is placed")


@test("index 0 of each array is the player, which is how the origin is known")
def _(t):
    p = t.pilot("route30")
    loc = p.reader.location()
    # Both arrays store coordinates offset by +4. Measured rather than assumed:
    # on this save the player's own placement reads raw (11,57) and the player
    # is standing at (7,53).
    player = next(o for o in p.collision.placed_objects() if o["index"] == 0)
    t.eq((player["x"], player["y"]), (loc.x, loc.y),
         "the placement decodes to where the player is")
    # And the player is skipped by the live read, because the tile the player
    # stands on is not an obstacle to the player.
    live = p.collision.live_objects()
    t.true(live is not None, "the structs are readable")
    t.false((loc.x, loc.y) in {(o["x"], o["y"]) for o in live},
            "the player is not in the live list")


@test("the live read stops at NUM_OBJECT_STRUCTS, not at sixteen")
def _(t):
    p = t.pilot("route30")
    s = p.session
    base = s.sym.addr("wObjectStructs")
    stride = s.sym.addr("wObject1Struct") - base
    objects = s.sym.addr("wMapObjects")
    # There are thirteen structs, not sixteen. The version of this that lived
    # in trainers.py looped to 16, and the last iteration read its coordinates
    # from *inside wMapObjects* -- so a tile from an unspawned placement could
    # be reported as an object standing there, which is the one thing the
    # caller must never be told.
    t.eq(p.collision.STRUCT_COUNT, 13, "thirteen, per NUM_OBJECT_STRUCTS")
    end = base + p.collision.STRUCT_COUNT * stride
    t.lte(end, objects, "the array read stays clear of the placements")
    over = base + 16 * stride
    t.gt(over, objects, "and reading sixteen would not have")


@test("what is occupied is read live, and re-read because people move")
def _(t):
    p = t.pilot("route30")
    cm = p.collision
    # Nothing is spawned near the player on this save -- eleven objects placed,
    # none of them live -- so nothing is avoided. That is the right answer: the
    # placements would have marked five tiles nobody is standing on.
    t.eq(cm.occupied(), set(), "nothing spawned nearby, nothing avoided")
    t.gt(len(cm.placed_objects()), 5, "though plenty is placed")


@test("an unreadable struct array falls back, an empty one does not")
def _(t):
    p = t.pilot("route30")
    cm = p.collision
    # `None` and `[]` mean different things and the fallback turns on it:
    # "cannot read the array" has to use the stale placements, while "the array
    # is readable and nothing is in it" must not, or the planner avoids five
    # tiles that are open.
    real = cm.live_objects
    cm.live_objects = lambda: None
    try:
        fallback = cm.occupied()
    finally:
        cm.live_objects = real
    t.gt(len(fallback), 0, "cannot-tell falls back to the placements")
    t.eq(cm.occupied(), set(), "readable-and-empty avoids nothing")


@test("a trainer must be spawned to be offered, not merely placed")
def _(t):
    p = t.pilot("route30")
    # Route 30 places three trainers and, from the south end of the route, has
    # spawned none of them -- one hidden behind an event flag and two too far
    # north to be loaded. Offering a walk to a trainer who is not there is
    # offering a walk to nobody, so this returns nothing rather than falling
    # back to the placements the way `occupied` does.
    t.eq(len(t.world.trainers["ROUTE_30"]), 3, "three are placed")
    t.eq(p.collision.trainers_here(), [], "and none are spawned here")


@test("objects outside the map are dropped rather than avoided")
def _(t):
    p = t.pilot("route30")
    w, h = p.collision.map_size()
    # The bounds are the only check available -- index 0 holds a placement, so
    # it cannot verify the origin on a build that stored objects differently.
    # And that is the right way to fail: an empty list means the planner walks
    # into people and recovers, where in-bounds but *wrong* tiles can seal a
    # one-tile corridor.
    for o in p.collision.placed_objects():
        t.true(0 <= o["x"] < w, f"x {o['x']} is on the map")
        t.true(0 <= o["y"] < h, f"y {o['y']} is on the map")
    live = p.collision.live_objects() or []
    for o in live:
        t.true(0 <= o["x"] < w and 0 <= o["y"] < h, "live objects too")


@test("a planner given a sealed corridor gives the people up before the walls")
def _(t):
    p = t.pilot("route30")
    cm = p.collision
    loc = p.reader.location()
    # Pretend somebody is standing on every tile around the player. The plan
    # must still be made: an avoid set that seals a corridor is worse than
    # walking up to somebody and taking a refused step.
    ring = {(loc.x + dx, loc.y + dy)
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))}
    # Eighteen steps north, and reachable: (7,47) two tiles closer is a wall,
    # which would make this test fail for a reason it is not about.
    goal = (loc.x, loc.y - 8)
    t.true(p.collision.path_to(goal) is not None, "the goal is reachable at all")
    real = cm.occupied
    cm.occupied = lambda: ring
    try:
        res = p.nav.walk_to(*goal)
    finally:
        cm.occupied = real
    t.false(res.blocked, "planned around the ring instead of refusing to plan")
