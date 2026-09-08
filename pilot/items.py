"""What the bag can hold: prices, pockets, and what each item actually fixes.

Parsed from `data/items/*.asm` for the same reason as everything else here --
the numbers come from the files the ROM was assembled from rather than from a
table somebody typed once and then had to remember to update.

Three different questions get asked of this module, and it matters that they
are three rather than one:

  * can this be *used*, and on a party member?   `attributes()`
  * how much HP does it put back?                `heals_hp()`
  * which status does it cure?                   `cures_status()`

A Potion answers the first two and not the third. An Antidote answers the first
and the third. A Full Restore answers all three. Reading one table for all of it
is how a pilot walks a poisoned party past a Full Heal it is already carrying --
which is exactly the bug the mobile port found, and the reason the healing path
asks about status before it asks about HP.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .gamedata import parse_consts

# item_attribute price, held effect, parameter, property, pocket, field, battle
#
# The `property` field is a *flag expression*, not a name: twenty-five key items
# write it `CANT_SELECT | CANT_TOSS`. A pattern expecting one word there matched
# 232 of the file's 257 rows and dropped the rest in silence -- which is the
# failure this module's own docstring warns about, committed by the module. The
# fields that can carry an expression are matched as one, and only the four
# fields actually read are captured.
FIELD = r"[\w\s|]+?"
ITEM_ATTR = re.compile(
    r"^\s*item_attribute\s+(-?\$?\w+)\s*,"      # price
    rf"\s*{FIELD}\s*,"                          # held effect
    r"\s*(?:-?\$?\w+)\s*,"                      # parameter
    rf"\s*{FIELD}\s*,"                          # property
    r"\s*(\w+)\s*,"                             # pocket
    r"\s*(\w+)\s*,"                             # field menu
    r"\s*(\w+)"                                 # battle menu
)
# The comment line above each row is the only place the item's *name* appears.
ATTR_NAME = re.compile(r"^;\s*([A-Z0-9_]+)\s*$")
# dbw ITEM, amount  -- amount is a number or MAX_STAT_VALUE
HEAL_HP = re.compile(r"^\s*dbw\s+([A-Z0-9_]+)\s*,\s*(\S+)")
# db ITEM, PARTYMENUTEXT_x, <status mask expression>
HEAL_STATUS = re.compile(
    r"^\s*db\s+([A-Z0-9_]+)\s*,\s*PARTYMENUTEXT_\w+\s*,\s*(.+?)\s*$"
)
MART_LABEL = re.compile(r"^(Mart[A-Za-z0-9_]*):\s*$")
# `[A-Z_]` first, not `[A-Z0-9_]`: every stock list opens with a count byte
# written `db 4`, which a pattern allowing a leading digit reads as an item
# called "4" -- and then the shopping errand's first candidate is a name no
# item table has.
MART_ITEM = re.compile(r"^\s*db\s+([A-Z_][A-Z0-9_]*)\s*$")
# The `Marts:` pointer table, in MART_* constant order.
MART_POINTER = re.compile(r"^\s*dw\s+(Mart[A-Za-z0-9_]*)\s*$")

# constants/item_data_constants.asm. The names are the ones the attribute rows
# use, which drop the `_POCKET` suffix the constants carry.
POCKETS = {"ITEM": 0, "BALL": 1, "KEY_ITEM": 2, "TM_HM": 3}
ITEM_POCKET, BALL_POCKET, KEY_ITEM_POCKET, TM_HM_POCKET = 0, 1, 2, 3

# How many *kinds* each pocket holds, from the same file. The ball pocket is
# twelve, not twenty: reading twenty entries walks eight entries past the end
# of wBalls and into wNumKeyItems.
POCKET_LIMITS = {ITEM_POCKET: 20, BALL_POCKET: 12, KEY_ITEM_POCKET: 25}

# The (count, list) symbol pair for each pocket that is a list of
# (item, quantity) pairs. Only two are, and the other two are left out rather
# than read wrongly:
#
#   KEY_ITEM  is one byte per entry with no quantity -- a key item is unique,
#             so there is nothing to count. The addresses prove it: wKeyItems
#             is d8bd and wNumBalls is d8d7, twenty-six bytes later, which is
#             MAX_KEY_ITEMS plus a terminator and not twice that. Handing it to
#             the pair reader interleaves ids with ids and reports half the
#             pocket as quantities of the other half.
#   TM_HM     is a fixed-length bitfield rather than a list at all.
#
# Neither is needed by anything the pilot does, and a reader that returns
# plausible nonsense is worse than one that refuses.
POCKET_SYMBOLS = {
    ITEM_POCKET: ("wNumItems", "wItems"),
    BALL_POCKET: ("wNumBalls", "wBalls"),
}

# An item is usable on a party member when its menu field says PARTY. NOUSE
# means the entry is drawn greyed out; CLOSE means using it shuts the pack
# (the Bicycle), which is not something a party member is chosen for.
MENU_PARTY = "ITEMMENU_PARTY"

# The five names `Mon.status_name` reports, so the two halves agree on
# spelling. SLP is a counter rather than a bit, which is why it has a mask of
# its own in the source.
STATUS_NAMES = ("SLP", "PSN", "BRN", "FRZ", "PAR")

# data/items/heal_hp.asm writes a full heal as MAX_STAT_VALUE rather than as a
# number, and treating that as 999 would be a lie in the one direction that
# matters: it would make MAX_POTION look worse than HYPER_POTION on a mon with
# more than 999 max HP, which cannot happen, and better than it on one with
# less, which is the ordinary case. `None` means "as much as it is missing".
FULL = None

# The sentinel price the disassembly gives things that are not for sale. It is
# a real number in the table, so an unguarded read makes the Town Map look like
# the most expensive item in the game.
NOT_FOR_SALE = 0x9999


def _amount(raw: str) -> int | None:
    raw = raw.strip()
    if raw == "MAX_STAT_VALUE":
        return FULL
    if raw.startswith("$"):
        return int(raw[1:], 16)
    try:
        return int(raw, 0)
    except ValueError:
        return FULL


def _statuses(expr: str) -> frozenset[str]:
    """Which of STATUS_NAMES a mask expression covers.

    The expressions are `1 << PSN`, `SLP_MASK`, or `%11111111` for everything.
    Matching the *names* out of the expression rather than evaluating the
    arithmetic keeps this honest about the one case that is not a shift: sleep
    is a three-bit counter, so it appears as a mask and never as `1 << SLP`.
    """
    expr = expr.split(";", 1)[0]
    if "%11111111" in expr or "ALL_STATUS" in expr:
        return frozenset(STATUS_NAMES)
    found = {n for n in STATUS_NAMES if re.search(rf"\b{n}\b", expr)}
    if "SLP_MASK" in expr:
        found.add("SLP")
    return frozenset(found)


@lru_cache(maxsize=4)
def attributes(source_root: str) -> dict[str, dict]:
    """ITEM_NAME -> {price, pocket, field_menu, battle_menu, for_sale}.

    Keyed by name rather than by id because this file never sees the id enum;
    the comment above each row is the only thing naming the entry, and the rows
    are in id order. Callers that have an id ask `GameData.item_name` first.
    """
    path = Path(source_root) / "data" / "items" / "attributes.asm"
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    pending: str | None = None
    for line in path.read_text(errors="replace").splitlines():
        m = ATTR_NAME.match(line.strip())
        if m:
            pending = m.group(1)
            continue
        m = ITEM_ATTR.match(line)
        if not m:
            continue
        price_raw, pocket, field_menu, battle_menu = m.groups()
        price = _amount(price_raw) or 0
        if pending is not None:
            out[pending] = {
                "name": pending,
                "price": price,
                "pocket": POCKETS.get(pocket, ITEM_POCKET),
                "field_menu": field_menu,
                "battle_menu": battle_menu,
                # Both conditions matter. A price of zero is the Master Ball,
                # which no counter sells; the sentinel is the Town Map, whose
                # listed price would otherwise make it the costliest thing
                # here. What a shop *actually* stocks is `mart_items`; this
                # only says whether a price can be paid at all.
                "for_sale": 0 < price < NOT_FOR_SALE,
            }
        pending = None
    return out


@lru_cache(maxsize=4)
def heals_hp(source_root: str) -> dict[str, int | None]:
    """ITEM_NAME -> HP restored, or FULL (None) for "all of it"."""
    path = Path(source_root) / "data" / "items" / "heal_hp.asm"
    out: dict[str, int | None] = {}
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        m = HEAL_HP.match(line.split(";", 1)[0])
        if m and not m.group(1).startswith("-"):
            out[m.group(1)] = _amount(m.group(2))
    return out


@lru_cache(maxsize=4)
def cures_status(source_root: str) -> dict[str, frozenset[str]]:
    """ITEM_NAME -> the statuses it clears, as {"PSN"}, {"SLP"}, or all five."""
    path = Path(source_root) / "data" / "items" / "heal_status.asm"
    out: dict[str, frozenset[str]] = {}
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        m = HEAL_STATUS.match(line.split(";", 1)[0])
        if m and not m.group(1).startswith("-"):
            found = _statuses(m.group(2))
            if found:
                out[m.group(1)] = found
    return out


@lru_cache(maxsize=4)
def mart_items(source_root: str) -> dict[str, tuple[str, ...]]:
    """Mart label -> the items it stocks, e.g. "MartCherrygrove" -> (POTION, ...).

    Keyed by the assembly label rather than by the MART_* constant, because the
    label is what a map's `pokemart` line names and therefore what joins a shop
    to a place you can walk to. The count byte is skipped: it is `db 4`, which
    the item pattern would otherwise not match anyway, and the terminator is
    `db -1`.
    """
    path = Path(source_root) / "data" / "items" / "marts.asm"
    out: dict[str, tuple[str, ...]] = {}
    if not path.exists():
        return out
    current: str | None = None
    items: list[str] = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.split(";", 1)[0]
        m = MART_LABEL.match(line)
        if m:
            if current and items:
                out[current] = tuple(items)
            current, items = m.group(1), []
            continue
        if current is None:
            continue
        m = MART_ITEM.match(line)
        if m:
            items.append(m.group(1))
    if current and items:
        out[current] = tuple(items)
    return out


@lru_cache(maxsize=4)
def mart_stock(source_root: str) -> dict[str, tuple[str, ...]]:
    """MART_* constant -> the items that counter stocks.

    Joins two files by position, which is how the disassembly itself joins
    them: `Marts:` in data/items/marts.asm is a table of `dw MartCherrygrove`
    pointers "in the order of the MART_* constants", and mart_constants.asm is
    that order. So index 0 of the table is the first MART_* constant.

    Keyed by the constant rather than the label because the constant is what a
    map's `pokemart` line names, and the label is an implementation detail
    sitting between them.
    """
    consts_path = Path(source_root) / "constants" / "mart_constants.asm"
    marts_path = Path(source_root) / "data" / "items" / "marts.asm"
    if not consts_path.exists() or not marts_path.exists():
        return {}
    # Two `const_def` blocks in that file: MARTTYPE_* then MART_*. Both restart
    # at zero, and the prefixes keep them apart.
    consts = {
        k: v for k, v in parse_consts(consts_path).items()
        if k.startswith("MART_") and not k.startswith("MARTTYPE_")
    }
    order: list[str] = []
    for line in marts_path.read_text(errors="replace").splitlines():
        m = MART_POINTER.match(line.split(";", 1)[0])
        if m:
            order.append(m.group(1))
    stock = mart_items(source_root)
    out: dict[str, tuple[str, ...]] = {}
    for const, index in consts.items():
        if index < len(order):
            found = stock.get(order[index])
            if found:
                out[const] = found
    return out


def sold_at(source_root: str, marts) -> tuple[str, ...]:
    """Everything the given MART_* constants stock between them, deduplicated.

    A map can hold more than one counter -- Cherrygrove's clerk has two stock
    lists behind an event flag, and Goldenrod's floors have four -- and which
    one a visit gets depends on story progress this cannot know. So the union
    is what a *destination* is chosen on, and the counter itself is the
    authority on what is actually there: `buy_from_clerk` drives the real list.
    """
    table = mart_stock(source_root)
    seen: list[str] = []
    for const in marts:
        for name in table.get(const, ()):
            if name not in seen:
                seen.append(name)
    return tuple(seen)


# --- the questions the pilot actually asks ---------------------------------
def healers(source_root: str, in_battle: bool = False) -> dict[str, int | None]:
    """Bag items that restore HP to a party member, name -> amount.

    Filtered by the menu field that applies where we are standing. A Berry
    heals HP but is `ITEMMENU_NOUSE` in the field, so offering it out of battle
    is offering a press that does nothing -- and "nothing happened" is the
    hardest failure to tell apart from "the menu did not open".
    """
    attrs = attributes(source_root)
    field = "battle_menu" if in_battle else "field_menu"
    return {
        name: amount
        for name, amount in heals_hp(source_root).items()
        if attrs.get(name, {}).get(field) == MENU_PARTY
    }


def cures(source_root: str, status: str,
          in_battle: bool = False) -> tuple[str, ...]:
    """Bag items that clear `status`, the one to spend first at the front.

    Ordered by how *narrow* the cure is before how cheap it is, and the order
    matters more than the price does. A Full Heal and an Antidote both end a
    poisoning; so does a Miracleberry, which costs less than either. Sorting on
    price alone therefore spends the one item that answers all five statuses on
    the one status that has four other answers -- and the party is then asleep
    with nothing that wakes it. Narrowest first, then cheapest among equals.
    """
    attrs = attributes(source_root)
    all_statuses = cures_status(source_root)
    field = "battle_menu" if in_battle else "field_menu"
    found = [
        name for name, statuses in all_statuses.items()
        if status in statuses and attrs.get(name, {}).get(field) == MENU_PARTY
    ]
    return tuple(sorted(
        found, key=lambda n: (len(all_statuses[n]), attrs[n]["price"] or 0, n)))


def price(source_root: str, name: str) -> int:
    """What a counter charges for one, or 0 for something no counter sells."""
    attr = attributes(source_root).get(name)
    return attr["price"] if attr and attr["for_sale"] else 0
