"""A world graph built from the disassembly: edge connections + indoor warps.

Two kinds of link exist in Gen 2 and the pilot needs both:
  * connections -- walking off a map edge onto the adjoining route/town
    (data/maps/attributes.asm)
  * warps       -- doorways and stairs (warp_events in maps/<Name>.asm)

Together they let the pilot answer "how do I get from this route to the nearest
Pokemon Center", which is what makes unattended healing possible.
"""
from __future__ import annotations

import re
from collections import deque
from pathlib import Path

MAP_ATTR = re.compile(r"^\s*map_attributes\s+(\w+)\s*,\s*([A-Z0-9_]+)\s*,")
CONNECTION = re.compile(r"^\s*connection\s+(north|south|east|west)\s*,\s*(\w+)\s*,\s*([A-Z0-9_]+)\s*,\s*(-?\d+)")
WARP = re.compile(r"^\s*warp_event\s+(\d+)\s*,\s*(\d+)\s*,\s*([A-Z0-9_]+)\s*,\s*(\d+)")
NURSE = re.compile(r"^\s*object_event\s+(\d+)\s*,\s*(\d+)\s*,\s*SPRITE_NURSE\b")
# object_event x, y, SPRITE, MOVEMENT, rx, ry, h1, h2, PAL, TYPE, n, script, event
TRAINER = re.compile(
    r"^\s*object_event\s+(-?\d+)\s*,\s*(-?\d+)\s*,\s*(\w+)\s*,\s*(\w+)\s*,"
    r"\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*,\s*\w+\s*,"
    r"\s*OBJECTTYPE_TRAINER\s*,\s*(\d+)\s*,\s*(\w+)\s*,\s*(\S+?)\s*$"
)

OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}
# Which way to walk to leave via an edge.
EDGE_TO_BUTTON = {"north": "up", "south": "down", "west": "left", "east": "right"}


class World:
    def __init__(self, gamedata, source_root: str | Path):
        self.gd = gamedata
        self.root = Path(source_root)
        # MAP_CONST -> {direction: MAP_CONST}
        self.connections: dict[str, dict[str, str]] = {}
        # MAP_CONST -> [ {x, y, to, to_warp} ]
        self.warps: dict[str, list[dict]] = {}
        # MAP_CONST -> (x, y) of the healing nurse, where one exists
        self.nurses: dict[str, tuple[int, int]] = {}
        # MAP_CONST -> [ {x, y, sprite, sight, script, event} ]
        self.trainers: dict[str, list[dict]] = {}
        self._load_connections()
        self._load_warps()

    def _load_connections(self) -> None:
        attrs = self.root / "data" / "maps" / "attributes.asm"
        if not attrs.exists():
            return
        current = None
        for line in attrs.read_text(errors="replace").splitlines():
            line = line.split(";", 1)[0]
            m = MAP_ATTR.match(line)
            if m:
                current = m.group(2)
                self.connections.setdefault(current, {})
                continue
            m = CONNECTION.match(line)
            if m and current:
                direction, _label, target, _off = m.groups()
                self.connections[current][direction] = target

    def _load_warps(self) -> None:
        """Warp events live in each map's own script file."""
        mapdir = self.root / "maps"
        if not mapdir.exists():
            return
        # map_const name -> file. Map labels are CamelCase of the const.
        by_camel = {}
        for const in self.gd.maps_by_name:
            by_camel[const.replace("_", "").lower()] = const
        for f in mapdir.glob("*.asm"):
            const = by_camel.get(f.stem.replace("_", "").lower())
            if not const:
                continue
            entries = []
            in_warps = False
            found_trainers = []
            for line in f.read_text(errors="replace").splitlines():
                m = NURSE.match(line)
                if m and const not in self.nurses:
                    self.nurses[const] = (int(m.group(1)), int(m.group(2)))
                m = TRAINER.match(line.split(";", 1)[0])
                if m:
                    x, y, sprite, _move, sight, script, event = m.groups()
                    found_trainers.append({
                        "x": int(x), "y": int(y), "sprite": sprite,
                        "sight": int(sight), "script": script,
                        "event": None if event == "-1" else event,
                    })
            if found_trainers:
                self.trainers[const] = found_trainers
            for line in f.read_text(errors="replace").splitlines():
                stripped = line.split(";", 1)[0].strip()
                if stripped.startswith("def_warp_events"):
                    in_warps = True
                    continue
                if stripped.startswith("def_") and in_warps:
                    break
                if in_warps:
                    m = WARP.match(line)
                    if m:
                        x, y, to, to_warp = m.groups()
                        entries.append({"x": int(x), "y": int(y),
                                        "to": to, "to_warp": int(to_warp)})
            if entries:
                self.warps[const] = entries

    # --- queries -----------------------------------------------------------
    def name_of(self, group: int, number: int) -> str:
        return self.gd.map_name(group, number)

    def neighbours(self, const: str) -> list[tuple[str, str, dict | None]]:
        """[(kind, target_const, warp_or_None)] where kind is 'edge' or 'warp'."""
        out: list[tuple[str, str, dict | None]] = []
        for direction, target in self.connections.get(const, {}).items():
            out.append((direction, target, None))
        for w in self.warps.get(const, []):
            out.append(("warp", w["to"], w))
        return out

    def route_to(self, start_const: str, predicate, max_depth: int = 8,
                 avoid_hops=None):
        """BFS for the nearest map satisfying `predicate(map_const)`.

        Returns a list of hops [(kind, target_const, warp_or_None)], or None.
        `avoid_hops` is a set of (from_const, kind, target_const) that failed --
        two maps can be listed as neighbours by an edge connection that is not
        actually walkable (Route 29 and Route 46 adjoin, but the way through is
        a gate building), and without excluding the failed hop the search keeps
        proposing it.
        """
        if predicate(start_const):
            return []
        avoid_hops = avoid_hops or set()
        seen = {start_const}
        q: deque[tuple[str, list]] = deque([(start_const, [])])
        while q:
            cur, path = q.popleft()
            if len(path) >= max_depth:
                continue
            for kind, target, warp in self.neighbours(cur):
                if target in seen or (cur, kind, target) in avoid_hops:
                    continue
                seen.add(target)
                hop = path + [(kind, target, warp)]
                if predicate(target):
                    return hop
                q.append((target, hop))
        return None

    def nearest_pokecenter(self, start_const: str, max_depth: int = 6):
        return self.route_to(
            start_const, lambda c: c.endswith("POKECENTER_1F"), max_depth=max_depth
        )
