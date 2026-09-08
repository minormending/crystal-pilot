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
# constants/pokemon_data_constants.asm. The active PC box holds twenty, and a
# full one is why a catch with a full party can still have nowhere to put it.
MONS_PER_BOX = 20

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

# --- which box is on screen ------------------------------------------------
# "A window is open" and "the cursor is somewhere" are both true of the *wrong*
# box, because the cursor keeps its previous value between boxes. What
# identifies one is its shape: how many rows it has (wMenuDataItems) and which
# screen row it starts at (wMenuBorderTopCoord). Every box the pack and the
# shop pass through is therefore matched on shape before anything is pressed
# into it.
#
# Measured rather than read off the menu headers, because a header is loaded by
# a routine and the values that reach WRAM are what a driver can see. The shop
# figures came from Cherrygrove's Mart, buying two POTIONs at 300 each and
# watching the wallet fall 3000 -> 2700 -> 2400.
#
# Three of these share a signature -- LEARN_MOVE, SHOP_CONFIRM and
# BATTLE_ITEM_USE are all two rows at row 7. That is safe only because the
# contexts cannot overlap: a shop box exists only while a shop is being driven.
# It is a claim the code has to keep, not a property it gets for free.
BOX_PACK = (5, 1)                # the pack, opened from START
BOX_ITEM_USE = (4, 3)            # USE / GIVE / TOSS / QUIT
BOX_BATTLE_ITEM = (2, 7)         # the battle pack's own USE / QUIT
# "Use on which Pokemon?" -- matched by where it starts, not by how many rows
# it has. Measured on the same one-Pokemon party: the field pack's party list
# reports four rows, the battle pack's reports two. Two menu headers for one
# question, so the count is not a fact about this box.
#
# `top == 0` is shared with the START menu and the shop's thanks, which is safe
# only because of *when* it is asked: immediately after confirming USE inside
# an open pack, neither of those can be on screen. That is a claim this file's
# callers have to keep rather than a property the shape gives them.
BOX_PARTY_PICK = (None, 0)
BOX_SHOP_MENU = (3, 0)           # BUY / SELL / QUIT, BUY on row 1
BOX_SHOP_LIST = (4, 3)           # what the mart stocks; wCurItem says which
BOX_SHOP_HOW_MANY = (4, 15)      # the quantity box
BOX_SHOP_CONFIRM = (2, 7)        # "that'll be N. OK?", YES on row 1
BOX_SHOP_DONE = (2, 0)           # the thanks; A returns to the list

# wCurItem while the cursor sits on the pack's CANCEL row. DOWN past the last
# entry lands here and *stays* -- the list does not wrap -- so an overshoot has
# to be walked back rather than pressed through.
CANCEL_ITEM = 0xFF

# The pack's USE row. GIVE and TOSS are the two rows under it, and TOSS throws
# the item away, which is why the cursor is confirmed rather than assumed even
# though it opens here.
PACK_USE_ROW = 1

# How long to hold A inside the *field* pack.
#
# Measured, one frame at a time, on a Route 30 save with three Potions: a hold
# of 5, 6 or 7 frames is swallowed and the USE box never appears; 8 lands, and
# so does everything above it. The ordinary tap is 6, which is why the first
# version of this reported "the USE box never appeared" from inside a pack that
# was open, on the right item, with the cursor in the right place.
#
# It is the *field* pack's quirk and not the pack's: the same press in the
# battle pack lands at 6, measured the same way. So this is deliberately not
# applied to `throw_ball`, which has always worked and would only be put at
# risk by being changed.
#
# The D-pad in the same list wants the opposite: at a hold of 8 a DOWN press
# auto-repeats and the cursor arrives back where it started. Those presses stay
# short, and `_pack_moved` watches the value rather than trusting either number.
FIELD_PACK_HOLD = 10
FIELD_PACK_GAP = 12


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
