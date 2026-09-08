"""Fighting the trainer nearest you.

`trainers` sweeps a whole route from the placements the disassembly lists. This
is the same errand aimed at one person, and it reads the live object structs
instead -- three differences, each a fact about people rather than about item
balls:

* a trainer is only there if the game has **spawned** it;
* a trainer **moves**, so the tile is re-read every attempt;
* a trainer who **refuses** is not asked again.

The Route 30 fixture turns out to be the perfect place to test the first of
those and a bad place to test a fight, and the disassembly says why. Three
objects stand in the one-tile corridor north at (5,24), (5,25) and (5,26) --
two `SPRITE_MONSTER` props and a Youngster, all gated on
`EVENT_ROUTE_30_BATTLE`. They are Joey's scripted battle, and the game is
deliberately walling the player in until it has been watched. So Mikey at
(5,23) really is unreachable from the south, and `unreachable` is the truth
rather than a bug.
"""
from pilot.tasks.base import FACE_FROM
from pilot.tasks.moment import DuelTask

from ..harness import test


def _duel(p):
    return DuelTask(p.session, p.reader, p.control, p.nav, p.world, p.gamedata,
                    p.traveler, p.saver, p.backups, log=lambda *a, **k: None)


@test("which way to face is derived from the movement table, not written again")
def _(t):
    """The table was written out twice and inverted both times.

    `FACE_FROM` is keyed by the offset from where you stand to where they are.
    Both copies said `(0, 1): "up"` -- and `nav.DELTA` says "up" is `(0, -1)`,
    so the trainer sweep turned its back on every trainer and then pressed A.

    It got away with it because a trainer *spots* you: Route 30's three have
    sight ranges of 3, 1 and 3, and the tile you stand on to talk is inside
    that, so the game starts the battle itself before the press matters. One
    table now, derived from the other, so an inversion is not expressible.
    """
    from pilot.nav import DELTA

    t.eq(FACE_FROM, {d: b for b, d in DELTA.items()}, "it is DELTA inverted")
    # Spelled out, because the whole bug was that this reads fine either way.
    t.eq(FACE_FROM[(0, -1)], "up", "they are north of you: press up")
    t.eq(FACE_FROM[(0, 1)], "down", "they are south of you: press down")
    t.eq(FACE_FROM[(1, 0)], "right", "they are east of you: press right")
    t.eq(FACE_FROM[(-1, 0)], "left", "they are west of you: press left")


@test("a route that places trainers but has spawned none says so without walking")
def _(t):
    p = t.pilot("route30", timeout=300)
    placed = len(p.world.trainers.get("ROUTE_30", []))
    t.eq(placed, 3, "Route 30 places three trainers")
    # From the south end none of them has a struct, which is the whole reason
    # this reads the structs: walking at a placement is walking at nobody.
    t.eq(p.collision.trainers_here(), [], "and none is spawned from down here")
    where = p.reader.location()
    res = p.duel(max_attempts=4)
    t.note(f"{res.status}: {res.message}")
    t.eq(res.status, "blocked", "refused")
    t.contains(res.message, "no trainer is standing near you", "and says why")
    t.eq(res.stats["asked"], 0, "having asked nobody")
    t.eq((p.reader.location().x, p.reader.location().y), (where.x, where.y),
         "and gone nowhere to find out")


@test("a spawned trainer is found, and an unreachable one is reported as that")
def _(t):
    p = t.pilot("route30", timeout=900)
    # Walk up to the mouth of the corridor, which spawns Mikey at (5,23).
    p.nav.walk_to(5, 27, replans=30, on_battle=lambda: None)
    live = p.collision.trainers_here()
    t.note(f"spawned: {live}")
    t.gte(len(live), 1, "Mikey has a struct from here")
    t.eq(live[0]["x"], 5, "at x=5")
    t.eq(live[0]["y"], 23, "and y=23")
    t.true("index" in live[0], "carrying the placement index, since a trainer moves")
    res = p.duel(max_attempts=3)
    t.note(f"{res.status}: {res.message} / {res.stats}")
    # Joey's scripted battle is still standing in the only corridor, so this is
    # the honest answer rather than a driving failure -- and it is *reported*
    # as unreachable rather than as nobody being there.
    t.eq(res.status, "blocked", "could not get to anybody")
    t.eq(res.stats["asked"], 1, "having asked exactly one person")
    t.contains(" ".join(res.notes), "unreachable", "and named the reason")


@test("the nearest spawned trainer is the one asked first")
def _(t):
    p = t.pilot("route30")
    task = _duel(p)
    here = p.reader.location()
    far = {"index": 9, "x": here.x, "y": here.y - 10, "sprite": 1}
    near = {"index": 4, "x": here.x, "y": here.y - 2, "sprite": 1}
    p.collision.trainers_here = lambda: [far, near]
    order = task._near(refused=set())
    t.eq([w["index"] for w in order], [4, 9], "nearest first")
    # And the refusal set is honoured, which is the third measured fact.
    t.eq([w["index"] for w in task._near(refused={4})], [9], "minus the refused")
    t.eq(task._near(refused={4, 9}), [], "and empty once both are refused")


@test("a trainer who refuses is not asked again")
def _(t):
    p = t.pilot("route30")
    here = p.reader.location()
    two = [{"index": 4, "x": here.x, "y": here.y - 2, "sprite": 1},
           {"index": 9, "x": here.x, "y": here.y - 6, "sprite": 1}]
    p.collision.trainers_here = lambda: list(two)
    asked: list[int] = []

    def refuse(who):
        asked.append(who["index"])
        return "no_battle"

    task = _duel(p)
    task._engage = refuse
    res = task.run(max_attempts=6)
    t.note(f"asked {asked}: {res.message}")
    # Two in range with the near one already beaten means every attempt goes to
    # the nearer, and the whole budget is spent on somebody who will never
    # answer. Each is asked exactly once.
    t.eq(sorted(asked), [4, 9], "both asked")
    t.eq(len(asked), 2, "and neither asked twice")
    t.eq(res.status, "blocked", "reported as refused")
    t.contains(res.message, "would fight", f"not as nobody being there ({res.message})")


@test("a duel will not start while a battle is already running")
def _(t):
    p = t.pilot("route30")
    t.into_wild_battle(p)
    res = p.duel()
    t.eq(res.status, "blocked", "refused")
    t.contains(res.message, "already in a battle", "and points at `battle`")


@test("a build that cannot read the object structs says so rather than walking")
def _(t):
    p = t.pilot("route30")
    where = p.reader.location()
    # None, not []: the same distinction `live_objects` is careful about, and
    # here getting it wrong costs a walk to nobody.
    p.collision.live_objects = lambda: None
    res = p.duel()
    t.eq(res.status, "blocked", "refused")
    t.contains(res.message, "cannot read the object structs", "naming the gap")
    t.eq((p.reader.location().x, p.reader.location().y), (where.x, where.y),
         "and went nowhere")
