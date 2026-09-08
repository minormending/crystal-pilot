"""The words on the screen, read out of the tilemap.

Gen 2 draws text as tiles, so `wTilemap` -- twenty by eighteen bytes of tile
ids -- holds the sentences a person is reading. They have been sitting in work
RAM the whole time, and this pilot drove the game's boxes by their *shape*
without ever looking at them.

Shape gets a long way and that is as far as shape goes. Three of the boxes in
`symbols.py` share `(2, 7)` and two share `(None, 0)`; the START menu grows, so
PACK sits at a different row depending on how far the game has got; and a box
whose row count is not a fact about it had to be found by pressing and checking.
The screen answers all three by name.

**The charmap is parsed, not measured.** Every other table in this project
comes out of the disassembly and this is no different: `constants/charmap.asm`
names all 374 of them. That is the difference between this and the mobile port,
which has only the ROM and had to dump the tilemap beside a screenshot and read
them against each other -- and it means a translated hack gets its own alphabet
for free.

The parse was checked against the mobile port's measurements anyway, because two
independent readings agreeing is worth more than either: `$80-$99` A-Z,
`$a0-$b9` a-z, `$f6-$ff` 0-9, `$7f` space, `$ea` the accented e in POKeMON,
`$ed` the cursor arrow, `$79-$7e` the box border. All eight agree.

Nothing here presses a button. Every function is a pure function of a tilemap
snapshot and a charmap.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

CHARMAP_ROW = re.compile(r'^\s*charmap\s+"(.*?)"\s*,\s*\$([0-9a-fA-F]{1,2})\s*(?:;.*)?$')
# `pushc` / `popc` bracket a *different* charmap -- the Unown alphabet and the
# mobile adapter's ASCII. Those tile ids mean something else entirely there, so
# the block is skipped rather than merged.
PUSH_C = re.compile(r"^\s*pushc\b")
POP_C = re.compile(r"^\s*popc\b")

SCREEN_W, SCREEN_H = 20, 18
SCREEN_AREA = SCREEN_W * SCREEN_H

# Readable text starts at $7f, and the floor matters more than it looks.
#
# `constants/charmap.asm` names ids below it too, and they are not letters: $50
# is the string *terminator*, $60-$6c are a bold alphabet from a different font
# sheet, $74 and $75 are marked unused. Meanwhile the tilemap uses that whole
# range for *pictures* -- measured with the pack open, the item illustration
# occupies $50-$5e, so the charmap decoded the picture as `@   #` and a shop's
# failure message quoted it back at the reader.
#
# The disassembly draws the line itself: the block from $7f is commented
# "Actual characters (from gfx/font/font.png)", which is the font the game
# writes sentences with. Everything below is control codes and other sheets.
#
# One real loss, stated rather than hidden: $6d is a colon with tinier dots,
# used in the save panel's "0:03", so that reads "0 03". The main font has its
# own colon at $9c and every sentence uses that one; admitting $6d would mean
# admitting the whole bold-alphabet range with it, and a picture decoding as
# words is the worse failure.
FONT_FIRST = 0x7F

# The cursor arrow, `charmap "▶"`. It is a *tile* in a list menu and a *sprite*
# in a yes/no box, which is the one thing about it worth knowing: a list can be
# driven by following the arrow, and a question cannot. `answer_yes_no` reads
# `wMenuCursorY` for exactly that reason.
CURSOR = "▶"

# Some charmap entries are written as `<TAG>` and still draw a character.
# Empty, and kept as the place to name one if a tag ever draws a single
# readable character *at or above* `FONT_FIRST`. Every candidate today --
# `<COLON>` and the bold alphabet -- sits below the floor and is excluded for
# the reason given there. `<PO>`, `<KE>` and `<LV>` would be excluded anyway:
# each draws *two* characters in one tile, and a two-character cell shifts every
# column after it, which matters because a row's position is what
# `menu_row_named` converts into a cursor value.
TAG_GLYPHS: dict[str, str] = {}


def _readable(glyph: str) -> bool:
    """Does this glyph appear in words a person reads off the screen?

    Everything else renders as a space, and the reason is the *graphics*. A
    tilemap holds pictures as well as text -- the pack's item icon, the pocket
    illustration, a box border -- and those tile ids are simply not claimed by
    the English part of the charmap. Read to the end of the file they pick up
    whatever the Japanese kana block assigned them, so the pack's picture
    decodes as `ぐげござ` and a phrase match can land inside it.

    Folding to ASCII first is what lets the accented e in POKeMON through while
    keeping kana and box-drawing out: `é` normalises to `e`, `ぐ` does not
    normalise to anything ASCII.
    """
    if glyph == CURSOR:
        return True
    folded = unicodedata.normalize("NFKD", glyph)
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return bool(stripped) and stripped.isascii() and stripped.isprintable()


# Tiles that draw a picture rather than a letter. Rendered as spaces, because
# naming them would put box-drawing characters in the middle of a line that a
# person reads as words -- and then a phrase spanning a border would not match.
GRAPHIC = frozenset("┌─┐│└┘")


@lru_cache(maxsize=4)
def charmap(source_root: str) -> dict[int, str]:
    """tile id -> the character it draws.

    First definition wins, which is the same rule `SymbolTable` applies to the
    symbol file and for the same reason: the English block comes first, and
    `constants/charmap.asm` then re-uses a dozen of those ids for Japanese kana
    that were never translated. Reading the file to the end makes `$ea` the
    katakana small u instead of the accented e in POKeMON, and `$e8` a Japanese
    full stop instead of a full stop.

    Multi-character entries are control codes -- `<PLAYER>`, `<LF>`, `<POKE>`
    -- and are skipped: they are text-engine directives, not glyphs, and a
    tilemap holds what was drawn rather than what produced it.
    """
    path = Path(source_root) / "constants" / "charmap.asm"
    out: dict[int, str] = {}
    if not path.exists():
        return out
    depth = 0
    for line in path.read_text(errors="replace").splitlines():
        if PUSH_C.match(line):
            depth += 1
            continue
        if POP_C.match(line):
            depth = max(0, depth - 1)
            continue
        if depth:
            continue
        m = CHARMAP_ROW.match(line)
        if not m:
            continue
        raw, tile = m.group(1), int(m.group(2), 16)
        if tile < FONT_FIRST:
            continue
        glyph = TAG_GLYPHS.get(raw, raw)
        if len(glyph) != 1 or not _readable(glyph):
            continue
        out.setdefault(tile, glyph)
    return out


def _fold(text: str) -> str:
    """Letters and digits only, upper-cased, for matching.

    The screen is not a string. POKeMON's accented letter is one tile, an item
    count reads `x 1` with the space drawn, and a logo can be graphics with a
    word next to it. Folding to letters and digits makes a phrase match what a
    person would say is on the screen rather than what the tiles happen to be:
    `POKéMON` and `POKEMON` are the same question.
    """
    plain = unicodedata.normalize("NFKD", text)
    return "".join(c for c in plain.upper() if c.isalnum())


class Screen:
    """One tilemap snapshot, decoded. Immutable and inert."""

    __slots__ = ("_rows",)

    def __init__(self, rows: list[str]):
        self._rows = rows

    @classmethod
    def decode(cls, tiles, table: dict[int, str]) -> Screen:
        rows = []
        for y in range(SCREEN_H):
            chars = []
            for x in range(SCREEN_W):
                idx = y * SCREEN_W + x
                glyph = table.get(tiles[idx], " ") if idx < len(tiles) else " "
                chars.append(" " if glyph in GRAPHIC else glyph)
            rows.append("".join(chars))
        return cls(rows)

    def lines(self) -> list[str]:
        """The eighteen rows, as drawn, with graphics as spaces."""
        return list(self._rows)

    def text(self) -> str:
        return "\n".join(self._rows)

    def says(self, phrase: str) -> bool:
        """Is `phrase` on any single row?

        **Per row, deliberately.** Two unrelated lines that happen to abut are
        not a sentence, and a box's last line sitting above the text window's
        first would otherwise match phrases nobody wrote.
        """
        want = _fold(phrase)
        return any(want in _fold(row) for row in self._rows) if want else False

    def row_of(self, phrase: str) -> int | None:
        """The first row containing `phrase`, or None."""
        want = _fold(phrase)
        if not want:
            return None
        for y, row in enumerate(self._rows):
            if want in _fold(row):
                return y
        return None

    def cursor_row(self) -> int | None:
        """The screen row the arrow is drawn on, or None if it is not drawn.

        None is the ordinary answer in a yes/no box, where the arrow is a
        sprite rather than a tile -- so a caller that needs to know where the
        cursor is in a *question* has to read `wMenuCursorY` instead.
        """
        for y, row in enumerate(self._rows):
            if CURSOR in row:
                return y
        return None

    def said(self, lines: int = 2) -> str:
        """The last few readable rows, as one short line for a message.

        Trimmed hard, because this goes on the end of a failure somebody reads:
        the bottom rows are the box and the text under it, which is what they
        would have been looking at.
        """
        rows = [r.strip() for r in self._rows]
        rows = [r for r in rows if r]
        return " / ".join(rows[-lines:]) if rows else ""

    def __repr__(self) -> str:
        return f"<Screen {self.said(1)!r}>"


def read(session, source_root: str) -> Screen | None:
    """The screen right now, or None on a build whose symbols do not name it.

    None rather than a blank screen, because "cannot read" and "says nothing"
    lead somewhere different -- the same distinction `live_objects` and
    `event_done` make. A caller appending this to a message wants to know which
    it has.
    """
    table = charmap(source_root)
    if not table:
        return None
    try:
        base = session.sym.addr("wTilemap")
    except KeyError:
        return None
    return Screen.decode(session.rbytes(base, SCREEN_AREA), table)
