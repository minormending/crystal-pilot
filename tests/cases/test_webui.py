"""The web UI's server side.

The HTTP layer is deliberately thin -- it reads a published snapshot and pushes
commands onto a queue -- so the parts worth testing are request validation and
the token check, both of which run without a socket.
"""
from pilot.webui import (MAX_PRESS_FRAMES, MENU_FIRST_ROW, MENU_ROW_STRIDE,
                         PLAYER_TILE_X, PLAYER_TILE_Y, SCREEN_TILES_X,
                         SCREEN_TILES_Y, TAP_FRAMES, TEXT_ROWS, WALK_FRAMES,
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


def _tap_on(tile_x, tile_y):
    """A tap in the middle of a screen tile, as the browser sends it."""
    return {"kind": "tap",
            "x": (tile_x + 0.5) / SCREEN_TILES_X,
            "y": (tile_y + 0.5) / SCREEN_TILES_Y}


@test("tapping the map walks there")
def _(t):
    p, web = _web(t, fixture="route30")
    start = p.reader.location()
    # One tile up from the player, who is always drawn at (4, 4).
    web._handle(_tap_on(PLAYER_TILE_X, PLAYER_TILE_Y - 1))
    now = p.reader.location()
    t.note(f"({start.x},{start.y}) -> ({now.x},{now.y})")
    t.eq((now.x, now.y), (start.x, start.y - 1), "walked one tile up")


@test("tapping several tiles away paths around what is in the way")
def _(t):
    p, web = _web(t, fixture="route30")
    start = p.reader.location()
    web._handle(_tap_on(PLAYER_TILE_X, PLAYER_TILE_Y - 3))
    now = p.reader.location()
    t.note(f"({start.x},{start.y}) -> ({now.x},{now.y})")
    t.eq((now.x, now.y), (start.x, start.y - 3), "walked three tiles up")


@test("a tap that cannot mean anything is refused, not guessed at")
def _(t):
    p, web = _web(t, fixture="route30")
    for bad in ({"x": 1.4, "y": 0.5}, {"x": -0.1, "y": 0.5}, {"x": "left", "y": 0.5}):
        title, call = web._plan("tap", bad)
        t.true(call is None, f"{bad} should be refused")
        t.contains(title, "off the screen", "says why")
    title, call = web._plan("tap", _tap_on(PLAYER_TILE_X, PLAYER_TILE_Y))
    t.true(call is None, "tapping yourself is not a walk")
    t.contains(title, "already standing", "says why")


@test("tapping an open menu moves the cursor and confirms nothing")
def _(t):
    """The dangerous version of this feature presses A. It never does: a wrong
    row would use an item or toss something. It also has to tell a real menu
    from the overworld -- wMenuCursorY keeps its last value out on the map, so
    the window stack is what decides."""
    p, web = _web(t, fixture="route30")
    p.control.open_start_menu()
    p.session.tick(30)
    t.true(web._window_open(), "the START menu is up")
    before_pos = p.reader.location()
    before_row = p.session.rb("wMenuCursorY")

    # Two entries further down the menu, in text rows.
    top = p.session.rb("wMenuBorderTopCoord")
    want_row = before_row + 2
    text_row = top + 2 + (want_row - 1) * 2
    web._handle({"kind": "tap", "x": 0.5,
                 "y": (text_row + 0.5) / 18})

    after_row = p.session.rb("wMenuCursorY")
    after_pos = p.reader.location()
    t.note(f"cursor {before_row} -> {after_row}")
    t.eq(after_row, want_row, "cursor moved to the tapped entry")
    t.true(web._window_open(), "the menu is still open -- nothing was confirmed")
    t.eq((after_pos.x, after_pos.y), (before_pos.x, before_pos.y),
         "and the player did not walk")


@test("a menu tap picks the entry it was aimed at, odd rows included")
def _(t):
    """The tap used to be rounded to the 16px tile the overworld is drawn in,
    which halves the resolution menus are laid out at. Two in five of the game's
    menus start on an odd text row, and on those every entry but the first
    resolved to the one above it."""
    p, web = _web(t, fixture="route30")
    p.control.open_start_menu()
    p.session.tick(30)
    t.true(web._window_open(), "the START menu is up")
    entries = web._menu_entries(web._menu_box())

    real_top = p.session.rb("wMenuBorderTopCoord")
    real_bottom = p.session.rb("wMenuBorderBottomCoord")
    for top in (real_top, real_top + 1):   # as drawn, and shifted onto an odd row
        # The whole box moves, not just its top edge: the entry count is bounded
        # by what fits between the borders, so shifting one edge alone invents a
        # menu the game would never draw.
        p.session.wb("wMenuBorderTopCoord", top)
        p.session.wb("wMenuBorderBottomCoord", real_bottom + (top - real_top))
        picked = []
        for row in range(1, entries + 1):
            text_row = top + MENU_FIRST_ROW + (row - 1) * MENU_ROW_STRIDE
            if text_row >= TEXT_ROWS:
                break
            title, call = web._plan("tap", {"x": 0.5,
                                            "y": (text_row + 0.5) / TEXT_ROWS})
            t.true(call is not None, f"row {row} should plan something: {title}")
            picked.append(int(title.rsplit(" ", 1)[1]))
        t.note(f"top={top}: aimed at {list(range(1, len(picked) + 1))}, got {picked}")
        t.eq(picked, list(range(1, len(picked) + 1)),
             f"every entry of a menu starting on row {top}")


@test("a command that cannot be parsed does not take the pilot down")
def _(t):
    """The loop's only guard was for KeyboardInterrupt, so anything else raised
    out of it into the finally that stops the emulator and writes the save. The
    HTTP layer had already replied ok, so a typo in a request ended a running
    session and said nothing."""
    p, web = _web(t)
    for bad in ({"kind": "input", "button": "left", "frames": "fast"},
                {"kind": "grind", "slot": "second", "level": 9},
                {"kind": "grind", "slot": 0, "level": "twenty"},
                {"kind": "tap", "x": None, "y": None}):
        try:
            web._handle(bad)
            raised = None
        except Exception as e:          # noqa: BLE001
            raised = f"{type(e).__name__}: {e}"
        t.true(raised is None, f"{bad} raised out of _handle ({raised})")
    # and the emulator is still there to be used
    t.gte(p.reader.party_count(), 1, "the pilot survived")


def _into_battle(p):
    """Walk the grass until a wild battle starts and its menu is drawn."""
    for _ in range(60):
        if p.reader.in_battle():
            break
        p.nav.step("left")
        p.nav.step("right")
    for _ in range(40):
        p.session.tap("a", hold=4, gap=8)
        if (p.session.rb("wMenuCursorY") in (1, 2)
                and p.session.rb("wMenuCursorX") in (1, 2)):
            break
    p.session.tick(40)
    return p.reader.in_battle()


@test("a tap during a battle is refused, not aimed at the battle menu")
def _(t):
    """The battle puts a window on the stack, so asking "is a menu up?" before
    "are we in a battle?" sent taps into the cursor code -- which drives one
    column and cannot work a 2x2 menu. It would quietly move the selection onto
    RUN or PKMN under the player."""
    p, web = _web(t, fixture="grass_cyndaquil")
    if not _into_battle(p):
        t.note("no wild battle appeared; nothing to check")
        return
    t.true(web._window_open(), "a battle really does open a window")
    before = (p.session.rb("wMenuCursorX"), p.session.rb("wMenuCursorY"))
    title, call = web._plan("tap", {"x": 0.5, "y": 0.85})
    t.true(call is None, f"should be refused, planned {title!r}")
    t.contains(title, "battle", "says why")
    t.eq((p.session.rb("wMenuCursorX"), p.session.rb("wMenuCursorY")), before,
         "and the battle menu selection is untouched")


@test("the menu entry count is not read from a byte that means something else")
def _(t):
    """wMenuDataItems shares an address with the 2D menu's dimensions byte, so
    it reads 34 for the battle menu. What fits between the borders is the
    honest ceiling."""
    p, web = _web(t, fixture="grass_cyndaquil")
    if not _into_battle(p):
        t.note("no wild battle appeared; nothing to check")
        return
    box = web._menu_box()
    raw = p.session.rb("wMenuDataItems")
    t.note(f"box={box} raw wMenuDataItems={raw} -> {web._menu_entries(box)}")
    t.gt(raw, 8, "the raw byte really is nonsense here")
    t.eq(web._menu_entries(box), 2, "two rows fit between those borders")


@test("a tap outside an open menu is not a choice of entry")
def _(t):
    """The START menu is the right half of the screen. A tap in the far corner
    used to move the cursor anyway, clamped to whichever end it rounded to."""
    p, web = _web(t, fixture="route30")
    p.control.open_start_menu()
    p.session.tick(30)
    top, left, bottom, right = web._menu_box()
    t.gt(left, 0, "the menu does not reach the left edge")
    before = p.session.rb("wMenuCursorY")

    title, call = web._plan("tap", {"x": 0.02, "y": 0.97})
    t.true(call is None, f"a corner tap should be refused, planned {title!r}")
    t.eq(p.session.rb("wMenuCursorY"), before, "cursor untouched")

    # inside the box it still works
    inside = {"x": (left + 1.5) / 20, "y": (top + MENU_FIRST_ROW + 0.5) / TEXT_ROWS}
    title, call = web._plan("tap", inside)
    t.true(call is not None, f"a tap on entry 1 should plan: {title!r}")


@test("a tap past the edge of the map says so")
def _(t):
    p, web = _web(t, fixture="route30")
    p.calibrate()
    width, height = p.collision.map_size()
    loc = p.reader.location()
    # the fixture stands on the bottom row, so tapping below it leaves the map
    if loc.y + (SCREEN_TILES_Y - 1) - PLAYER_TILE_Y < height:
        t.note("this fixture is not near an edge; nothing to check")
        return
    title, call = web._plan("tap", _tap_on(PLAYER_TILE_X, SCREEN_TILES_Y - 1))
    t.note(f"map is {width}x{height}, player at ({loc.x},{loc.y}): {title!r}")
    t.true(call is None, "should be refused")
    t.contains(title, "off the edge", "says why, rather than blaming the route")


@test("the idle loop runs the game at normal speed, not as fast as it can")
def _(t):
    """The pilot is unthrottled so tasks finish in seconds, but the idle loop is
    what someone is watching: left at full speed it ran the world at 127x."""
    import threading, time
    # run() writes the .sav when it stops, so this runs against a private copy
    # of the ROM. Pointed at the shared one it left a save behind that every
    # later test then booted from.
    p = t.pilot_on(t.rom_copy("idle"), "route30")
    from pilot.webui import WebPilot
    web = WebPilot(p, source=t.source, log=lambda *a, **k: None)
    web.port, web.host, web.token = 8199, "127.0.0.1", "test"
    thread = threading.Thread(target=web.run, daemon=True)
    thread.start()
    try:
        time.sleep(0.8)
        first = p.session.pyboy.frame_count
        time.sleep(1.2)
        fps = (p.session.pyboy.frame_count - first) / 1.2
    finally:
        web._running = False
        time.sleep(0.3)
    t.note(f"{fps:,.0f} fps while idle")
    t.true(fps < 400, f"idle should be about real time, saw {fps:,.0f} fps")


@test("tapping a doorway says which room it came out in")
def _(t):
    """Doors, stairs, caves and warp panels fire the moment you step on them,
    but the transition runs for a few frames after that. The walk therefore
    finishes on the old map and the warp lands while the result is being
    written -- so it used to report "walked to (7,0)", naming a tile on a map
    the player had already left."""
    from pilot.collision import CollisionMap
    from pilot.tasks.bootstrap import Bootstrap
    p = t.pilot_on(t.rom_copy("doorway"))
    Bootstrap(p.session, p.reader, p.control, p.nav, log=lambda *a: None).run_intro()
    web = WebPilot(p, source=t.source, log=lambda *a, **k: None)
    p.calibrate()
    before = p.reader.location()

    width, height = p.collision.map_size()
    warps = [(x, y) for y in range(height) for x in range(width)
             if CollisionMap.is_warp(p.collision.collision_at(x, y))]
    t.gte(len(warps), 1, "the starting room has a way out")
    gx, gy = warps[0]
    tap = _tap_on(gx - before.x + PLAYER_TILE_X, gy - before.y + PLAYER_TILE_Y)

    title, call = web._plan("tap", tap)
    t.true(call is not None, f"the doorway should be walkable to: {title!r}")
    res = call()
    after = p.reader.location()
    t.note(f"{res.message}  ({before.group}.{before.number} -> "
           f"{after.group}.{after.number})")
    t.eq(res.status, "completed", res.message)
    t.ne(after.key, before.key, "we really did change map")
    t.contains(res.message, "through to", "reported as going through, not arriving")
    t.contains(res.stats["to"], str(after.x), "the coordinates are the new map's")
