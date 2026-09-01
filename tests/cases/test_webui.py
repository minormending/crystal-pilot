"""The web UI's server side.

The HTTP layer is deliberately thin -- it reads a published snapshot and pushes
commands onto a queue -- so the parts worth testing are request validation and
the token check, both of which run without a socket.
"""
from pilot.webui import (MAX_PRESS_FRAMES, TAP_FRAMES, WALK_FRAMES,
                         WebPilot, make_handler)

from ..harness import test


def _web(t, fixture="grass_cyndaquil", **kw):
    p = t.pilot(fixture)
    return p, WebPilot(p, source=t.source, log=lambda *a, **k: None, **kw)


@test("state published to the phone describes where you actually are")
def _(t):
    p, web = _web(t)
    web._refresh_status()
    s = web.snapshot()
    t.eq(s["where"], "Route 29", "location")
    t.eq(len(s["party"]), p.reader.party_count(), "party size")
    t.eq(s["party"][0]["name"], p.reader.mon(0).species_name, "lead name")
    # The species list is what the phone offers for hunt/catch, so it has to be
    # this route's encounter table, not everything in the game.
    t.contains(s["species"], "SENTRET", "a Route 29 species")
    t.false("PIKACHU" in s["species"], "not a species from elsewhere")
    t.eq(s["trainers"], 0, "Route 29 has no trainers")
    t.false(s["busy"], "idle at rest")


@test("a sensible grind request is accepted and described")
def _(t):
    p, web = _web(t)
    level = p.reader.mon(0).level + 3
    title, call = web._plan("grind", {"slot": 0, "level": level})
    t.true(call is not None, f"should be accepted, got: {title}")
    t.contains(title, str(level), "title names the target level")


@test("nonsense requests are refused with a reason, not attempted")
def _(t):
    p, web = _web(t)
    level = p.reader.mon(0).level
    cases = [
        (("grind", {"slot": 9, "level": 20}), "party slot"),
        (("grind", {"slot": 0, "level": level}), "already"),
        (("grind", {"slot": 0, "level": 999}), "between 1 and 100"),
        (("hunt", {}), "choose something"),
        (("catch", {"species": "PIDGEY"}), "Poke Balls"),
        (("trainers", {}), "no trainers"),
        (("nonsense", {}), "unknown task"),
    ]
    for (kind, body), expected in cases:
        title, call = web._plan(kind, body)
        t.true(call is None, f"{kind} {body} should be refused")
        t.contains(title.lower(), expected.lower(), f"reason for {kind} {body}")


@test("catch becomes available once there are balls in the bag")
def _(t):
    p, web = _web(t)
    _title, call = web._plan("catch", {"species": "PIDGEY"})
    t.true(call is None, "refused with an empty bag")
    t.give_balls(p)
    title, call = web._plan("catch", {"species": "PIDGEY"})
    t.true(call is not None, f"should be accepted with balls, got: {title}")


@test("trainers is offered only where there are trainers")
def _(t):
    p, web = _web(t, fixture="route30")
    web._refresh_status()
    t.gt(web.snapshot()["trainers"], 0, "Route 30 has trainers")
    _title, call = web._plan("trainers", {})
    t.true(call is not None, "should be accepted on Route 30")


@test("the screen is served as a real PNG")
def _(t):
    p, web = _web(t)
    p.session.tick(1, True)
    web._refresh_frame(force=True)
    png = web.screen_png()
    t.gt(len(png), 500, "png has content")
    t.eq(png[:8], b"\x89PNG\r\n\x1a\n", "PNG magic bytes")


@test("requests without the right token are refused")
def _(t):
    p, web = _web(t, token="sekrit")
    handler = make_handler(web)

    class _Req:
        """Just enough of a handler to exercise the token check."""
        def __init__(self, path, header=None):
            self.path = path
            self.headers = {"X-Pilot-Token": header} if header else {}

    check = handler._authorised
    t.true(check(_Req("/api/state?t=sekrit")), "correct token in the query")
    t.true(check(_Req("/api/state", header="sekrit")), "correct token in a header")
    t.false(check(_Req("/api/state")), "no token")
    t.false(check(_Req("/api/state?t=")), "empty token")
    t.false(check(_Req("/api/state?t=wrong")), "wrong token")
    t.false(check(_Req("/api/state?t=sekri")), "prefix of the token")


@test("each run gets its own token unless one is given")
def _(t):
    p1, a = _web(t)
    p2, b = _web(t)
    t.gt(len(a.token), 8, "token is long enough to matter")
    t.ne(a.token, b.token, "tokens differ between runs")
    p3, c = _web(t, token="fixed")
    t.eq(c.token, "fixed", "an explicit token is honoured")


@test("a press from the phone turns and steps, not just turns")
def _(t):
    """Gen 2 turns you before it walks you. A press in a direction you are not
    already facing spends itself on the turn if it is short -- the six frames
    this used to send never moved you at all, so the first tap of every change
    of direction was wasted. A walk-length press does both."""
    # route30 rather than the grass fixture: a step taken in grass can be eaten
    # by a wild encounter, which would make this measure the wrong thing.
    p, web = _web(t, fixture="route30")

    def at():
        loc = p.reader.location()
        return (loc.x, loc.y)

    p.nav.step("up")                  # settle on a walkable tile, facing up
    before = at()
    web._handle({"kind": "input", "button": "left", "frames": 6})
    turned = at()
    t.eq(turned, before, "six frames in a new direction only turn you")

    web._handle({"kind": "input", "button": "left", "frames": WALK_FRAMES})
    t.note(f"{before} -> six frames {turned} -> {WALK_FRAMES} frames {at()}")
    t.ne(at(), before, "a walk-length press moves you")


@test("the phone cannot pin a button down, or press one that does not exist")
def _(t):
    """The press blocks the pilot's loop while it runs, so the length is clamped
    rather than trusted."""
    p, web = _web(t)
    held = []
    p.session.tap = lambda button, hold=0, gap=0: held.append((button, hold))

    web._handle({"kind": "input", "button": "left", "frames": 10_000})
    web._handle({"kind": "input", "button": "left", "frames": 0})
    web._handle({"kind": "input", "button": "left"})
    web._handle({"kind": "input", "button": "self-destruct", "frames": WALK_FRAMES})

    t.eq([h for _b, h in held], [MAX_PRESS_FRAMES, 1, TAP_FRAMES],
         "clamped high, clamped low, and a default when unasked")
    t.eq([b for b, _h in held], ["left", "left", "left"],
         "the button that is not a button was refused")
