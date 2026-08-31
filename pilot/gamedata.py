"""Parses names and move data straight out of the pokecrystal source tree.

Nothing here is hardcoded: species ids, move ids and move power/type/PP all come
from the same files the ROM was assembled from, so they cannot drift from the
build the pilot is driving.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

CONST_DEF = re.compile(r"^\s*const_def(?:\s+(-?\w+))?")
CONST = re.compile(r"^\s*const\s+([A-Z0-9_]+)")
CONST_SKIP = re.compile(r"^\s*const_skip(?:\s+(\d+))?")
CONST_NEXT = re.compile(r"^\s*const_next\s+(\$?\w+)")
NEWGROUP = re.compile(r"^\s*newgroup\s+([A-Z0-9_]+)")
MAP_CONST = re.compile(r"^\s*map_const\s+([A-Z0-9_]+)\s*,\s*(\d+)\s*,\s*(\d+)")
MOVE_ROW = re.compile(
    r"^\s*move\s+([A-Z0-9_]+)\s*,\s*([A-Z0-9_]+)\s*,\s*(\d+)\s*,\s*([A-Z0-9_]+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)"
)
# Moves whose listed power is 0 but which still deal damage in a fixed way.
# The pilot treats these as usable-but-last-resort rather than "status".
FIXED_DAMAGE_EFFECTS = {
    "EFFECT_SUPER_FANG", "EFFECT_LEVEL_DAMAGE", "EFFECT_PSYWAVE",
    "EFFECT_FIXED_DAMAGE", "EFFECT_OHKO", "EFFECT_COUNTER", "EFFECT_MIRROR_COAT",
}


def _rgbds_int(raw: str) -> int:
    raw = raw.strip()
    if raw.startswith("$"):
        return int(raw[1:], 16)
    return int(raw, 0)


def _parse_consts(path: Path, first_block_only: bool = False) -> dict[str, int]:
    """Reads an rgbds `const_def`/`const` enum block into {NAME: value}.

    `first_block_only` stops at a second `const_def`. That matters for
    pokemon_constants.asm, which restarts at 1 for the UNOWN_A..Z forms -- read
    naively, those overwrite ids 1-26 and every Pokemon from Bulbasaur to Raichu
    ends up displaying as an Unown form.
    """
    out: dict[str, int] = {}
    n = 0
    blocks = 0
    for line in path.read_text(errors="replace").splitlines():
        line = line.split(";", 1)[0]
        m = CONST_DEF.match(line)
        if m:
            blocks += 1
            if first_block_only and blocks > 1:
                break
            raw = m.group(1)
            n = _rgbds_int(raw) if raw and raw.lstrip("-$").isalnum() and raw.lstrip("-$") else 0
            continue
        m = CONST_NEXT.match(line)
        if m:
            n = _rgbds_int(m.group(1))
            continue
        m = CONST_SKIP.match(line)
        if m:
            n += int(m.group(1)) if m.group(1) else 1
            continue
        m = CONST.match(line)
        if m:
            out[m.group(1)] = n
            n += 1
    return out



def _parse_maps(path: Path) -> dict[tuple[int, int], dict]:
    """Reads map_constants.asm into {(group, number): {name, width, height}}."""
    out: dict[tuple[int, int], dict] = {}
    group = 0
    number = 0
    for line in path.read_text(errors="replace").splitlines():
        line = line.split(";", 1)[0]
        m = NEWGROUP.match(line)
        if m:
            group += 1
            number = 0
            continue
        m = MAP_CONST.match(line)
        if m:
            number += 1
            name, w, h = m.groups()
            out[(group, number)] = {
                "name": name, "width": int(w), "height": int(h),
                "group": group, "number": number,
            }
    return out


class GameData:
    def __init__(self, source_root: str | Path):
        self.root = Path(source_root)
        pk = self.root / "constants" / "pokemon_constants.asm"
        mv = self.root / "constants" / "move_constants.asm"
        moves_data = self.root / "data" / "moves" / "moves.asm"
        mapc = self.root / "constants" / "map_constants.asm"
        itemc = self.root / "constants" / "item_constants.asm"
        for p in (pk, mv, moves_data, mapc, itemc):
            if not p.exists():
                raise FileNotFoundError(
                    f"expected pokecrystal source file missing: {p}\n"
                    "Point --source at a pokecrystal disassembly checkout."
                )
        self.species: dict[str, int] = {
            k: v for k, v in _parse_consts(pk, first_block_only=True).items()
            if not k.startswith("NUM_")
        }
        self.species_by_id: dict[int, str] = {v: k for k, v in self.species.items()}
        # ANIM_* constants continue the same enum past the real moves; keep the
        # name lookup to actual moves so ids cannot be shadowed.
        self.moves: dict[str, int] = {
            k: v for k, v in _parse_consts(mv).items()
            if not k.startswith(("NUM_", "ANIM_"))
        }
        self.moves_by_id: dict[int, str] = {v: k for k, v in self.moves.items()}

        self.items: dict[str, int] = {
            k: v for k, v in _parse_consts(itemc, first_block_only=True).items()
            if not k.startswith("NUM_")
        }
        self.items_by_id: dict[int, str] = {v: k for k, v in self.items.items()}

        self.maps: dict[tuple[int, int], dict] = _parse_maps(mapc)
        self.maps_by_name: dict[str, dict] = {v["name"]: v for v in self.maps.values()}

        # move id -> stats, in table order (index 1..N matches move ids)
        self.move_info: dict[int, dict] = {}
        idx = 0
        for line in moves_data.read_text(errors="replace").splitlines():
            m = MOVE_ROW.match(line)
            if not m:
                continue
            idx += 1
            _anim, effect, power, mtype, acc, pp, chance = m.groups()
            self.move_info[idx] = {
                "id": idx,
                "name": self.moves_by_id.get(idx, f"MOVE_{idx}"),
                "effect": effect,
                "power": int(power),
                "type": mtype,
                "accuracy": int(acc),
                "pp": int(pp),
                "effect_chance": int(chance),
            }

    # --- lookups -----------------------------------------------------------
    def species_id(self, name: str) -> int:
        """Accepts 'pikachu', 'PIKACHU', 'Mr. Mime', or a raw numeric id."""
        if isinstance(name, int) or str(name).isdigit():
            return int(name)
        key = self._norm(name)
        table = {self._norm(k): v for k, v in self.species.items()}
        if key not in table:
            near = sorted(k for k in table if key[:4] and k.startswith(key[:4]))
            hint = f" Did you mean: {', '.join(near[:6])}?" if near else ""
            raise KeyError(f"unknown species {name!r}.{hint}")
        return table[key]

    def species_name(self, sid: int) -> str:
        return self.species_by_id.get(sid, f"#{sid}")

    def move_name(self, mid: int) -> str:
        if mid == 0:
            return "-"
        return self.moves_by_id.get(mid, f"MOVE_{mid}")

    def move(self, mid: int) -> dict:
        return self.move_info.get(
            mid,
            {"id": mid, "name": self.move_name(mid), "effect": "?", "power": 0,
             "type": "?", "accuracy": 100, "pp": 0, "effect_chance": 0},
        )

    def item_id(self, name: str) -> int:
        if isinstance(name, int) or str(name).isdigit():
            return int(name)
        key = self._norm(name)
        table = {self._norm(k): v for k, v in self.items.items()}
        if key not in table:
            near = sorted(k for k in table if "BALL" in k)
            raise KeyError(f"unknown item {name!r}. Balls: {', '.join(near[:8])}")
        return table[key]

    def item_name(self, iid: int) -> str:
        return self.items_by_id.get(iid, f"ITEM_{iid}")

    def map_name(self, group: int, number: int) -> str:
        info = self.maps.get((group, number))
        return info["name"] if info else f"MAP_{group}.{number}"

    def map_pretty(self, group: int, number: int) -> str:
        return self.map_name(group, number).replace("_", " ").title()

    def map_info(self, group: int, number: int) -> dict | None:
        return self.maps.get((group, number))

    def find_map(self, name: str) -> dict:
        key = self._norm(name)
        table = {self._norm(k): v for k, v in self.maps_by_name.items()}
        if key not in table:
            near = sorted(k for k in table if key[:5] and key[:5] in k)
            hint = f" Close matches: {', '.join(near[:6])}." if near else ""
            raise KeyError(f"unknown map {name!r}.{hint}")
        return table[key]

    def is_damaging(self, mid: int) -> bool:
        info = self.move(mid)
        return info["power"] > 0 or info["effect"] in FIXED_DAMAGE_EFFECTS

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(s).upper())


@lru_cache(maxsize=4)
def load(source_root: str) -> GameData:
    return GameData(source_root)
