"""A Game Boy that is not there.

CI has the pokecrystal disassembly but no ROM: building one needs rgbds, and no
ROM is distributed. So 88 of the 108 tests skipped themselves there, and the
badge covered 18% of the suite -- the 18% that reads data files, not the part
that decides anything.

The mobile port hit the same wall and went round it. The bugs either half has
shipped were decisions made about a game state: which move to pick, whether a
battle ended in a knockout or a getaway, whether the game is settled enough to
save. None of that needs a cartridge. It needs a plausible work-RAM snapshot and
a way to watch what the code does with it.

That is what this is. The session surface the readers and controllers actually
use is small -- rb/rbytes/rw for reading, tick/tap/repeat for driving, and the
event queue -- so a stand-in is a couple of hundred lines rather than a
simulator. Anything outside that surface raises instead of quietly returning
None, because a fake that answers questions it does not understand is worse than
no fake at all.

The symbol table is the *real* SymbolTable over a generated .sym, so the parser
is under test too, and addresses are synthetic on purpose: state.py's job is to
read whatever address the table gives it, and pinning tests to one build's
addresses would test the build and rot the day the ROM is rebuilt.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from pilot.symbols import SymbolTable

WRAM_START = 0xC000
WRAM_BYTES = 0x2000
SRAM_BYTES = 32768
PARTY_STRUCT = 0x30

# Offsets inside a party entry, from the disassembly.
MON_SPECIES, MON_MOVES, MON_PP = 0x00, 0x02, 0x17
MON_LEVEL, MON_STATUS, MON_HP, MON_MAXHP = 0x1F, 0x20, 0x22, 0x24

# Every name the readers resolve, with how many bytes it needs. Laid out in
# order from a base address; see the module docstring on why not real ones.
_LAYOUT = [
    ("wPartyCount", 1), ("wPartySpecies", 7), ("wPartyMon1", 6 * PARTY_STRUCT),
    ("wPartyMon1Nickname", 11),
    ("wBattleMode", 1), ("wBattleType", 1), ("wOtherTrainerClass", 1),
    ("wMapGroup", 1), ("wMapNumber", 1), ("wMapStatus", 1), ("wScriptMode", 1),
    ("wXCoord", 1), ("wYCoord", 1), ("wPlayerTileCollision", 1),
    ("wPlayerDirection", 1), ("wPlayerStandingMapX", 1), ("wPlayerStandingMapY", 1),
    ("wMenuCursorX", 1), ("wMenuCursorY", 1), ("wBattleMenuCursorPosition", 1),
    ("wMenuDataItems", 1), ("wMenuBorderTopCoord", 1), ("wMenuBorderRightCoord", 1),
    ("wEnemyMonSpecies", 1), ("wEnemyMonLevel", 1), ("wEnemyMonHP", 2),
    ("wEnemyMonMaxHP", 2), ("wEnemySubStatus5", 1),
    ("wBattleMonSpecies", 1), ("wBattleMonLevel", 1),
    ("wBattleMonHP", 2), ("wBattleMonMaxHP", 2),
    ("wBattleMonMoves", 4), ("wBattleMonPP", 4),
    ("wNumItems", 1), ("wItems", 40), ("wNumBalls", 1), ("wBalls", 40),
    ("wCurPocket", 1), ("wCurItem", 1), ("wCurItemQuantity", 1),
    ("wWindowStackSize", 1), ("wTimeOfDay", 1), ("wCurSpecies", 1),
    ("wOverworldMapBlocks", 1300), ("wMapWidth", 1), ("wMapHeight", 1),
    ("wTilesetCollisionAddress", 2), ("wTilesetCollisionBank", 1),
    ("wCurPartyMon", 1), ("wCurMoveNum", 1), ("wEnemyMonDVs", 2),
    ("wMenuBorderBottomCoord", 1), ("wMenuBorderLeftCoord", 1),
    ("wPlayerName", 11), ("wPartyMonNicknames", 11 * 6),
    # 16 objects of 16 bytes: the overworld sprites, which collision.py reads
    # to find out which tiles are occupied.
    ("wObjectStructs", 16 * 16), ("wObject1Struct", 16),
]

# Save-validity markers live in SRAM bank 1, a different space from work RAM.
_SRAM = [("sCheckValue1", 1, 0xA008), ("sCheckValue2", 1, 0xAD0F)]


def _sym_text() -> str:
    lines, at = [], WRAM_START + 0x100
    for name, size in _LAYOUT:
        lines.append(f"00:{at:04x} {name}")
        at += size
    for name, _size, addr in _SRAM:
        lines.append(f"01:{addr:04x} {name}")
    # A ROM symbol or two, for the code that reads the cartridge's own tables.
    lines.append("10:5afb Moves")
    return "\n".join(lines) + "\n"


def fake_symbols(directory: Path | None = None) -> SymbolTable:
    """The real parser over a generated .sym."""
    d = Path(directory or tempfile.mkdtemp(prefix="crystal-pilot-fakesym-"))
    d.mkdir(parents=True, exist_ok=True)
    path = d / "fake.sym"
    path.write_text(_sym_text())
    return SymbolTable(path)


class FakeBudget:
    """Permissive: budget exhaustion has its own tests against a real session."""

    def __init__(self) -> None:
        self.frames = 0
        self.reserve_opened = False

    def charge(self, frames: int) -> None:
        self.frames += frames

    def frames_used(self) -> int:
        return self.frames

    def wall_elapsed(self) -> float:
        return 0.0

    def remaining_frames(self) -> int:
        return 10 ** 9

    def open_reserve(self) -> None:
        self.reserve_opened = True


class FakeSession:
    """Stands in for Session: memory to read, buttons to press, events to fire.

    `on_tap` and `on_tick` are where a test says what the game does in response,
    which is the whole point -- without them the machine is inert, which is
    right for testing a refusal and useless for testing a sequence.
    """

    def __init__(self, sym: SymbolTable, wram: bytearray | None = None,
                 on_tap=None, on_tick=None):
        self.sym = sym
        self.wram = wram if wram is not None else bytearray(WRAM_BYTES)
        self.sram = bytearray(SRAM_BYTES)
        self.budget = FakeBudget()
        self.frame = 0
        self.presses: list[str] = []
        self._events: list[str] = []
        self.on_tap = on_tap
        self.on_tick = on_tick
        self.stopped = False

    # --- reading ----------------------------------------------------------
    # `where` is a symbol name or an address, matching the real session: the
    # callers say rb("wPartyCount"), and resolving that here rather than at
    # every call site is most of what a session is for.
    def _at(self, where: str | int) -> int:
        addr = self.sym.addr(where) if isinstance(where, str) else where
        i = addr - WRAM_START
        if not 0 <= i < len(self.wram):
            raise AssertionError(
                f"read outside the fake's work RAM: {where!r} -> {addr:#06x}. "
                f"Either the layout in tests/fake.py is missing a symbol, or "
                f"the code under test is reading somewhere unexpected."
            )
        return i

    def rb(self, where: str | int) -> int:
        return self.wram[self._at(where)]

    def rbytes(self, where: str | int, n: int) -> list[int]:
        """A list of ints, as the real session returns."""
        i = self._at(where)
        self._at((self.sym.addr(where) if isinstance(where, str) else where) + n - 1)
        return list(self.wram[i:i + n])

    def rw(self, where: str | int) -> int:
        """Big-endian, the convention Gen 2 uses for HP and stats."""
        i = self._at(where)
        return (self.wram[i] << 8) | self.wram[i + 1]

    def rw_le(self, where: str | int) -> int:
        """Little-endian, which is what Game Boy *pointers* are."""
        i = self._at(where)
        return self.wram[i] | (self.wram[i + 1] << 8)

    def wb(self, where: str | int, value: int) -> None:
        self.wram[self._at(where)] = value & 0xFF

    def addr_of(self, symbol: str) -> int:
        return self.sym.addr(symbol)

    # --- driving ----------------------------------------------------------
    def tick(self, frames: int = 1) -> None:
        self.frame += frames
        self.budget.charge(frames)
        if self.on_tick:
            self.on_tick(frames, self)

    def tap(self, button: str, hold: int = 6, gap: int = 6) -> None:
        self.presses.append(button)
        self.tick(hold + gap)
        if self.on_tap:
            self.on_tap(button, self)

    def repeat(self, button: str, times: int, hold: int = 6, gap: int = 6) -> None:
        for _ in range(times):
            self.tap(button, hold=hold, gap=gap)

    # --- events -----------------------------------------------------------
    def fire(self, *keys: str) -> None:
        """Test-side: queue events as though a ROM hook had run."""
        self._events.extend(keys)

    def clear_events(self) -> None:
        self._events.clear()

    def drain_events(self) -> list[str]:
        out = list(self._events)
        self._events.clear()
        return out

    def has_event(self, *keys: str) -> bool:
        return any(k in self._events for k in keys)

    def await_event(self, *keys: str, timeout: int = 600):
        for k in keys:
            if k in self._events:
                return k
        return None

    # --- deliberately absent ---------------------------------------------
    def __getattr__(self, name):
        raise AttributeError(
            f"FakeSession has no {name!r}. It stands in for the reading, "
            f"driving and event surface only -- anything else (save states, "
            f"SRAM flushing, recorders) needs a real session, so a test "
            f"reaching for it is testing the wrong thing here."
        )

    def count(self, button: str) -> int:
        return self.presses.count(button)


# --- describing a situation -------------------------------------------------
def _w8(wram, sym, name, value, offset=0):
    wram[sym.addr(name) - WRAM_START + offset] = value & 0xFF


def _w16(wram, sym, name, value):
    i = sym.addr(name) - WRAM_START
    wram[i] = (value >> 8) & 0xFF
    wram[i + 1] = value & 0xFF


def world(sym: SymbolTable, *, party=(), battle_mode=0, map=(24, 3), pos=(5, 5),
          map_status=2, script_mode=0, tile=0, menu=(0, 0), battle_cursor=0,
          window_stack=0, enemy=None, active=None, balls=(), cur_pocket=0,
          cur_item=0, menu_items=0, menu_top=0, menu_right=0,
          time_of_day=1) -> bytearray:
    """A work-RAM snapshot, written in the language of the game.

    So a test says "in a wild battle against a 20 HP Pidgey with the menu up"
    rather than which byte that is.
    """
    wram = bytearray(WRAM_BYTES)
    _w8(wram, sym, "wPartyCount", len(party))
    for i, mon in enumerate(party):
        base = sym.addr("wPartyMon1") - WRAM_START + i * PARTY_STRUCT
        wram[base + MON_SPECIES] = mon.get("species", 155)
        wram[base + MON_LEVEL] = mon.get("level", 5)
        hp, max_hp = mon.get("hp", 20), mon.get("max_hp", 20)
        wram[base + MON_HP], wram[base + MON_HP + 1] = (hp >> 8) & 0xFF, hp & 0xFF
        wram[base + MON_MAXHP], wram[base + MON_MAXHP + 1] = \
            (max_hp >> 8) & 0xFF, max_hp & 0xFF
        for k, m in enumerate(mon.get("moves", ())):
            wram[base + MON_MOVES + k] = m
        for k, p in enumerate(mon.get("pp", ())):
            wram[base + MON_PP + k] = p
        wram[sym.addr("wPartySpecies") - WRAM_START + i] = mon.get("species", 155)
    _w8(wram, sym, "wBattleMode", battle_mode)
    _w8(wram, sym, "wMapGroup", map[0])
    _w8(wram, sym, "wMapNumber", map[1])
    _w8(wram, sym, "wMapStatus", map_status)
    _w8(wram, sym, "wScriptMode", script_mode)
    _w8(wram, sym, "wXCoord", pos[0])
    _w8(wram, sym, "wYCoord", pos[1])
    _w8(wram, sym, "wPlayerStandingMapX", pos[0])
    _w8(wram, sym, "wPlayerStandingMapY", pos[1])
    _w8(wram, sym, "wPlayerTileCollision", tile)
    _w8(wram, sym, "wMenuCursorX", menu[0])
    _w8(wram, sym, "wMenuCursorY", menu[1])
    _w8(wram, sym, "wBattleMenuCursorPosition", battle_cursor)
    _w8(wram, sym, "wWindowStackSize", window_stack)
    _w8(wram, sym, "wMenuDataItems", menu_items)
    _w8(wram, sym, "wMenuBorderTopCoord", menu_top)
    _w8(wram, sym, "wMenuBorderRightCoord", menu_right)
    _w8(wram, sym, "wTimeOfDay", time_of_day)
    if enemy:
        _w8(wram, sym, "wEnemyMonSpecies", enemy.get("species", 16))
        _w8(wram, sym, "wEnemyMonLevel", enemy.get("level", 3))
        _w16(wram, sym, "wEnemyMonHP", enemy.get("hp", 20))
        _w16(wram, sym, "wEnemyMonMaxHP", enemy.get("max_hp", 20))
    if active:
        _w16(wram, sym, "wBattleMonHP", active.get("hp", 20))
        _w16(wram, sym, "wBattleMonMaxHP", active.get("max_hp", 20))
        _w8(wram, sym, "wBattleMonLevel", active.get("level", 5))
        for k, m in enumerate(active.get("moves", ())):
            _w8(wram, sym, "wBattleMonMoves", m, offset=k)
        for k, p in enumerate(active.get("pp", ())):
            _w8(wram, sym, "wBattleMonPP", p, offset=k)
    _w8(wram, sym, "wNumBalls", len(balls))
    for i, (ball_id, qty) in enumerate(balls):
        _w8(wram, sym, "wBalls", ball_id, offset=i * 2)
        _w8(wram, sym, "wBalls", qty, offset=i * 2 + 1)
    _w8(wram, sym, "wCurPocket", cur_pocket)
    _w8(wram, sym, "wCurItem", cur_item)
    return wram


def mark_saved(sram: bytearray, sym: SymbolTable, present: bool = True) -> bytearray:
    """Write the cartridge's own "there is a save here" markers."""
    for name, value in (("sCheckValue1", 99), ("sCheckValue2", 127)):
        bank, addr = sym.banked(name)
        sram[bank * 0x2000 + (addr - 0xA000)] = value if present else 0
    return sram
