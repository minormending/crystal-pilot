"""Which wild Pokemon actually appear where.

Parsed from data/wild/*.asm so the in-game menus can offer the species you could
plausibly meet on the route you are standing on, instead of asking you to type a
name and then hunting fruitlessly for one that was never there.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

DEF_WILD = re.compile(r"^\s*def_(\w+)_wildmons\s+([A-Z0-9_]+)")
ENTRY = re.compile(r"^\s*db\s+(\d+)\s*,\s*([A-Z0-9_]+)\s*$")
END_WILD = re.compile(r"^\s*end_\w+_wildmons")

SOURCES = ("johto_grass.asm", "johto_water.asm", "kanto_grass.asm",
           "kanto_water.asm")

# A grass table is three blocks of seven: morning, day, night, in that order.
# Which block you meet depends on the clock, and the difference is not cosmetic
# -- Route 29 swaps PIDGEY and SENTRET for HOOTHOOT after dark. Water tables
# have a single block that applies all day.
GRASS_SLOTS_PER_TIME = 7
MORN, DAY, NITE = 0, 1, 2


@lru_cache(maxsize=4)
def load(source_root: str) -> dict[str, list[dict]]:
    """MAP_CONST -> [{species, level, kind, time}], in table order.

    `time` is 0/1/2 for a grass slot's morning/day/night block, or None for a
    table that does not vary with the clock.
    """
    root = Path(source_root) / "data" / "wild"
    out: dict[str, list[dict]] = {}
    for name in SOURCES:
        path = root / name
        if not path.exists():
            continue
        current, kind = None, ""
        for line in path.read_text(errors="replace").splitlines():
            line = line.split(";", 1)[0]
            m = DEF_WILD.match(line)
            if m:
                kind, current = m.group(1), m.group(2)
                out.setdefault(current, [])
                continue
            if END_WILD.match(line):
                current = None
                continue
            if current:
                m = ENTRY.match(line)
                if m:
                    slot = len(out[current])
                    time = (slot // GRASS_SLOTS_PER_TIME
                            if kind == "grass" else None)
                    out[current].append({"level": int(m.group(1)),
                                         "species": m.group(2),
                                         "kind": kind, "time": time})
    return out


def species_on(source_root: str, map_const: str,
               time_of_day: int | None = None,
               kinds: tuple[str, ...] = ("grass",)) -> list[str]:
    """Distinct species on a map, commonest first.

    Frequency is approximated by how many slots a species occupies, which is
    what actually decides how often you meet it.

    Pass `time_of_day` (0 morning, 1 day, 2 night) to get only what is out
    now. Without it the list spans the whole day, which is how a menu came to
    offer PIDGEY at midnight on a route where nothing but HOOTHOOT appears
    after dark -- the fruitless hunt this module exists to prevent.

    Grass only by default, for the same reason. The pilot walks; it does not
    surf or fish, so a route's water table is full of Pokemon it can pace the
    grass all day without ever meeting. Route 30 was offering POLIWAG.
    """
    entries = [e for e in load(str(source_root)).get(map_const, [])
               if e["kind"] in kinds]
    if time_of_day is not None:
        entries = [e for e in entries
                   if e["time"] is None or e["time"] == time_of_day]
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["species"]] = counts.get(e["species"], 0) + 1
    return [s for s, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def levels_on(source_root: str, map_const: str, species: str) -> tuple[int, int]:
    """(lowest, highest) level a species appears at on a map."""
    levels = [e["level"] for e in load(str(source_root)).get(map_const, [])
              if e["species"] == species]
    return (min(levels), max(levels)) if levels else (0, 0)
