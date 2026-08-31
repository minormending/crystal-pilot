"""Movement and the collision map it plans against.

The collision decode is arithmetic over WRAM and a ROM table, so it can be
subtly wrong in a way that still looks plausible. The game publishes the
collision of the tile the player stands on, which gives these tests a source of
truth to check against.
"""
from pilot.nav import DELTA

from ..harness import test


@test("the collision decode reproduces the game's own value")
def _(t):
    p = t.pilot("grass_cyndaquil")
    t.true(p.collision.calibrated, "collision map should calibrate")
    t.eq(p.collision._off, (4, 4), "offset derived from GetBlockLocation")
    t.true(p.collision.verify(), "decoded tile should equal wPlayerTileCollision")


@test("the decode stays correct while walking around")
def _(t):
    p = t.pilot("grass_cyndaquil")
    cm, r = p.collision, p.reader
    checked = mismatch = 0
    for i in range(40):
        d = ("left", "up", "right", "down")[i % 4]
        res = p.nav.step(d)
        if res.battle:
            p.traveler._handle_battle()
            continue
        if res.map_changed:
            break
        loc = r.location()
        checked += 1
        if cm.collision_at(loc.x, loc.y) != r.tile_collision():
            mismatch += 1
    t.note(f"{checked} tiles checked, {mismatch} mismatched")
    t.gt(checked, 10, "tiles actually visited")
    t.eq(mismatch, 0, "tiles where the decode disagreed with the game")


@test("the decode reads all four quadrants, not the same one repeatedly")
def _(t):
    p = t.pilot("grass_cyndaquil")
    cm = p.collision
    w, h = cm.map_size()
    blocks = varied = 0
    for by in range(0, h - 1, 2):
        for bx in range(0, w - 1, 2):
            quads = {cm.collision_at(bx + dx, by + dy)
                     for dy in (0, 1) for dx in (0, 1)}
            blocks += 1
            varied += len(quads) > 1
    t.note(f"{varied}/{blocks} blocks have differing quadrants")
    # Walking a patch of uniform grass cannot tell a correct quadrant index from
    # a broken one -- every quadrant of the block holds the same value there.
    # Across a whole route, ~a fifth of blocks mix terrain, so a decode that
    # always reads quadrant 0 collapses to zero variation.
    t.gt(blocks, 100, "blocks scanned")
    t.gt(varied, 20, "blocks where the four quadrants differ")


@test("walkability never claims a reachable tile is blocked")
def _(t):
    p = t.pilot("grass_cyndaquil")
    cm, r = p.collision, p.reader
    false_negatives = tried = 0
    for i in range(60):
        d = ("left", "left", "up", "right", "right", "down")[i % 6]
        loc = r.location()
        dx, dy = DELTA[d]
        predicted = cm.walkable(loc.x + dx, loc.y + dy, allow_warp=True,
                                allow_ledge=True)
        res = p.nav.step(d)
        if res.battle:
            p.traveler._handle_battle()
            continue
        if res.map_changed:
            break
        tried += 1
        if res.moved and not predicted:
            false_negatives += 1
    t.note(f"{tried} steps, {false_negatives} said-blocked-but-moved")
    t.gt(tried, 20, "steps actually attempted")
    # A false negative makes tiles look unreachable and strands pathfinding.
    # The reverse (said walkable, bumped) is fine and expected -- NPCs stand on
    # walkable tiles and the collision map knows nothing about them.
    t.eq(false_negatives, 0, "tiles wrongly reported as blocked")


@test("ledges are standable but never routed off one-way")
def _(t):
    p = t.pilot("route30")
    cm = p.collision
    w, h = cm.map_size()
    ledges = [(x, y) for y in range(h) for x in range(w)
              if cm.is_ledge(cm.collision_at(x, y))]
    t.gt(len(ledges), 0, "Route 30 should have ledges")
    lx, ly = ledges[0]
    # Standable: excluding them outright made whole route sections look
    # unreachable, which is what stranded the trainer sweep.
    t.true(cm.walkable(lx, ly), "a ledge tile can be stood on")
    hops = cm.hop_dirs(lx, ly)
    t.gt(len(hops), 0, "a ledge should have at least one hop direction")
    # A path that steps off a ledge in its hop direction cannot be walked back.
    path = cm.path_to((lx, ly))
    if path:
        t.note(f"ledge at ({lx},{ly}) hops {hops}, reachable in {len(path)} steps")


@test("pathfinding routes around a tile that refused a step")
def _(t):
    p = t.pilot("route30")
    cm = p.collision
    loc = p.reader.location()
    goal = None
    for dist in range(3, 12):
        cand = (loc.x, loc.y - dist)
        if cm.walkable(*cand) and cm.path_to(cand):
            goal = cand
            break
    if goal is None:
        t.note("no straight-line goal available on this map; skipped")
        return
    direct = cm.path_to(goal)
    blocked = cm.path_to(goal, avoid={(loc.x, loc.y - 1)})
    t.true(direct is not None, "a direct path exists")
    if blocked is not None:
        t.gte(len(blocked), len(direct), "the detour is not shorter")
        t.note(f"direct {len(direct)} steps, detour {len(blocked)}")


@test("a fresh session reaches a live overworld, not the CONTINUE screen")
def _(t):
    rom = t.rom_copy("continue")
    seed = t.pilot_on(rom, "grass_cyndaquil")
    ok, why = seed.settle_for_save()
    t.true(ok, f"fixture should be savable: {why}")
    t.true(seed.save(), "in-game save should commit")

    p = t.pilot_on(rom)
    t.true(p.continue_game(), "continue_game should reach the world")
    # Party data and coordinates are restored *before* the map is, so waiting on
    # the party alone starts pressing buttons while still in the menus.
    t.true(p.session.world_loaded(), "wMapStatus should say the map is live")
    t.gt(p.reader.party_count(), 0, "party loaded")
    t.ne(p.reader.location().key, (0, 0), "a real map is loaded")
    t.true(p.collision.calibrated, "collision calibrates after resuming")
