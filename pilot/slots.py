"""Save slots you pick, and the one the pilot keeps for you.

Three numbered slots plus `undo`, each holding a *machine save state* and the
`.sav` beside it. PyBoy can put a state back, so a slot here is an exact moment
rather than a save point: it can be taken mid-battle, and loading it returns you
to that frame. The mobile port cannot do this -- its emulator will capture a
state and refuses to restore one -- so slots there hold battery saves and behave
differently. Worth knowing if you use both.

The `undo` slot is written before every pilot job, which is what makes a job
safe to try: if you dislike what it did, `undo` puts the game back to the
instant before it started. It is deliberately a different thing from the
per-task backup in `backup.py`. A backup is insurance kept forever against
losing a game; the undo slot is a single step backwards that the next job
overwrites without ceremony.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

SLOT_IDS = ("1", "2", "3")
UNDO_SLOT = "undo"
ALL_SLOTS = (*SLOT_IDS, UNDO_SLOT)


@dataclass
class SlotInfo:
    """What a slot holds, without loading the state itself."""

    slot: str
    when: float
    where: str = ""
    lead: str = ""
    party: int = 0
    job: str = ""

    @property
    def stamp(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.when))

    def describe(self) -> str:
        bits = [b for b in (self.where, self.lead) if b]
        if self.job:
            bits.append(f"before {self.job}")
        return f"{self.stamp}  {' · '.join(bits)}" if bits else self.stamp


class Slots:
    """Reads and writes the slot directory."""

    def __init__(self, directory: str | Path, log=print):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = log

    # --- paths ------------------------------------------------------------
    def _state(self, slot: str) -> Path:
        return self.dir / f"{slot}.state"

    def _sav(self, slot: str) -> Path:
        return self.dir / f"{slot}.sav"

    def _meta(self, slot: str) -> Path:
        return self.dir / f"{slot}.json"

    @staticmethod
    def check(slot: str) -> str:
        s = str(slot)
        if s not in ALL_SLOTS:
            raise ValueError(f"no slot {slot!r}: pick one of "
                             f"{', '.join(ALL_SLOTS)}")
        return s

    # --- writing ----------------------------------------------------------
    def save(self, slot: str, session, reader=None, job: str = "",
             describe=None) -> SlotInfo:
        """Snapshot the machine into a slot, with the .sav beside it.

        Both, because they answer different questions later: the state is the
        exact moment, and the `.sav` is what another emulator could open if the
        state ever turns out to be unreadable.
        """
        slot = self.check(slot)
        session.save_state_to(self._state(slot))
        try:
            session.flush_sram(self._sav(slot))
        except Exception as e:                        # noqa: BLE001
            # A missing .sav is a smaller loss than a missing slot, so this is
            # noted rather than fatal -- the state alone still restores.
            self.log(f"slot {slot}: could not write the .sav ({e})")
        info = SlotInfo(slot=slot, when=time.time(), job=job)
        if describe is not None:
            where, lead, party = describe()
            info.where, info.lead, info.party = where, lead, party
        self._meta(slot).write_text(json.dumps({
            "when": info.when, "where": info.where, "lead": info.lead,
            "party": info.party, "job": info.job,
        }, indent=2) + "\n")
        return info

    # --- reading ----------------------------------------------------------
    def info(self, slot: str) -> SlotInfo | None:
        slot = self.check(slot)
        if not self._state(slot).exists():
            return None
        meta = {}
        if self._meta(slot).exists():
            try:
                meta = json.loads(self._meta(slot).read_text())
            except json.JSONDecodeError:
                meta = {}
        return SlotInfo(
            slot=slot,
            when=float(meta.get("when", self._state(slot).stat().st_mtime)),
            where=meta.get("where", ""), lead=meta.get("lead", ""),
            party=int(meta.get("party", 0) or 0), job=meta.get("job", ""),
        )

    def list(self) -> dict[str, SlotInfo | None]:
        return {s: self.info(s) for s in ALL_SLOTS}

    def load(self, slot: str, session) -> bool:
        """Put a slot back. False if it is empty.

        The `.sav` is written out too, so the battery on disk matches the
        machine that was just restored -- otherwise the next in-game save would
        be layered on top of a different game's battery.
        """
        slot = self.check(slot)
        state = self._state(slot)
        if not state.exists():
            return False
        session.load_state_from(state)
        session.flush_sram()
        return True

    def clear(self, slot: str) -> bool:
        slot = self.check(slot)
        gone = False
        for p in (self._state(slot), self._sav(slot), self._meta(slot)):
            if p.exists():
                p.unlink()
                gone = True
        return gone


def describer(reader, gamedata):
    """A callable giving (where, lead, party) for slot metadata.

    Kept out of Slots so that module needs nothing but a session -- and so the
    naming matches whatever the reader already calls things.
    """
    def describe():
        try:
            loc = reader.location()
            # The same namer the tasks use, so a slot says "Route 29" rather
            # than the map numbers it is stored as.
            where = gamedata.map_pretty(*loc.key)
            count = reader.party_count()
            lead = ""
            if count:
                mon = reader.mon(0)
                lead = f"{mon.species_name} Lv{mon.level}"
            return where, lead, count
        except Exception:                              # noqa: BLE001
            return "", "", 0
    return describe
