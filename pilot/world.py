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
CLERK = re.compile(r"^\s*object_event\s+(\d+)\s*,\s*(\d+)\s*,\s*SPRITE_CLERK\b")
# `pokemart MARTTYPE_STANDARD, MART_CHERRYGROVE` -- the type says how the
# counter behaves and the constant says what it stocks. Only the standard type
# is driven: a bargain shop sells one of each and a pharmacy has its own
# quantity box, so buying five of something there is a different sequence.
SHOP = re.compile(r"^\s*pokemart\s+(MARTTYPE_\w+)\s*,\s*([A-Z0-9_]+)")
MARTTYPE_STANDARD = "MARTTYPE_STANDARD"
# An item lying on the ground. The script name is the join to `itemball ITEM`
# elsewhere in the same file, which is the only place the item is named; the
# event flag is what says whether it is still there.
ITEMBALL = re.compile(
    r"^\s*object_event\s+(-?\d+)\s*,\s*(-?\d+)\s*,\s*\w+\s*,\s*\w+\s*,"
    r"\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*,\s*\w+\s*,"
    r"\s*OBJECTTYPE_ITEMBALL\s*,\s*\d+\s*,\s*(\w+)\s*,\s*(\S+?)\s*$"
)
# A fruit tree is OBJECTTYPE_SCRIPT like any NPC, so the sprite is the only
# thing that identifies one. Which is also why it has no event flag to read:
# a tree regrows, and what it gives is decided by data/items/fruit_trees.asm
# rather than by the object.
FRUIT_TREE = re.compile(
    r"^\s*object_event\s+(-?\d+)\s*,\s*(-?\d+)\s*,\s*SPRITE_FRUIT_TREE\b")
# `LabelName:` at column zero, then `itemball ANTIDOTE` inside it.
SCRIPT_LABEL = re.compile(r"^([A-Za-z_]\w*):\s*$")
ITEMBALL_GIVES = re.compile(r"^\s*itemball\s+([A-Z0-9_]+)")
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
        # MAP_CONST -> {clerk: (x, y), marts: (MART_CONST, ...)}
        self.shops: dict[str, dict] = {}
        # MAP_CONST -> [ {x, y, kind, item, event} ]
        self.takeables: dict[str, list[dict]] = {}
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
        """Everything a map's own script file says about the map.

        Warps, the healing nurse, the trainers, and now the shop counters --
        all in one pass over each file. It used to be two passes over the same
        text, which is 1,300 files read twice for no reason; adding a third
        thing to look for was the moment that stopped being tidy enough to
        leave alone.
        """
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
            entries: list[dict] = []
            found_trainers: list[dict] = []
            marts: list[str] = []
            clerk: tuple[int, int] | None = None
            balls: list[dict] = []
            trees: list[dict] = []
            # script label -> the item its `itemball` line hands over. Built in
            # the same pass, and the objects are joined to it afterwards --
            # a map's object list sits at the bottom of the file, well after the
            # scripts it names, but nothing guarantees that so the join waits.
            gives: dict[str, str] = {}
            label: str | None = None
            in_warps = False
            for raw in f.read_text(errors="replace").splitlines():
                line = raw.split(";", 1)[0]
                stripped = line.strip()
                m = SCRIPT_LABEL.match(line)
                if m:
                    label = m.group(1)
                m = ITEMBALL_GIVES.match(line)
                if m and label:
                    gives[label] = m.group(1)
                # Warps are the only thing that needs to know where in the file
                # it is, because `warp_event` appears in exactly one block.
                if stripped.startswith("def_warp_events"):
                    in_warps = True
                elif in_warps and stripped.startswith("def_"):
                    in_warps = False
                elif in_warps:
                    m = WARP.match(line)
                    if m:
                        x, y, to, to_warp = m.groups()
                        entries.append({"x": int(x), "y": int(y),
                                        "to": to, "to_warp": int(to_warp)})
                    continue
                m = NURSE.match(raw)
                if m and const not in self.nurses:
                    self.nurses[const] = (int(m.group(1)), int(m.group(2)))
                m = CLERK.match(raw)
                if m and clerk is None:
                    clerk = (int(m.group(1)), int(m.group(2)))
                m = SHOP.match(line)
                # Standard counters only. A bargain shop stocks one of each and
                # a pharmacy and the rooftop sale have their own quantity boxes,
                # so `buy_from_clerk`'s five-box sequence does not describe
                # them -- and a shop the pilot cannot drive is worse than no
                # shop at all, because it is a place it will walk to.
                if m and m.group(1) == MARTTYPE_STANDARD and m.group(2) not in marts:
                    marts.append(m.group(2))
                m = ITEMBALL.match(line)
                if m:
                    x, y, script, event = m.groups()
                    balls.append({"x": int(x), "y": int(y), "kind": "ball",
                                  "script": script,
                                  "event": None if event == "-1" else event})
                m = FRUIT_TREE.match(raw)
                if m:
                    trees.append({"x": int(m.group(1)), "y": int(m.group(2)),
                                  "kind": "tree", "item": None, "event": None})
                m = TRAINER.match(line)
                if m:
                    x, y, sprite, _move, sight, script, event = m.groups()
                    found_trainers.append({
                        "x": int(x), "y": int(y), "sprite": sprite,
                        "sight": int(sight), "script": script,
                        "event": None if event == "-1" else event,
                    })
            for ball in balls:
                ball["item"] = gives.get(ball.pop("script"))
            if balls or trees:
                self.takeables[const] = balls + trees
            if found_trainers:
                self.trainers[const] = found_trainers
            if entries:
                self.warps[const] = entries
            # A `pokemart` line with no clerk to talk to is not somewhere the
            # pilot can shop, and a clerk with no `pokemart` sells nothing.
            # Both halves or neither.
            if marts and clerk is not None:
                self.shops[const] = {"clerk": clerk, "marts": tuple(marts)}

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

    def routes_from(self, start_const: str, targets, max_depth: int = 8,
                    avoid_hops=None) -> dict[str, list]:
        """Routes to several named places at once, from one search.

        `route_to` answers "the nearest map matching a predicate", which is the
        right shape for finding a Pokemon Center and the wrong shape for
        ranking a list. Asking it about ten destinations means ten
        breadth-first walks over the same graph; this is the same walk, stopping
        once every place asked about has been found.

        Answers a dict, so "not reachable" and "reachable at no cost" stay
        distinguishable: an absent key is the first, and the map you are
        standing on is never included -- that is not a journey.
        """
        want = {t for t in targets if t != start_const}
        found: dict[str, list] = {}
        if not want:
            return found
        avoid_hops = avoid_hops or set()
        seen = {start_const}
        q: deque[tuple[str, list]] = deque([(start_const, [])])
        while q and len(found) < len(want):
            cur, path = q.popleft()
            if len(path) >= max_depth:
                continue
            for kind, target, warp in self.neighbours(cur):
                if target in seen or (cur, kind, target) in avoid_hops:
                    continue
                seen.add(target)
                hop = path + [(kind, target, warp)]
                if target in want:
                    found[target] = hop
                q.append((target, hop))
        return found

    def shops_selling(self, name: str) -> tuple[str, ...]:
        """Every map with a standard counter that stocks `name`."""
        from . import items as I
        return tuple(
            const for const, shop in sorted(self.shops.items())
            if name in I.sold_at(str(self.root), shop["marts"])
        )

    def nearest_shop_for(self, start_const: str, names,
                         max_depth: int = 8) -> tuple[str, str, list] | None:
        """The closest counter that stocks any of `names`. -> (map, item, route).

        One graph search for every candidate shop rather than one per shop,
        which is what `routes_from` is for -- there are twenty-two counters and
        asking about them one at a time is twenty-two walks over the same graph.

        Ties break on fewest legs first, then on the order `names` was given,
        so a caller that lists what it wants in preference order gets it.
        """
        from . import items as I
        wanted = list(names)
        candidates: dict[str, str] = {}
        for const, shop in self.shops.items():
            stock = I.sold_at(str(self.root), shop["marts"])
            for name in wanted:
                if name in stock:
                    candidates[const] = name
                    break
        if not candidates:
            return None
        if start_const in candidates:
            return (start_const, candidates[start_const], [])
        routes = self.routes_from(start_const, candidates, max_depth=max_depth)
        if not routes:
            return None
        best = min(routes, key=lambda c: (len(routes[c]),
                                          wanted.index(candidates[c]), c))
        return (best, candidates[best], routes[best])
