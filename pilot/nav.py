"""Overworld movement.

Every movement primitive is position-verified: we hold a direction and watch
wXCoord/wYCoord until they actually change, rather than assuming a fixed number
of frames per tile. Walking always yields immediately on a wild encounter or a
map change (a warp), because both invalidate whatever the caller was doing.
"""
from __future__ import annotations

DIRS = ("up", "down", "left", "right")
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}
# Screen coords grow right/down, so a step's effect on (x, y):
DELTA = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
# Which way to walk to leave a map via a given edge.
EDGE_TO_BUTTON = {"north": "up", "south": "down", "west": "left", "east": "right"}


class StepResult:
    __slots__ = ("moved", "battle", "map_changed", "blocked")

    def __init__(self, moved=False, battle=False, map_changed=False, blocked=False):
        self.moved = moved
        self.battle = battle
        self.map_changed = map_changed
        self.blocked = blocked

    @property
    def interrupted(self) -> bool:
        return self.battle or self.map_changed

    def __repr__(self) -> str:
        flags = [k for k in ("moved", "battle", "map_changed", "blocked")
                 if getattr(self, k)]
        return f"<step {'+'.join(flags) or 'nothing'}>"


class Navigator:
    def __init__(self, session, reader, collision=None):
        self.s = session
        self.r = reader
        # When a calibrated CollisionMap is attached, movement is planned with a
        # breadth-first search over real walkability instead of bumping into
        # things to find out where the walls are.
        self.collision = collision

    # --- primitives --------------------------------------------------------
    def step(self, direction: str, timeout: int = 60) -> StepResult:
        """Take exactly one tile step. Yields early on battle or warp."""
        if direction not in DIRS:
            raise ValueError(f"bad direction {direction!r}")
        loc0 = self.r.location()
        self.s.pyboy.button_press(direction)
        try:
            for _ in range(timeout):
                self.s.tick(1)
                if self.r.in_battle():
                    return StepResult(battle=True)
                loc = self.r.location()
                # (0, 0) means a map load is in flight, not a new map.
                if loc.key != loc0.key and loc.key != (0, 0):
                    return StepResult(moved=True, map_changed=True)
                if (loc.x, loc.y) != (loc0.x, loc0.y):
                    return StepResult(moved=True)
        finally:
            self.s.pyboy.button_release(direction)
            self.s.tick(3)
        return StepResult(blocked=True)

    def walk(self, direction: str, tiles: int) -> StepResult:
        """Step `tiles` times in one direction, stopping on interruption."""
        last = StepResult()
        for _ in range(tiles):
            last = self.step(direction)
            if last.interrupted or last.blocked:
                return last
        return last

    def face(self, direction: str) -> None:
        """Turn without necessarily moving (a tap is a turn when blocked)."""
        self.s.tap(direction, hold=4, gap=6)

    # --- goal-directed -----------------------------------------------------
    def walk_to(self, x: int, y: int, max_steps: int = 80,
                on_battle=None) -> StepResult:
        """Walk to (x, y) on the current map, planning a route when possible."""
        if self.collision is not None and self.collision.calibrated:
            return self.follow_path_to((x, y), on_battle=on_battle)
        return self._walk_to_greedy(x, y, max_steps=max_steps)

    # --- planned movement --------------------------------------------------
    def follow_path_to(self, goal, on_battle=None, replans: int = 8,
                       allow_warp_goal: bool = True,
                       max_battles: int = 60) -> StepResult:
        """BFS to `goal` and walk the route, re-planning when knocked off it.

        A step can fail spuriously (the player turns before moving, or an NPC is
        in the way for a moment), so each step is retried before giving up on the
        plan and re-planning from wherever we actually are.
        """
        cm = self.collision
        from_key = self.r.location().key
        # Tiles that refused a step. The collision map knows about terrain but
        # not about NPCs standing on it, so a tile that bumps once is excluded
        # from the next plan instead of being retried forever.
        avoid: set[tuple[int, int]] = set()
        # Wild encounters interrupt constantly when crossing grass. They are not
        # navigation failures -- no progress is lost -- so they get their own
        # generous allowance instead of eating the replan budget, which exists
        # for genuine obstacles.
        battles = 0
        attempts = 0
        cleared_once = False
        while attempts < replans:
            if self.r.in_battle():
                if on_battle is None:
                    return StepResult(battle=True)
                battles += 1
                if battles > max_battles:
                    return StepResult(blocked=True)
                on_battle()
                if self.r.in_battle():
                    return StepResult(battle=True)   # handler left it running
                continue
            attempts += 1
            if self.r.location().key != from_key:
                return StepResult(moved=True, map_changed=True)
            path = cm.path_to(goal, allow_warp_goal=allow_warp_goal, avoid=avoid)
            if path is None:
                if not avoid or cleared_once:
                    # Genuinely unreachable -- e.g. the only corridor is held by
                    # an NPC. Clearing the avoid set again would just walk back
                    # into it forever.
                    return StepResult(blocked=True)
                cleared_once = True
                avoid.clear()
                path = cm.path_to(goal, allow_warp_goal=allow_warp_goal)
                if path is None:
                    return StepResult(blocked=True)
            if not path:
                return StepResult(moved=True)
            derailed = False
            for d in path:
                before = self.r.location()
                res = None
                for _attempt in range(3):
                    res = self.step(d, timeout=90)
                    if res.moved or res.battle or res.map_changed:
                        break
                if res.battle:
                    if on_battle is None:
                        return res
                    battles += 1
                    if battles > max_battles:
                        return StepResult(blocked=True)
                    on_battle()
                    if self.r.in_battle():
                        return StepResult(battle=True)
                    derailed = True
                    break
                if res.map_changed or self.r.location().key != from_key:
                    return StepResult(moved=True, map_changed=True)
                if not res.moved:
                    dx, dy = DELTA[d]
                    avoid.add((before.x + dx, before.y + dy))
                    derailed = True
                    break
                after = self.r.location()
                dx, dy = DELTA[d]
                if (after.x, after.y) != (before.x + dx, before.y + dy):
                    derailed = True    # ledge jump or forced movement
                    break
            if not derailed:
                return StepResult(moved=True)
        return StepResult(blocked=True)

    def _walk_to_greedy(self, x: int, y: int, max_steps: int = 80) -> StepResult:
        """Greedy walk to (x, y) on the current map, sidestepping obstacles.

        No collision map is consulted -- we try the axis with the larger error,
        and on a bump fall back to the other axis. That is enough for the short,
        mostly-open hops the pilot needs (grass patches, town streets, doorways).
        """
        last = StepResult()
        stuck = 0
        for _ in range(max_steps):
            loc = self.r.location()
            dx, dy = x - loc.x, y - loc.y
            if dx == 0 and dy == 0:
                return StepResult(moved=True)
            # Prefer the longer axis; try the other one when blocked.
            options = []
            if abs(dx) >= abs(dy):
                options = [("right" if dx > 0 else "left") if dx else None,
                           ("down" if dy > 0 else "up") if dy else None]
            else:
                options = [("down" if dy > 0 else "up") if dy else None,
                           ("right" if dx > 0 else "left") if dx else None]
            options = [o for o in options if o]
            progressed = False
            for d in options:
                last = self.step(d)
                if last.interrupted:
                    return last
                if last.moved:
                    progressed = True
                    break
            if not progressed:
                stuck += 1
                if stuck >= 3:
                    return StepResult(blocked=True)
            else:
                stuck = 0
        return StepResult(blocked=True)

    def settle(self, timeout: int = 240) -> None:
        """Wait out an in-flight map load so coordinate reads are meaningful."""
        for _ in range(timeout):
            if self.r.location().key != (0, 0):
                return
            self.s.tick(1)

    def _on_new_map(self, from_key: tuple[int, int]) -> bool:
        self.settle()
        key = self.r.location().key
        return key != from_key and key != (0, 0)

    def wait_for_map_change(self, from_key: tuple[int, int],
                           timeout: int = 240) -> bool:
        """A warp is not instant: the transition runs for many frames after the
        step that triggered it, so arriving on the tile is not arriving on the
        new map. Tick until wMapGroup/wMapNumber actually change."""
        for _ in range(timeout):
            self.s.tick(1)
            key = self.r.location().key
            if key != from_key and key != (0, 0):
                self.s.tick(30)   # let the new map settle before reading coords
                return True
            if self.r.in_battle():
                return False
        return False

    def take_warp(self, x: int, y: int, push: str | None = None,
                  timeout: int = 240, on_battle=None) -> bool:
        """Walk onto a warp tile and confirm the map actually changed.

        Standing on a doorway is not the same as going through it: building
        doors in Gen 2 trigger when you walk *into* them, so after reaching the
        tile we try stepping onward. The direction is discovered rather than
        assumed (callers that know it can pass `push`), and we return to the
        warp tile between attempts since a wrong guess steps off it.
        """
        from_key = self.r.location().key

        def crossed() -> bool:
            return self._on_new_map(from_key)

        directions = [push] if push else [None, "up", "down", "left", "right"]
        for d in directions:
            if self.r.in_battle():
                if on_battle is None:
                    return False
                on_battle()
            res = self.walk_to(x, y, on_battle=on_battle)
            if res.map_changed or crossed():
                self.s.tick(30)
                return True
            if res.battle:
                if on_battle is None:
                    return False
                on_battle()
                continue
            # Give the warp a chance to fire from simply arriving.
            if self.wait_for_map_change(from_key, timeout=60):
                return True
            if d is None:
                continue
            step = self.step(d, timeout=90)
            if step.map_changed or crossed():
                self.s.tick(30)
                return True
            if self.wait_for_map_change(from_key, timeout=timeout):
                return True
        return False

    def cross_edge(self, direction: str, max_steps: int = 4000,
                   on_battle=None) -> StepResult:
        """Walk off a map edge onto the adjoining map (a `connection` link).

        A connection spans only part of the shared edge, so this walks to
        walkable edge tiles (centre-out) and tries to step off each one. With a
        collision map the route to each candidate is planned, which matters
        because routes are full of one-way ledges -- an exploratory walker can
        drop down one and strand itself in a region with no way back up.
        """
        if self.collision is None or not self.collision.calibrated:
            return self._cross_edge_explore(direction, max_steps=max_steps,
                                            on_battle=on_battle)
        cm = self.collision
        btn = EDGE_TO_BUTTON[direction]
        from_key = self.r.location().key

        def crossed() -> bool:
            return self._on_new_map(from_key)

        for tile in cm.edge_tiles(direction)[:24]:
            if self.r.in_battle():
                if on_battle is None:
                    return StepResult(battle=True)
                on_battle()
            if crossed():
                self.s.tick(30)
                return StepResult(moved=True, map_changed=True)
            res = self.follow_path_to(tile, on_battle=on_battle)
            if res.map_changed or crossed():
                self.s.tick(30)
                return StepResult(moved=True, map_changed=True)
            if res.blocked:
                continue
            # On the edge tile: step outward to leave the map.
            for _ in range(3):
                step = self.step(btn, timeout=90)
                if step.battle:
                    if on_battle is None:
                        return step
                    on_battle()
                    break
                if step.map_changed or crossed():
                    self.s.tick(30)
                    return StepResult(moved=True, map_changed=True)
                if not step.moved:
                    break
        return StepResult(blocked=True)

    def _cross_edge_explore(self, direction: str, max_steps: int = 1500,
                            on_battle=None) -> StepResult:
        """Fallback edge crossing with no collision data: greedy wall-follower."""
        btn = EDGE_TO_BUTTON[direction]
        from_key = self.r.location().key
        slide = "down" if btn in ("left", "right") else "right"
        stuck = 0
        for _ in range(max_steps):
            if self.r.in_battle():
                if on_battle is None:
                    return StepResult(battle=True)
                on_battle()
                continue
            res = self.step(btn)
            if res.map_changed or self._on_new_map(from_key):
                self.s.tick(30)
                return StepResult(moved=True, map_changed=True)
            if res.battle:
                if on_battle is None:
                    return res
                on_battle()
                continue
            if res.moved:
                stuck = 0
                continue
            side = self.step(slide)
            if side.map_changed or self._on_new_map(from_key):
                self.s.tick(30)
                return StepResult(moved=True, map_changed=True)
            if not side.moved:
                slide = OPPOSITE[slide]
                stuck += 1
                if stuck > 10:
                    return StepResult(blocked=True)
        return StepResult(blocked=True)

    def find_grass(self, max_steps: int = 600, on_battle=None) -> bool:
        """Get the player onto an encounter tile."""
        if self.r.on_grass() or self.r.in_battle():
            return True
        if self.collision is not None and self.collision.calibrated:
            from .symbols import GRASS_COLLISION
            res = self.follow_path_to(
                lambda p: self.collision.walkable(p[0], p[1])
                and self.collision.collision_at(p[0], p[1]) in GRASS_COLLISION,
                on_battle=on_battle,
            )
            if res.battle or self.r.in_battle():
                return True
            return self.r.on_grass()
        return self._find_grass_sweep(max_steps=max_steps)

    def _find_grass_sweep(self, max_steps: int = 600) -> bool:
        """Fallback grass search with no collision data: boustrophedon sweep."""
        primary, secondary = "left", "down"
        steps = 0
        flips = 0
        while steps < max_steps:
            res = self.step(primary)
            steps += 1
            if res.battle or self.r.on_grass():
                return True
            if res.map_changed:
                return False
            if res.blocked:
                side = self.step(secondary)
                steps += 1
                if side.battle or self.r.on_grass():
                    return True
                if side.blocked or side.map_changed:
                    primary = OPPOSITE[primary]
                    secondary = OPPOSITE[secondary]
                    flips += 1
                    if flips > 4:
                        return self.r.on_grass()
                else:
                    primary = OPPOSITE[primary]
        return self.r.on_grass()

    def pace_until_battle(self, max_steps: int = 400,
                          axis: tuple[str, str] = ("left", "right")) -> StepResult:
        """Walk back and forth on grass until a wild battle starts.

        Each step onto an encounter tile rolls for a battle. If a step leaves
        the grass we immediately reverse, so the pilot stays in the patch
        instead of drifting off the route.
        """
        a, b = axis
        d = a
        for _ in range(max_steps):
            res = self.step(d)
            if res.battle:
                return res
            if res.map_changed:
                return res
            if res.blocked or not self.r.on_grass():
                d = b if d == a else a
                if not self.r.on_grass() and not res.blocked:
                    back = self.step(d)   # step back into the patch
                    if back.battle:
                        return back
        return StepResult()
