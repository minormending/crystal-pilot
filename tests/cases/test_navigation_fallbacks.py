"""The navigator's fallbacks: the code that runs when planning has failed.

These were the least-covered lines in the project -- `_cross_edge_explore` had
32 of its 35 lines never executed, `_find_grass_sweep` 23 of 26 -- and that is
the wrong way round. They only run when the collision map is missing or a route
could not be planned, which is to say when something has already gone wrong. The
code that handles trouble was the code least likely to work.

They need no ROM. They feel their way by bumping into things, so what they need
is something to bump into: tests/fake.py's FakeWorld is a small walkable grid
wired into a session's work RAM.
"""
from pilot.nav import Navigator
from pilot.state import GameStateReader

from ..fake import FakeWorld, fake_symbols
from ..harness import test


def walking(t, **world):
    sym = fake_symbols()
    w = FakeWorld(sym, **world)
    r = GameStateReader(w.session, t.gamedata)
    return w, Navigator(w.session, r), r


# --- one step ---------------------------------------------------------------
@test("a step reports moved, blocked or a new map, and never lies about it")
def _(t):
    w, nav, r = walking(t, walls={(4, 5)}, pos=(5, 5))
    t.true(nav.step("left").blocked, "a wall blocks")
    t.eq((r.location().x, r.location().y), (5, 5), "and nothing moved")
    t.true(nav.step("right").moved, "open ground moves")
    t.eq((r.location().x, r.location().y), (6, 5), "by exactly one tile")


# --- the wall-following edge crossing --------------------------------------
@test("the explorer crossing finds a gap in a wall it cannot see")
def _(t):
    # A wall across the player's path with one hole in it. With no collision
    # data there is nothing to plan against, so the fallback has to feel along
    # the wall until it finds the way through.
    wall = {(x, 4) for x in range(12) if x != 9}
    w, nav, _r = walking(t, walls=wall, pos=(5, 6), edges={"up": (24, 4)},
                         width=12, height=12)
    res = nav._cross_edge_explore("north", max_steps=400)
    t.true(res.map_changed, f"it got through and onto the next map ({res})")
    t.eq(w.map_key, (24, 4), "which is the map the edge leads to")


@test("the explorer crossing gives up rather than walking forever")
def _(t):
    # Boxed in on all four sides. The wall-follower must notice, not spin until
    # the step budget runs out -- it counts consecutive failures and stops at
    # ten, which is the difference between giving up and appearing to hang.
    boxed = {(5, 4), (5, 6), (4, 5), (6, 5)}
    w, nav, _r = walking(t, walls=boxed, pos=(5, 5), edges={"up": (24, 4)})
    res = nav._cross_edge_explore("north", max_steps=1500)
    t.true(res.blocked, f"it reports blocked ({res})")
    t.eq(w.map_key, (24, 3), "and it is still on the map it started on")
    # Bounded well below max_steps, which is the whole point. Counted from the
    # directional presses, not session.presses -- that only sees `tap`, and
    # every step goes through button_press, so the first version of this
    # assertion was measuring a list that never grew.
    t.true(len(w.session.pyboy.pushes) < 120,
           f"it stopped early rather than exhausting the budget "
           f"({len(w.session.pyboy.pushes)} presses)")


@test("the explorer crossing hands a battle back rather than swallowing it")
def _(t):
    # Walking into grass mid-crossing starts a battle, and the caller decides
    # what to do about it. Without an on_battle hook it has to stop and say so.
    w, nav, _r = walking(t, pos=(5, 6), battle_at={(5, 5)},
                         edges={"up": (24, 4)})
    res = nav._cross_edge_explore("north", max_steps=200)
    t.true(res.battle, f"the battle is reported, not walked through ({res})")


@test("the explorer crossing answers battles itself when given a hook")
def _(t):
    w, nav, _r = walking(t, pos=(5, 8), battle_at={(5, 7)},
                         edges={"up": (24, 4)}, width=12, height=12)
    fought = []

    def on_battle():
        fought.append(True)
        w.in_battle = False           # the hook is what ends it
        w._sync()

    res = nav._cross_edge_explore("north", max_steps=400, on_battle=on_battle)
    t.eq(len(fought), 1, "the hook was called once")
    t.true(res.map_changed, f"and the crossing then completed ({res})")


# --- the grass sweep --------------------------------------------------------
@test("the grass sweep finds grass it was not told the position of")
def _(t):
    w, nav, r = walking(t, grass={(2, 5)}, pos=(5, 5))
    t.true(nav._find_grass_sweep(max_steps=200), "it found grass")
    t.true(r.on_grass(), "and is standing on it")


@test("the grass sweep steps aside when its primary direction is blocked")
def _(t):
    # A wall directly left, grass below-left. Sweeping left alone never gets
    # there; the sweep has to try its secondary direction and carry on.
    w, nav, r = walking(t, walls={(4, 5)}, grass={(3, 6)}, pos=(5, 5))
    t.true(nav._find_grass_sweep(max_steps=200), "it worked around the wall")
    t.true(r.on_grass(), "and reached the grass")


@test("the grass sweep gives up on a map with no grass at all")
def _(t):
    w, nav, r = walking(t, grass=set(), pos=(5, 5))
    t.false(nav._find_grass_sweep(max_steps=120), "it reports failure")
    t.false(r.on_grass(), "honestly, because there is none")


@test("the grass sweep stops when it walks off the map")
def _(t):
    # Leaving the map means the sweep is somewhere else entirely and its
    # reckoning is void, so it stops rather than carrying on in the new place.
    w, nav, _r = walking(t, grass=set(), pos=(1, 5), edges={"left": (24, 2)})
    t.false(nav._find_grass_sweep(max_steps=200), "it does not claim success")


# --- the greedy walk --------------------------------------------------------
@test("the greedy walk takes the other axis when the longer one is blocked")
def _(t):
    # A diagonal goal, with the longer axis walled. It should try the longer
    # axis, bump, take the shorter one, and keep going.
    w, nav, r = walking(t, walls={(4, 5)}, pos=(5, 5))
    nav._walk_to_greedy(2, 7, max_steps=80)
    t.eq((r.location().x, r.location().y), (2, 7), "it arrived around the wall")


@test("the greedy walk cannot route round a wall on a straight approach")
def _(t):
    """A documented limitation, pinned so it stays a decision.

    With the goal straight left there is no perpendicular option to fall back
    on -- the algorithm only ever tries the two axes that reduce the error, and
    one of those is zero. It bumps three times and gives up.

    That is what it promises: "enough for the short, mostly-open hops the pilot
    needs". Worth a test anyway, because the next person to read the docstring
    will assume "sidestepping obstacles" means more than it does, and because if
    this ever starts succeeding somebody has changed the algorithm.
    """
    w, nav, r = walking(t, walls={(4, 5)}, pos=(5, 5))
    res = nav._walk_to_greedy(3, 5, max_steps=60)
    t.true(res.blocked, f"it reports blocked ({res})")
    t.eq((r.location().x, r.location().y), (5, 5), "having gone nowhere")
    t.true(len(w.session.pyboy.pushes) < 12,
           f"and it stopped after a few tries, not sixty "
           f"({len(w.session.pyboy.pushes)} presses)")


@test("the greedy walk gives up on a goal it cannot reach")
def _(t):
    # Walled off completely. It must stop within its step budget and say so
    # rather than reporting an arrival it did not make.
    walled = {(x, 3) for x in range(12)} | {(x, 7) for x in range(12)} \
        | {(2, y) for y in range(12)}
    w, nav, r = walking(t, walls=walled, pos=(5, 5))
    nav._walk_to_greedy(0, 5, max_steps=40)
    t.ne((r.location().x, r.location().y), (0, 5), "it did not get there")
    t.true(r.location().x > 2, "and is still on its own side of the wall")


@test("the grass sweep reverses when it runs out of room the first way")
def _(t):
    """The flip is what makes it a sweep rather than a walk.

    Grass to the *right*, with the left side walled off. Sweeping left gets
    nowhere, the secondary direction gets nowhere either, and only reversing
    both finds the grass. Without the flip this fails, which the first version
    of these tests did not notice: every other case here happened to be
    reachable by walking left.
    """
    walls = {(4, y) for y in range(12)}
    w, nav, r = walking(t, walls=walls, grass={(8, 5)}, pos=(5, 5))
    t.true(nav._find_grass_sweep(max_steps=300), "it found the grass")
    t.true(r.on_grass(), "and is standing on it")
    t.true(r.location().x > 5, "having gone the other way to get there")
