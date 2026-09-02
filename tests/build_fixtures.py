"""Builds the save-state fixtures the suite runs against.

Fixtures are committed as gzipped PyBoy save states (~14 KB each), but they are
generated here rather than hand-made, so they can be rebuilt whenever the ROM is
rebuilt or a new starting situation is needed:

    ./run-tests --build-fixtures

Only situations that are slow to reach get stored. Anything cheap to derive --
being *in* a battle, having balls in the bag -- is set up at test time instead,
which keeps the fixture set small and its contents obvious. Both of those have
harness helpers: `give_balls()` writes them into the bag, `into_wild_battle()`
walks the grass until one starts.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from .harness import DEFAULT_ROM, DEFAULT_SOURCE, save_fixture

# What each fixture is for, so a future reader knows why it exists.
FIXTURES = {
    "grass_cyndaquil": "Route 29 grass, Quilava ~Lv14 knowing EMBER (40) and "
                       "TACKLE (35) -- move ranking has an unambiguous winner",
    "pre_learn_chikorita": "Chikorita Lv14 with all four move slots full, one "
                           "level below POISONPOWDER -- the learn-move prompt",
    "route30": "standing on Route 30, which has trainers and a story-gated one",
}


def _pilot(rom: Path, log=print):
    from pilot.pilot import Pilot
    return Pilot(rom=rom, source=DEFAULT_SOURCE, window="null", speed=0,
                 timeout_seconds=900, log=log)


def _fresh_rom(tmp: Path, tag: str) -> Path:
    """A private ROM+sym copy, so building never touches the real save."""
    dest = tmp / tag
    dest.mkdir(parents=True, exist_ok=True)
    rom = dest / DEFAULT_ROM.name
    shutil.copy2(DEFAULT_ROM, rom)
    shutil.copy2(DEFAULT_ROM.with_suffix(".sym"), rom.with_suffix(".sym"))
    return rom


def build(names: list[str] | None = None, log=print) -> int:
    wanted = set(names or FIXTURES)
    unknown = wanted - set(FIXTURES)
    if unknown:
        log(f"unknown fixture(s): {', '.join(sorted(unknown))}")
        log(f"known: {', '.join(FIXTURES)}")
        return 2

    with tempfile.TemporaryDirectory(prefix="crystal-pilot-fixtures-") as tmpdir:
        tmp = Path(tmpdir)
        t0 = time.monotonic()

        if "grass_cyndaquil" in wanted or "route30" in wanted:
            log("building: grass_cyndaquil (new game -> Route 29 -> Lv14)")
            rom = _fresh_rom(tmp, "cyndaquil")
            p = _pilot(rom, log=lambda *a, **k: None)
            p.bootstrap(starter="cyndaquil")
            res = p.grind(species="cyndaquil", to_level=14, save_when_done=False)
            if not res.ok:
                log(f"  ! grind did not reach Lv14: {res.message}")
            # Heal before snapshotting: a grind ends wherever PP happened to
            # run out, and a fixture whose best move has 0 PP silently makes
            # the move-ranking tests meaningless.
            p.traveler.heal_round_trip()
            p.nav.find_grass()
            mon = p.reader.mon(0)
            log(f"  lead: {mon.describe(p.gamedata)}")
            if "grass_cyndaquil" in wanted:
                path = save_fixture("grass_cyndaquil", p.session.snapshot())
                log(f"  wrote {path.name} ({path.stat().st_size / 1024:.0f} KB)")

            if "route30" in wanted:
                log("building: route30 (travel Route 29 -> Route 30)")
                if p.traveler.travel_to("ROUTE_30"):
                    path = save_fixture("route30", p.session.snapshot())
                    log(f"  wrote {path.name} ({path.stat().st_size / 1024:.0f} KB)")
                else:
                    log("  ! could not reach Route 30; fixture not written")
            p.stop(save_sram=False)

        if "pre_learn_chikorita" in wanted:
            log("building: pre_learn_chikorita (new game -> Chikorita Lv14)")
            rom = _fresh_rom(tmp, "chikorita")
            p = _pilot(rom, log=lambda *a, **k: None)
            p.bootstrap(starter="chikorita")
            # Lv14: RAZOR_LEAF and REFLECT have filled the empty slots, and
            # POISONPOWDER at Lv15 is the first move that needs one replaced.
            res = p.grind(species="chikorita", to_level=14, save_when_done=False)
            if not res.ok:
                log(f"  ! grind did not reach Lv14: {res.message}")
            p.traveler.heal_round_trip()
            p.nav.find_grass()
            mon = p.reader.mon(0)
            log(f"  lead: {mon.describe(p.gamedata)}")
            filled = sum(1 for m in mon.moves if m)
            if filled < 4:
                log(f"  ! only {filled} move slots filled; the learn-move "
                    f"prompt needs all four")
            path = save_fixture("pre_learn_chikorita", p.session.snapshot())
            log(f"  wrote {path.name} ({path.stat().st_size / 1024:.0f} KB)")
            p.stop(save_sram=False)

    log(f"\nfixtures built in {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(build(sys.argv[1:] or None))
