"""Crossing the world graph when the game says no.

A route is shortest by *legs* and knows nothing about a leg being hard. Route
29's connection struct says there is a map to the north, and there is — Route
46 — but the pilot cannot get up there, and a gate two tiles south of Violet
City turns the walk back in words until Falkner's badge is won. Both look
identical to a pathfinder, and both look identical to a bug from outside.

So `travel_to` writes such a leg off and asks again without it. These tests are
about the bookkeeping around that, which is where it went wrong:

- **a refused leg is not a leg walked**, so refusals must not spend the
  arrival budget
- a refusal with **words on the screen** is somebody talking, and the words are
  the answer
- a write-off **survives the call** — asking twice where to heal should not
  walk into the same closed gate twice
- and is **dropped when a badge is won**, because a badge is what opens one

`walk_hop` is stubbed in most of these: it is the part that drives the
emulator, and what is under test is the arithmetic above it. The one test that
does not stub anything walks a real route, to keep the stubs honest about what
they stand for.
"""
from ..harness import test


def _traveler(t, fixture="route30"):
    p = t.pilot(fixture, timeout=600)
    return p, p.traveler


@test("a refused leg does not spend the walk's arrival budget")
def _(t):
    p, trav = _traveler(t)
    here = trav.current_const()
    asked: list[tuple[str, str]] = []

    def refuse_everything(kind, target, warp, attempts=8):
        asked.append((kind, target))
        return False        # refused, and the player has not moved

    trav.walk_hop = refuse_everything
    # One arrival allowed, which the old accounting would have spent on the
    # first refusal and then given up. Every refusal here is charged to its own
    # budget instead, so the search keeps going until the graph runs out of
    # ways round -- which is what makes a place the pilot cannot reach report
    # the refusal rather than "too many legs" from somewhere it never needed.
    got = trav.travel_to("VIOLET_CITY", max_hops=1, max_refusals=6)
    t.false(got, "it did not get there")
    t.gt(len(asked), 1, f"and it tried more than one leg ({asked})")
    t.eq(trav.current_const(), here, "having gone nowhere")


@test("what the game said when it turned the walk back is kept and reported")
def _(t):
    p, trav = _traveler(t)
    # The gate's own words, as a person would read them off the screen.
    t.paint_screen(p, ["", "", "Wait up! What's the",
                       "hurry?"])

    def refuse(kind, target, warp, attempts=8):
        return False

    trav.walk_hop = refuse
    trav.travel_to("VIOLET_CITY", max_hops=2, max_refusals=2)
    t.note(f"turned_back = {trav.turned_back!r}")
    # "could not leave ROUTE_30 going up" blames the pilot's walking for a rule
    # of the game, while the reason was on the screen the whole time.
    t.contains(trav.turned_back, "Wait up", "the words are kept")
    t.contains(trav.turned_back, trav.current_const(), "with the leg they came from")


@test("a silent refusal is reported as a refusal, not as words nobody said")
def _(t):
    p, trav = _traveler(t)
    # A blank screen is the other kind: a tile somebody is standing on. Making
    # one up would be worse than saying nothing.
    t.paint_screen(p, [""] * 4)

    def refuse(kind, target, warp, attempts=8):
        return False

    trav.walk_hop = refuse
    trav.travel_to("VIOLET_CITY", max_hops=2, max_refusals=2)
    t.eq(trav.turned_back, "", f"nothing invented ({trav.turned_back!r})")
    t.gt(len(trav.written_off), 0, "but the leg is still written off")


@test("a written-off leg survives the call that discovered it")
def _(t):
    p, trav = _traveler(t)

    def refuse(kind, target, warp, attempts=8):
        return False

    trav.walk_hop = refuse
    trav.travel_to("VIOLET_CITY", max_hops=2, max_refusals=3)
    learned = set(trav.written_off)
    t.gt(len(learned), 0, "something was written off")
    # The question this is for -- "where is the nearest place I can heal?" --
    # gets asked again and again. Rediscovering the same wall each time is how
    # a heal spends two minutes finding out what it already knew.
    trav.travel_to("VIOLET_CITY", max_hops=2, max_refusals=3)
    t.true(learned <= trav.written_off, "and is still known on the next walk")


@test("winning a badge re-opens every written-off leg")
def _(t):
    p, trav = _traveler(t)

    def refuse(kind, target, warp, attempts=8):
        return False

    trav.walk_hop = refuse
    trav.travel_to("VIOLET_CITY", max_hops=2, max_refusals=3)
    t.gt(len(trav.written_off), 0, "legs written off with no badges")
    t.eq(p.reader.badge_count(), 0, "this fixture has none")

    # A badge is precisely the thing that opens one of these gates, and nothing
    # says which badge opened which. So all of them are re-asked rather than
    # guessed at.
    p.session.wb("wJohtoBadges", 0b00000001)
    t.eq(p.reader.badge_count(), 1, "Falkner's badge")
    trav._forget_write_offs_on_a_badge()
    t.eq(trav.written_off, set(), "every write-off re-opened")


@test("a route the graph cannot offer is refused rather than walked forever")
def _(t):
    p, trav = _traveler(t)
    tries = []

    def refuse(kind, target, warp, attempts=8):
        tries.append(target)
        return False

    trav.walk_hop = refuse
    # Every leg refused, so the write-off set grows until the graph has no way
    # left. That is what makes this terminate without a bound doing the work --
    # the bound is the belt, not the braces.
    got = trav.travel_to("VIOLET_CITY", max_hops=6, max_refusals=40)
    t.false(got, "it gave up")
    t.lt(len(tries), 40, f"before exhausting the refusal budget ({len(tries)})")
    t.note(f"{len(tries)} legs tried, {len(trav.written_off)} written off")


@test("an ordinary walk across two real maps still arrives")
def _(t):
    # Nothing stubbed. This is what the stubs above stand in for, and it is the
    # test that would notice if the rework above had broken travel itself.
    p, trav = _traveler(t)
    t.eq(trav.current_const(), "ROUTE_30", "starting on Route 30")
    t.true(trav.travel_to("CHERRYGROVE_CITY"), "walked to Cherrygrove")
    t.eq(trav.current_const(), "CHERRYGROVE_CITY", "and is there")
    t.eq(trav.turned_back, "", "with nothing having turned it back")
    t.eq(trav.written_off, set(), "and nothing written off")
