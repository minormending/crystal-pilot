"""Reads the live collision map out of WRAM so movement can be planned, not guessed.

Gen 2 keeps the loaded map's blocks in wOverworldMapBlocks and each tileset's
per-quadrant collision values in ROM at wTilesetCollisionAddress. Together they
give the collision byte for any tile on the current map, which turns navigation
from trial-and-error walking into a plain breadth-first search.

The indexing is derived from GetBlockLocation (home/map.asm) and then *verified
at runtime* against wPlayerTileCollision -- the game publishes the collision of
the tile the player is standing on, so the decode can check itself rather than
being trusted.
"""
from __future__ import annotations

import re
from collections import deque
from functools import lru_cache
from pathlib import Path

from .nav import DELTA, DIRS

PERM_ROW = re.compile(r"^\s*db\s+([A-Z_]+(?:\s*\|\s*[A-Z_]+)*)\s*$")
PERM_VALUES = {"LAND_TILE": 0x00, "WATER_TILE": 0x01, "WALL_TILE": 0x0F, "TALK": 0x10}

LAND, WATER, WALL = 0x00, 0x01, 0x0F
# High-nybble groups from constants/collision_constants.asm
WARP_LO, WARP_HI = 0x70, 0x7F
LEDGE_LO, LEDGE_HI = 0xA0, 0xBF
# A ledge tile can be stood on; it is *leaving* it in the hop direction that
# jumps you two tiles and cannot be undone. Index is `collision & 7`, from the
# .ledge_table in engine/overworld/player_movement.asm.
LEDGE_HOPS = {
    0: ("right",), 1: ("left",), 2: ("up",), 3: ("down",),
    4: ("down", "right"), 5: ("down", "left"),
    6: ("up", "right"), 7: ("up", "left"),
}


@lru_cache(maxsize=4)
def load_permissions(source_root: str) -> tuple[int, ...]:
    """CollisionPermissionTable: collision value -> permission byte."""
    path = Path(source_root) / "data" / "collision" / "collision_permissions.asm"
    perms: list[int] = []
    for line in path.read_text(errors="replace").splitlines():
        m = PERM_ROW.match(line.split(";", 1)[0].rstrip())
        if not m:
            continue
        val = 0
        for part in m.group(1).split("|"):
            val |= PERM_VALUES.get(part.strip(), 0)
        perms.append(val)
    while len(perms) < 256:
        perms.append(WALL)
    return tuple(perms[:256])


# constants/map_object_constants.asm: the object-type nibble the engine
# branches on when you press A. Only the trainer value is needed here; the
# item-ball one lives in world.py, which reads the same fact out of the
# disassembly's `object_event` lines instead of out of work RAM.
OBJECTTYPE_TRAINER = 2


class CollisionMap:
    def __init__(self, session, reader, source_root):
        self.s = session
        self.r = reader
        self.perms = load_permissions(str(source_root))
        self._blocks_base = self.s.sym.addr("wOverworldMapBlocks")
        # (x_offset, y_offset) in tiles; calibrated against the live game.
        self._off = (4, 4)
        self._calibrated = False

    # --- raw reads ---------------------------------------------------------
    def _stride(self) -> int:
        return self.s.rb("wMapWidth") + 6

    def block_at(self, tx: int, ty: int, off=None) -> int:
        ox, oy = off or self._off
        x_off, y_off = tx + ox, ty + oy
        stride = self._stride()
        idx = 1 + stride * (1 + (y_off >> 1)) + (x_off >> 1)
        return self.s.pyboy.memory[self._blocks_base + idx]

    def collision_at(self, tx: int, ty: int, off=None) -> int:
        """Collision byte for a tile on the current map."""
        ox, oy = off or self._off
        block = self.block_at(tx, ty, off=(ox, oy))
        quadrant = ((ty + oy) & 1) * 2 + ((tx + ox) & 1)
        bank = self.s.rb("wTilesetCollisionBank")
        addr = self.s.rw_le("wTilesetCollisionAddress")
        return self.s.read_rom(bank, (addr + block * 4 + quadrant) & 0xFFFF)

    # --- calibration -------------------------------------------------------
    def calibrate(self, candidates=((4, 4), (0, 0), (4, 0), (0, 4), (2, 2), (6, 6),
                                    (5, 5), (3, 3))) -> bool:
        """Confirm the decode by reproducing wPlayerTileCollision.

        If the derived offset does not reproduce the game's own value, try a few
        nearby ones rather than silently pathfinding against garbage.
        """
        loc = self.r.location()
        truth = self.r.tile_collision()
        for off in candidates:
            try:
                if self.collision_at(loc.x, loc.y, off=off) == truth:
                    self._off = off
                    self._calibrated = True
                    return True
            except Exception:  # noqa: BLE001,S112 -- a wrong offset can read out of bounds; that is what the loop is for
                continue
        return False

    @property
    def calibrated(self) -> bool:
        return self._calibrated

    def verify(self) -> bool:
        """Re-check the decode against the player's current tile."""
        loc = self.r.location()
        try:
            return self.collision_at(loc.x, loc.y) == self.r.tile_collision()
        except Exception:  # noqa: BLE001 -- an unmapped read means uncalibrated, not a crash
            return False

    # --- classification ----------------------------------------------------
    def permission(self, coll: int) -> int:
        return self.perms[coll & 0xFF] & 0x0F

    def is_wall(self, coll: int) -> bool:
        return self.permission(coll) == WALL

    def is_water(self, coll: int) -> bool:
        return self.permission(coll) == WATER

    @staticmethod
    def is_ledge(coll: int) -> bool:
        return LEDGE_LO <= coll <= LEDGE_HI

    def hop_dirs(self, tx: int, ty: int) -> tuple[str, ...]:
        """Directions that would hop a ledge from this tile (empty if none)."""
        coll = self.collision_at(tx, ty)
        if not self.is_ledge(coll):
            return ()
        return LEDGE_HOPS.get(coll & 7, ())

    @staticmethod
    def is_warp(coll: int) -> bool:
        return WARP_LO <= coll <= WARP_HI

    def walkable(self, tx: int, ty: int, allow_warp: bool = False,
                 allow_ledge: bool = True) -> bool:
        """Can the player stand on this tile?

        Ledge tiles are standable -- what is one-way is *hopping off* one, which
        path_to handles by refusing the hop move rather than by refusing the
        tile. Excluding ledge tiles outright made whole sections of routes look
        unreachable. Warps are excluded unless the caller is heading for one.
        """
        w, h = self.map_size()
        if not (0 <= tx < w and 0 <= ty < h):
            return False
        coll = self.collision_at(tx, ty)
        if self.is_wall(coll) or self.is_water(coll):
            return False
        if self.is_ledge(coll) and not allow_ledge:
            return False
        if self.is_warp(coll) and not allow_warp:
            return False
        return True

    def map_size(self) -> tuple[int, int]:
        return self.s.rb("wMapWidth") * 2, self.s.rb("wMapHeight") * 2

    # --- who is standing where ---------------------------------------------
    # The collision map is *terrain*. It has nothing to say about the Youngster
    # standing in a one-tile corridor, so a planner reading only terrain routes
    # straight through people -- bumps, learns one tile, and re-plans, once per
    # person. Reading the object arrays turns that into a route that goes round
    # them on the first attempt.
    #
    # Both arrays store coordinates offset by +4, which is measured rather than
    # assumed: on Route 30, index 0 of each is the player, and it reads raw
    # (11,57) with the player standing at (7,53).
    OBJECT_ORIGIN = 4
    # wMapObjects: 16 entries of MAPOBJECT_LENGTH, index 0 is the player's
    # placement. Sprite, y, x and a type nibble, per map_object_constants.asm.
    PLACED_COUNT, PLACED_BYTES = 16, 0x10
    PLACED_SPRITE, PLACED_Y, PLACED_X, PLACED_TYPE = 1, 2, 3, 8
    TYPE_MASK = 0x0F
    # wObjectStructs: NUM_OBJECT_STRUCTS entries, stride taken from the symbol
    # table rather than hardcoded so a patched build cannot silently shift it.
    STRUCT_COUNT = 13
    STRUCT_SPRITE, STRUCT_PLACED_INDEX, STRUCT_X, STRUCT_Y = 0, 1, 0x10, 0x11

    def placed_objects(self) -> list[dict]:
        """What the map *places*: [{index, sprite, type, x, y}].

        Index 0 is kept, because the index is what a struct points back at; the
        callers that would be confused by the player drop it.

        Tiles outside the map are dropped. Index 0 cannot be used to check the
        origin -- it holds a placement rather than a live position -- so the
        map's bounds are the only check available, and that is also the right
        way to fail: on a build that stored objects at a different origin an
        empty list means the planner walks into people and recovers, where a
        list of in-bounds but *wrong* tiles can seal a one-tile corridor.
        """
        try:
            base = self.s.sym.addr("wMapObjects")
        except KeyError:
            return []
        w, h = self.map_size()
        out = []
        for i in range(self.PLACED_COUNT):
            at = base + i * self.PLACED_BYTES
            sprite = self.s.rb(at + self.PLACED_SPRITE)
            if not sprite:
                continue
            x = self.s.rb(at + self.PLACED_X) - self.OBJECT_ORIGIN
            y = self.s.rb(at + self.PLACED_Y) - self.OBJECT_ORIGIN
            if not (0 <= x < w and 0 <= y < h):
                continue
            out.append({"index": i, "sprite": sprite, "x": x, "y": y,
                        "type": self.s.rb(at + self.PLACED_TYPE) & self.TYPE_MASK})
        return out

    def live_objects(self) -> list[dict] | None:
        """What the game has actually *spawned*, joined back to its placement.

        Two things are true of this and not of the placements, and they are the
        two reasons to prefer it:

          * the coordinates are live, so a wanderer reads where it is standing;
          * an object the game has not spawned is simply absent, whether because
            an event flag hides it or because it is too far away to matter.

        Thirteen structs against sixteen placements, so being off the list is
        ordinary rather than exceptional -- measured on Route 30 from the south
        end, eleven objects are placed and exactly one is spawned: the player.

        Index 0 is the player's own struct and is skipped: the tile the player
        stands on is not an obstacle to the player.

        Returns None, not an empty list, when the symbol table does not name the
        array. The callers need "cannot tell" apart from "nothing there" --
        `occupied` falls back to the placements, and `trainers_here` must not
        claim a map has no trainers when it has not looked.
        """
        try:
            base = self.s.sym.addr("wObjectStructs")
            stride = self.s.sym.addr("wObject1Struct") - base
            placed = self.s.sym.addr("wMapObjects")
        except KeyError:
            return None
        if stride <= 0:
            return None
        w, h = self.map_size()
        out = []
        for i in range(1, self.STRUCT_COUNT):
            at = base + i * stride
            sprite = self.s.rb(at + self.STRUCT_SPRITE)
            if not sprite:
                continue
            index = self.s.rb(at + self.STRUCT_PLACED_INDEX)
            x = self.s.rb(at + self.STRUCT_X) - self.OBJECT_ORIGIN
            y = self.s.rb(at + self.STRUCT_Y) - self.OBJECT_ORIGIN
            if not (0 <= x < w and 0 <= y < h):
                continue
            # The type comes from the placement the struct points at, because a
            # struct does not carry one. A struct pointing outside the array is
            # not trusted for its type and is still trusted for its tile:
            # something is standing there whatever it turns out to be.
            kind = None
            if index < self.PLACED_COUNT:
                kind = (self.s.rb(placed + index * self.PLACED_BYTES
                                  + self.PLACED_TYPE) & self.TYPE_MASK)
            out.append({"index": index, "sprite": sprite, "x": x, "y": y,
                        "type": kind})
        return out

    def occupied(self) -> set[tuple[int, int]]:
        """Tiles that people and props are standing on, right now.

        Read from the *live* structs where they can be read at all, because on
        the placements this is wrong in both directions at once: a wanderer is
        marked where it was placed rather than where it is, and objects an event
        flag has never spawned are marked at all.

        Falls back to the placements when the structs cannot be read. Stale
        tiles beat no tiles, because walking into somebody costs a refused step
        and `follow_path_to` recovers -- while a corridor sealed by a *wrong*
        avoid set is a route that looks impassable.
        """
        live = self.live_objects()
        if live is None:
            live = [o for o in self.placed_objects() if o["index"] != 0]
        return {(o["x"], o["y"]) for o in live}

    def trainers_here(self) -> list[dict]:
        """The trainers the game has spawned on this map: [{x, y, sprite}].

        Both arrays at once, because either alone answers wrongly. The
        placement says what an object *is* -- the type byte the game branches
        on -- and the struct says whether it is here and where. A trainer whose
        event flag has not fired is a placement with no struct, and walking to
        one is walking to nobody.

        Which is why this returns an empty list rather than falling back to the
        placements the way `occupied` does. There the fallback is a hint that
        can be wrong at the cost of a re-plan; here it would be a walk.
        """
        live = self.live_objects()
        if not live:
            return []
        # The placement index comes with them, because **a trainer moves** and
        # its tile is therefore not its identity. A caller that walks to one and
        # is turned away needs to not ask the same person again, and after a
        # step the coordinates no longer say who that was.
        return [{"index": o["index"], "x": o["x"], "y": o["y"],
                 "sprite": o["sprite"]}
                for o in live if o["type"] == OBJECTTYPE_TRAINER]

    # --- pathfinding -------------------------------------------------------
    def path_to(self, goal, start=None, allow_warp_goal: bool = True,
                max_nodes: int = 20000, avoid=None) -> list[str] | None:
        """Breadth-first path as a list of directions, or None if unreachable.

        `goal` is a single (x, y), an iterable of acceptable tiles, or a
        predicate over (x, y).
        """
        loc = self.r.location()
        start = tuple(start) if start else (loc.x, loc.y)
        if callable(goal):
            is_goal = goal
        else:
            targets = set(goal) if not (isinstance(goal, tuple) and len(goal) == 2
                                        and isinstance(goal[0], int)) else {tuple(goal)}
            def is_goal(p):
                return p in targets

        if is_goal(start):
            return []
        seen = {start}
        q: deque[tuple[tuple[int, int], list[str]]] = deque([(start, [])])
        nodes = 0
        while q and nodes < max_nodes:
            pos, path = q.popleft()
            nodes += 1
            hops = self.hop_dirs(*pos)
            for d in DIRS:
                if d in hops:
                    # Leaving a ledge this way jumps two tiles and cannot be
                    # reversed. Skip it so every planned path stays walkable
                    # back the way it came.
                    continue
                dx, dy = DELTA[d]
                nxt = (pos[0] + dx, pos[1] + dy)
                if nxt in seen:
                    continue
                goal_here = is_goal(nxt)
                if avoid and nxt in avoid and not goal_here:
                    continue    # a tile we bumped into: probably an NPC
                if not self.walkable(nxt[0], nxt[1],
                                     allow_warp=goal_here and allow_warp_goal):
                    continue
                seen.add(nxt)
                if goal_here:
                    return path + [d]
                q.append((nxt, path + [d]))
        return None

    def edge_tiles(self, direction: str) -> list[tuple[int, int]]:
        """Walkable tiles along one map edge, centre-out.

        Route connections sit inland of the corners, so trying the middle of the
        edge first finds the opening far sooner than sweeping from a corner.
        """
        w, h = self.map_size()
        if direction == "west":
            line = [(0, y) for y in range(h)]
        elif direction == "east":
            line = [(w - 1, y) for y in range(h)]
        elif direction == "north":
            line = [(x, 0) for x in range(w)]
        else:
            line = [(x, h - 1) for x in range(w)]
        vertical = direction in ("west", "east")
        mid = ((h if vertical else w) - 1) / 2
        line.sort(key=lambda p: abs((p[1] if vertical else p[0]) - mid))
        return [p for p in line if self.walkable(*p, allow_warp=True, allow_ledge=True)]
