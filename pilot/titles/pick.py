"""Which cartridge is this, and therefore which profile drives its opening.

One registry and one function. Every title says how to recognise itself and the
first that agrees wins, with `generic` last -- so an unknown cartridge is
supported immediately rather than refused.

Recognising a hack is not a solved problem and this does not pretend otherwise.
A pokecrystal hack routinely keeps PM_CRYSTAL in its header, so a header match
alone would claim every hack as Crystal and then walk into a lab that has been
moved. The header is a necessary condition and never a sufficient one; a profile
may also probe the symbol table, and Crystal asks for both.
"""
from __future__ import annotations

from .contract import validate_title
from .crystal import crystal
from .generic import generic

# Most specific first. The last entry must match anything.
TITLES = (crystal, generic)

# Cartridge header title, 0x0134-0x0143. NUL-terminated in practice: Crystal
# writes "PM_CRYSTAL" and then a manufacturer code, so reading all sixteen
# bytes gives "PM_CRYSTALBYTE" and no profile matches.
HEADER_START = 0x0134
HEADER_LENGTH = 16


def header_title(session) -> str:
    """The cartridge's own name for itself."""
    raw = [session.pyboy.memory[HEADER_START + i] for i in range(HEADER_LENGTH)]
    out = []
    for byte in raw:
        if byte == 0:
            break
        if 32 <= byte < 127:
            out.append(chr(byte))
    return "".join(out).strip()


def pick_title(session, log=print):
    """The profile that drives this cartridge. Never None -- `generic` is last.

    A profile that fails the contract is skipped with its reasons printed,
    rather than used: a hack with a broken description keeps a working pilot.
    """
    header = header_title(session)
    for title in TITLES:
        bad = validate_title(title)
        if bad:
            log(f"warning: ignoring the {title.id} profile -- "
                + "; ".join(bad))
            continue
        try:
            if title.matches(header, session.sym):
                return title
        except Exception as e:  # noqa: BLE001 -- a broken matcher must not stop the pilot
            log(f"warning: the {title.id} profile's matches() raised: {e}")
    # Unreachable while `generic` is in TITLES and valid, and returned anyway
    # rather than raising: no profile at all is still a drivable pilot.
    return generic


def title_by_id(name: str, log=print):
    """One profile by id, if it is usable -- the override for a named hack.

    Gated the same way as recognising one. Naming a profile by hand is the one
    path a profile author would use to try their own file, so putting it past
    the only thing that would tell them it is wrong would be the worst place to
    skip the check.
    """
    found = next((t for t in TITLES if t.id == name), None)
    if found is None:
        log(f"warning: no profile called {name!r}; there is "
            + ", ".join(t.id for t in TITLES))
        return None
    bad = validate_title(found)
    if bad:
        log(f"warning: the {name} profile is not usable -- " + "; ".join(bad))
        return None
    return found
