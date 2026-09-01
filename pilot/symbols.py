"""Symbol table + struct offsets for Pokemon Crystal (pokecrystal disassembly).

Addresses are resolved from the build's .sym file rather than hardcoded, so a
rebuilt/patched ROM stays supported as long as it ships its symbol map.
"""
from __future__ import annotations

import re
from pathlib import Path

SYM_LINE = re.compile(r"^([0-9A-Fa-f]{2,3}):([0-9A-Fa-f]{4})\s+(\S+)\s*$")


class SymbolTable:
    """Maps `symbol -> (bank, address)` from a .sym file."""

    def __init__(self, sym_path: Path):
        self.path = Path(sym_path)
        self._syms: dict[str, tuple[int, int]] = {}
        if not self.path.exists():
            raise FileNotFoundError(
                f"symbol file not found: {self.path}\n"
                "The pilot needs pokecrystal.sym (built alongside the ROM) to read game state."
            )
        for line in self.path.read_text(errors="replace").splitlines():
            line = line.split(";", 1)[0]
            m = SYM_LINE.match(line)
            if m:
                bank, addr, name = m.groups()
                # First definition wins; later duplicates are aliases/locals.
                self._syms.setdefault(name, (int(bank, 16), int(addr, 16)))

    def __contains__(self, name: str) -> bool:
        return name in self._syms

    def addr(self, name: str) -> int:
        """WRAM/HRAM address of a symbol (bank ignored -- see `banked`)."""
        try:
            return self._syms[name][1]
        except KeyError:
            raise KeyError(f"symbol {name!r} not present in {self.path.name}") from None

    def banked(self, name: str) -> tuple[int, int]:
        try:
            return self._syms[name]
        except KeyError:
            raise KeyError(f"symbol {name!r} not present in {self.path.name}") from None

    def require(self, *names: str) -> None:
        """Fail fast at startup rather than mid-task on a symbol we depend on."""
        missing = [n for n in names if n not in self._syms]
        if missing:
            raise KeyError(
                "symbol file is missing required symbols: "
                + ", ".join(missing)
                + f"\n({self.path} may be from a different build)"
            )

    def __len__(self) -> int:
        return len(self._syms)


# --- party / box mon struct -------------------------------------------------
# Verified against pokecrystal.sym: wPartyMon2 - wPartyMon1 == 0x30.
PARTY_STRUCT_LEN = 0x30
MON_SPECIES = 0x00
MON_ITEM = 0x01
MON_MOVES = 0x02      # 4 bytes
MON_EXP = 0x08        # 3 bytes, big-endian
MON_PP = 0x17         # 4 bytes
MON_HAPPINESS = 0x1B
MON_LEVEL = 0x1F
MON_STATUS = 0x20
MON_HP = 0x22         # 2 bytes, big-endian
MON_MAXHP = 0x24
MON_ATTACK = 0x26
MON_DEFENSE = 0x28
MON_SPEED = 0x2A
MON_SPCL_ATK = 0x2C
MON_SPCL_DEF = 0x2E

NUM_MOVES = 4
MAX_PARTY = 6

# --- wBattleMode values ----------------------------------------------------
BATTLE_NONE = 0
BATTLE_WILD = 1
BATTLE_TRAINER = 2

# --- status condition bitmasks (MON_STATUS) --------------------------------
STATUS_BITS = {
    "SLP": 0b00000111,   # sleep counter, non-zero == asleep
    "PSN": 1 << 3,
    "BRN": 1 << 4,
    "FRZ": 1 << 5,
    "PAR": 1 << 6,
}

# --- shininess (engine/gfx/color.asm CheckShininess) -----------------------
# DVs are two bytes: (Atk << 4 | Def), (Spd << 4 | Spc). A mon is shiny when the
# Attack DV has bit 1 set and Def, Spd and Spc are all exactly 10.
SHINY_ATK_MASK = 0b0010
SHINY_DEF_DV = SHINY_SPD_DV = SHINY_SPC_DV = 10


def dvs_are_shiny(dv_hi: int, dv_lo: int) -> bool:
    atk, dfn = dv_hi >> 4, dv_hi & 0x0F
    spd, spc = dv_lo >> 4, dv_lo & 0x0F
    return bool(atk & SHINY_ATK_MASK) and (dfn, spd, spc) == (
        SHINY_DEF_DV, SHINY_SPD_DV, SHINY_SPC_DV)


# --- tile collision values that roll for wild encounters -------------------
# constants/collision_constants.asm: COLL_LONG_GRASS $14, COLL_TALL_GRASS $18
# (plus the two unused mirrors the engine still treats as grass).
GRASS_COLLISION = frozenset({0x10, 0x14, 0x18, 0x1C})

# --- map status (constants/ram_constants.asm) ------------------------------
MAPSTATUS_START, MAPSTATUS_ENTER, MAPSTATUS_HANDLE, MAPSTATUS_DONE = 0, 1, 2, 3

# --- routines the pilot hooks to learn what the game is asking for ---------
# Each is resolved by name; a missing one is reported at startup.
#
# NOTE: PyBoy's hooks only fire for routines in the low ROM banks -- every one
# below sits in bank 0x10 or lower and is verified to fire. Routines in high
# banks (DoPlayerMovement at 20:4000, OverworldLoop at 25:66b0) register without
# error but never trigger, so they cannot be used as signals. Session checks the
# bank of each hook at startup so a future addition fails loudly instead of
# quietly never firing.
MAX_HOOKABLE_BANK = 0x10
HOOK_ROUTINES = {
    "battle_menu": "BattleMenu",
    "move_select": "MoveSelectionScreen",
    "text_wait": "WaitButton",
    "text_prompt": "PromptButton",
    "text_aorb": "WaitPressAorB_BlinkCursor",
    "yes_no": "YesNoBox",
    "learn_move": "LearnMove",
    "evolve": "EvolveAfterBattle",
    # The in-game save goes SaveMenu -> AskOverwriteSaveFile ->
    # SaveTheGame_yesorno -> _SaveGameData -> SavedTheGame. Note the confirm is
    # NOT a YesNoBox, and the `SaveGameData` symbol is a different wrapper that
    # a normal overworld save never reaches.
    "save_menu": "SaveMenu",
    "save_confirm": "SaveTheGame_yesorno",
    "save_write": "_SaveGameData",
    "saved_ok": "SavedTheGame",
    "try_run": "TryToRunAwayFromBattle",
    # Trainer battles cannot be fled, so these are the signals that matter when
    # one goes badly: our mon fainted, the game wants a replacement, we lost.
    "mon_fainted": "HandlePlayerMonFaint",
    "choose_mon": "ForcePlayerMonChoice",
    "lost_battle": "LostBattle",
    # Naming. Every one of these prompts is A-confirmable, which is precisely
    # the problem: mashing A through them names the player AAAAA and gives
    # every catch a nickname typed the same way. Each hook fires just *before*
    # its prompt, which is the only moment there is to decide differently.
    "name_player": "NamePlayer",              # the NAME menu in the intro
    "give_poke": "GivePoke",                  # the starter, and any gift mon
    "ball_nickname": "PokeBallEffect.SkipPartyMonFriendBall",
    "ball_nickname_box": "PokeBallEffect.SkipBoxMonFriendBall",
    # Not acted on, but worth knowing about: reaching the letter grid at all
    # means a prompt was answered the wrong way.
    "naming_screen": "NamingScreen",
}

# The player-name menu, from ChrisNameMenuHeader in data/player_names.asm:
# five items (NEW NAME plus four presets) drawn in the top-left ten columns.
# Worth matching on rather than trusting the cursor, because wMenuCursorY holds
# whatever the previous menu left there until this one is actually drawn.
NAME_MENU_ITEMS = 5
NAME_MENU_RIGHT = 10
# Cursor 1 is NEW NAME, which opens the letter grid. 2 and below are the names
# the game ships: CHRIS/MAT/ALLAN/JON, or KRIS/AMANDA/JUANA/JODI.
NAME_MENU_FIRST_PRESET = 2

# constants/text_constants.asm
MON_NAME_LENGTH = 11
PLAYER_NAME_LENGTH = 8


def decode_text(raw) -> str:
    """Decode the game's own character encoding into a Python string.

    Only the part that appears in names: letters, digits, space, and the "@"
    that terminates every string. Enough to read back what the game called
    something, which is the only way to check that a naming prompt was
    answered the way it was meant to be.
    """
    out = []
    for b in raw:
        if b == 0x50:                       # "@", end of string
            break
        if b == 0x7F:
            out.append(" ")
        elif 0x80 <= b <= 0x99:
            out.append(chr(ord("A") + b - 0x80))
        elif 0xA0 <= b <= 0xB9:
            out.append(chr(ord("a") + b - 0xA0))
        elif 0xF6 <= b <= 0xFF:
            out.append(chr(ord("0") + b - 0xF6))
        else:
            out.append("?")
    return "".join(out).strip()
