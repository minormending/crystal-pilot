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


@lru_cache(maxsize=4)
def load(source_root: str) -> dict[str, list[dict]]:
    """MAP_CONST -> [{species, level, kind}], in table order."""
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
                    out[current].append({"level": int(m.group(1)),
                                         "species": m.group(2), "kind": kind})
    return out


def species_on(source_root: str, map_const: str) -> list[str]:
    """Distinct species on a map, commonest first.

    Frequency is approximated by how many slots a species occupies across the
    time-of-day tables, which is what actually decides how often you meet it.
    """
    entries = load(str(source_root)).get(map_const, [])
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["species"]] = counts.get(e["species"], 0) + 1
    return [s for s, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def levels_on(source_root: str, map_const: str, species: str) -> tuple[int, int]:
    """(lowest, highest) level a species appears at on a map."""
    levels = [e["level"] for e in load(str(source_root)).get(map_const, [])
              if e["species"] == species]
    return (min(levels), max(levels)) if levels else (0, 0)
