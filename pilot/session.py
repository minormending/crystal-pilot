"""Emulator session: owns PyBoy, the symbol table, hooks and the input model.

The pilot is event-driven. Rather than inferring game state from pixels, it
registers hooks on the routines the game itself calls when it wants input
(`BattleMenu`, `WaitButton`, `YesNoBox`, ...). Hook callbacks run inside CPU
emulation, so they only record events; the control loop drains them between
ticks and decides what to press.
"""
from __future__ import annotations

import io
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from pyboy import PyBoy

from . import symbols as S
from .symbols import HOOK_ROUTINES, SymbolTable

BUTTONS = ("a", "b", "start", "select", "up", "down", "left", "right")


class PilotTimeout(RuntimeError):
    """The task exceeded its frame or wall-clock budget."""


class PilotStuck(RuntimeError):
    """The game stopped responding the way the task expected."""


@dataclass
class Budget:
    """Bounds a task in both emulated frames and real seconds."""

    max_frames: int
    max_wall_seconds: float
    _frames_used: int = 0
    _t0: float = field(default_factory=time.monotonic)

    def charge(self, frames: int) -> None:
        self._frames_used += frames
        if self._frames_used > self.max_frames:
            raise PilotTimeout(
                f"frame budget exhausted ({self._frames_used:,} > {self.max_frames:,} frames "
                f"~= {self.max_frames / 60 / 60:.1f} in-game hours)"
            )
        if time.monotonic() - self._t0 > self.max_wall_seconds:
            raise PilotTimeout(
                f"wall-clock budget exhausted ({self.max_wall_seconds:.0f}s)"
            )

    @property
    def frames_used(self) -> int:
        return self._frames_used

    @property
    def wall_elapsed(self) -> float:
        return time.monotonic() - self._t0

    def remaining_frames(self) -> int:
        return max(0, self.max_frames - self._frames_used)

    def open_reserve(self, frames: int = 120_000, seconds: float = 120.0) -> None:
        """Extend the budget for teardown after a timeout.

        Once the budget trips, every further tick raises -- which would stop the
        pilot from doing the one thing it still must do: leave the game in a
        clean state and save. This grants a bounded extra allowance for that.
        """
        self.max_frames = self._frames_used + frames
        self.max_wall_seconds = (time.monotonic() - self._t0) + seconds


class Session:
    def __init__(
        self,
        rom: str | Path,
        sym: str | Path | None = None,
        sav: str | Path | None = None,
        window: str = "null",
        speed: int = 0,
        budget: Budget | None = None,
    ):
        self.rom = Path(rom)
        if not self.rom.exists():
            raise FileNotFoundError(f"ROM not found: {self.rom}")
        sym_path = Path(sym) if sym else self.rom.with_suffix(".sym")
        self.sym = SymbolTable(sym_path)
        self.sym.require(*HOOK_ROUTINES.values())

        self.budget = budget or Budget(max_frames=60 * 60 * 60 * 6, max_wall_seconds=1800)
        self._events: deque[tuple[int, str]] = deque(maxlen=4096)
        self._frame = 0
        self._render = window != "null"

        # Battery SRAM is kept in a conventional .sav next to the ROM instead of
        # PyBoy's default <rom>.gbc.ram, so the same file works in other
        # emulators and on hardware.
        self._recorder = None
        self._checkpoints = None
        self._sav_path = Path(sav) if sav else self.rom.with_suffix(".sav")
        ram_handle = None
        if self._sav_path.exists() and self._sav_path.stat().st_size > 0:
            ram_handle = self._sav_path.open("rb")
        try:
            # PyBoy loads <rom>.sym automatically when present, which is what
            # makes symbol-name hook registration work.
            self.pyboy = PyBoy(
                str(self.rom),
                window=window,
                sound_emulated=False,
                symbols=str(sym_path),
                ram_file=ram_handle,
                # PyBoy warns once per .sym line that is not an address, and
                # pokecrystal's symbol file has hundreds of scene constants.
                # It is harmless, and 300+ lines drown out anything the pilot
                # says. PyBoy applies this itself at construction, so setting
                # the logger level from outside gets overwritten.
                log_level="ERROR",
            )
        finally:
            if ram_handle is not None:
                ram_handle.close()
        self.loaded_sav = ram_handle is not None
        if speed is not None:
            self.pyboy.set_emulation_speed(speed)
        self._hook_ids: dict[str, str] = {}
        self._register_hooks()

    # --- hooks / events ----------------------------------------------------
    def _register_hooks(self) -> None:
        high = []
        for key, routine in HOOK_ROUTINES.items():
            bank, _addr = self.sym.banked(routine)
            if bank > S.MAX_HOOKABLE_BANK:
                high.append(f"{routine} (bank {bank:#x})")
            self.pyboy.hook_register(None, routine, self._make_cb(key), None)
            self._hook_ids[key] = routine
        if high:
            raise RuntimeError(
                "these hook routines live in ROM banks where PyBoy hooks never "
                "fire, so they would silently do nothing: " + ", ".join(high)
            )

    def _make_cb(self, key: str):
        def cb(_ctx):
            self._events.append((self._frame, key))

        return cb

    def drain_events(self) -> list[str]:
        ev = [name for _f, name in self._events]
        self._events.clear()
        return ev

    def clear_events(self) -> None:
        self._events.clear()

    def has_event(self, *names: str) -> bool:
        return any(n in names for _f, n in self._events)

    def set_render(self, on: bool) -> None:
        """Turn per-frame rendering on or off.

        With a window open, drawing every frame throttles the emulator to a few
        hundred fps -- fine for playing, far too slow for a task that needs
        hundreds of thousands of frames.
        """
        self._render = on

    @property
    def rendering(self) -> bool:
        return self._render

    def set_budget(self, budget: Budget) -> None:
        """Swap in a fresh budget (each dispatched task gets its own)."""
        self.budget = budget

    # --- time --------------------------------------------------------------
    @property
    def frame(self) -> int:
        return self._frame

    def tick(self, n: int = 1, render: bool | None = None) -> None:
        r = self._render if render is None else render
        self.budget.charge(n)
        rec, cps = self._recorder, self._checkpoints
        if rec is None and cps is None:
            self.pyboy.tick(n, r)
            self._frame += n
            return
        # While recording or checkpointing, step frame by frame so only the
        # sampled frames are rendered. Rendering costs ~4x, so drawing one frame
        # in sixty keeps the pilot at hundreds of times real time instead of
        # dropping to ~200x.
        for _ in range(n):
            shoot = rec is not None and rec.should_capture(self._frame)
            self.pyboy.tick(1, r or shoot)
            self._frame += 1
            if shoot:
                rec.capture(self.pyboy)
            if cps is not None and cps.should_capture(self._frame):
                cps.capture(self, self._frame)

    def attach_recorder(self, recorder) -> None:
        self._recorder = recorder

    def detach_recorder(self):
        rec, self._recorder = self._recorder, None
        return rec

    @property
    def recorder(self):
        return self._recorder

    def attach_checkpoints(self, writer) -> None:
        self._checkpoints = writer

    def detach_checkpoints(self):
        cps, self._checkpoints = self._checkpoints, None
        return cps

    @property
    def checkpoints(self):
        return self._checkpoints

    def wait_event(self, *names: str, timeout: int = 600) -> str | None:
        """Tick until one of `names` fires, ignoring anything already queued."""
        self.clear_events()
        return self._await(names, timeout)

    def await_event(self, *names: str, timeout: int = 600) -> str | None:
        """Like wait_event but honours events that fired *before* the call.

        Hooks are edge-triggered, so an event raised during the button press
        that caused it is already in the queue by the time we look. Clearing
        first (as wait_event does) would silently drop it and then wait forever
        for a repeat that never comes.
        """
        return self._await(names, timeout)

    def _await(self, names, timeout: int) -> str | None:
        for _f, n in list(self._events):
            if n in names:
                self.clear_events()
                return n
        waited = 0
        while waited < timeout:
            self.tick(1)
            waited += 1
            for _f, n in list(self._events):
                if n in names:
                    self.clear_events()
                    return n
        return None

    def world_loaded(self) -> bool:
        """Is a map actually loaded and being handled?

        Party data and coordinates are restored *before* the map is, so the
        CONTINUE screen already reports a full party at real coordinates. Only
        wMapStatus reaching MAPSTATUS_HANDLE (with a map size published) means
        the world is live and safe to drive.
        """
        return (self.rb("wMapStatus") == S.MAPSTATUS_HANDLE
                and self.rb("wMapWidth") > 0
                and self.rb("wMapGroup") != 0)

    def await_world(self, timeout: int = 6000, tap_a_every: int = 0) -> bool:
        """Tick (optionally tapping A) until a map is loaded and live."""
        waited = 0
        while waited < timeout:
            if tap_a_every and waited >= tap_a_every:
                self.tap("a", hold=5, gap=8)
                waited = 0
            else:
                self.tick(4)
                waited += 4
            if self.world_loaded():
                self.tick(30)
                return True
        return False

    def wait_frames(self, n: int) -> None:
        self.tick(n)

    # --- input -------------------------------------------------------------
    def tap(self, button: str, hold: int = 6, gap: int = 6) -> None:
        """Press and release, with enough frames either side for the game to poll."""
        if button not in BUTTONS:
            raise ValueError(f"unknown button {button!r}")
        self.pyboy.button_press(button)
        self.tick(hold)
        self.pyboy.button_release(button)
        self.tick(gap)

    def repeat(self, button: str, times: int, hold: int = 6, gap: int = 6) -> None:
        for _ in range(times):
            self.tap(button, hold=hold, gap=gap)

    # --- memory ------------------------------------------------------------
    # 0xD000-0xDFFF is *switchable* WRAM on CGB and Crystal genuinely switches
    # it (SVBK 1, 5 and 6 all occur during normal play), so unbanked reads of
    # that window return another bank's bytes for a good fraction of frames.
    # Every access is therefore bank-qualified. 0xC000-0xCFFF is fixed WRAM and
    # HRAM is unbanked, so those are read directly.
    WRAM_SWITCHABLE = range(0xD000, 0xE000)
    DEFAULT_WRAM_BANK = 1

    def _resolve(self, where: str | int) -> tuple[int | None, int]:
        """-> (wram_bank_or_None, address)."""
        if isinstance(where, str):
            bank, addr = self.sym.banked(where)
        else:
            addr = where
            bank = self.DEFAULT_WRAM_BANK
        if addr in self.WRAM_SWITCHABLE:
            return (bank or self.DEFAULT_WRAM_BANK), addr
        return None, addr

    def _read(self, where: str | int) -> int:
        bank, addr = self._resolve(where)
        mem = self.pyboy.memory
        return mem[bank, addr] if bank is not None else mem[addr]

    def rb(self, where: str | int) -> int:
        """Read one byte, bank-qualified."""
        return self._read(where)

    def rw(self, where: str | int) -> int:
        """Read a big-endian 16-bit value (Game Boy Pokemon stat convention)."""
        bank, addr = self._resolve(where)
        mem = self.pyboy.memory
        if bank is not None:
            return (mem[bank, addr] << 8) | mem[bank, addr + 1]
        return (mem[addr] << 8) | mem[addr + 1]

    def rw_le(self, where: str | int) -> int:
        """Read a little-endian 16-bit value (Game Boy *pointers* are LE, unlike
        Pokemon's big-endian stats)."""
        bank, addr = self._resolve(where)
        mem = self.pyboy.memory
        if bank is not None:
            return mem[bank, addr] | (mem[bank, addr + 1] << 8)
        return mem[addr] | (mem[addr + 1] << 8)

    def read_rom(self, bank: int, addr: int) -> int:
        """Read a byte from a ROM bank. (Not `rom` -- that is the ROM path.)"""
        return self.pyboy.memory[bank, addr]

    def rbytes(self, where: str | int, n: int) -> list[int]:
        bank, addr = self._resolve(where)
        mem = self.pyboy.memory
        if bank is not None:
            return [mem[bank, addr + i] for i in range(n)]
        return [mem[addr + i] for i in range(n)]

    def wb(self, where: str | int, value: int) -> None:
        bank, addr = self._resolve(where)
        if bank is not None:
            self.pyboy.memory[bank, addr] = value & 0xFF
        else:
            self.pyboy.memory[addr] = value & 0xFF

    def addr_of(self, symbol: str) -> int:
        return self.sym.addr(symbol)

    @property
    def wram_bank(self) -> int:
        return self.pyboy.memory[0xFF70] & 0x07

    # --- save states / SRAM ------------------------------------------------
    def snapshot(self) -> bytes:
        buf = io.BytesIO()
        self.pyboy.save_state(buf)
        return buf.getvalue()

    def restore(self, blob: bytes) -> None:
        self.pyboy.load_state(io.BytesIO(blob))
        self.clear_events()

    def save_state_to(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            self.pyboy.save_state(fh)
        return path

    def load_state_from(self, path: str | Path) -> None:
        with Path(path).open("rb") as fh:
            self.pyboy.load_state(fh)
        self.clear_events()

    def screenshot(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.pyboy.tick(1, True)
        self._frame += 1
        self.pyboy.screen.image.save(str(path))
        return path

    def flush_recorder(self) -> dict | None:
        rec = self.detach_recorder()
        return rec.close() if rec is not None else None

    # --- battery SRAM ------------------------------------------------------
    SRAM_BANK_SIZE = 0x2000        # 8 KiB per cartridge RAM bank
    SRAM_BASE = 0xA000             # where cartridge RAM is mapped

    @property
    def sav_path(self) -> Path:
        return self._sav_path

    # Cartridge header RAM-size byte (0x0149) -> number of 8 KiB banks.
    # Probing for readable banks over-reports (PyBoy does not bounds-check), and
    # writing the wrong size produces a .sav other emulators reject -- Crystal
    # declares 0x03, i.e. exactly 32 KiB.
    RAM_SIZE_BANKS: ClassVar[dict[int, int]] = {
        0x00: 0, 0x01: 1, 0x02: 1, 0x03: 4, 0x04: 16, 0x05: 8,
    }

    def _sram_banks(self) -> int:
        code = self.pyboy.memory[0x0149]
        banks = self.RAM_SIZE_BANKS.get(code)
        if banks is None:
            banks = 4
        return banks

    def flush_sram(self, path: str | Path | None = None) -> Path | None:
        """Write cartridge SRAM to the .sav without ending the session.

        PyBoy does not expose the cartridge object, but cartridge RAM is mapped
        at 0xA000-0xBFFF and is bank-switched, so the save can be read straight
        out of the banked memory view. Call this after the game commits an
        in-game save so the .sav on disk matches what the game just wrote.
        """
        banks = self._sram_banks()
        if banks == 0:
            return None
        mem = self.pyboy.memory
        target = Path(path) if path else self._sav_path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = bytearray()
        for bank in range(banks):
            for off in range(self.SRAM_BANK_SIZE):
                data.append(mem[bank, self.SRAM_BASE + off] & 0xFF)
        target.write_bytes(bytes(data))
        return target

    def stop(self, save_sram: bool = True) -> None:
        if save_sram:
            try:
                self.flush_sram()
            except Exception as e:  # noqa: BLE001 -- teardown must never mask a task result
                print(f"warning: could not write {self._sav_path}: {e}")
        self.pyboy.stop(save=False)
