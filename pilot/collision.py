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
