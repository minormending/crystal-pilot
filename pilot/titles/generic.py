"""The profile that matches anything, and admits what it does not know.

An unknown cartridge is *supported at once* rather than refused until somebody
writes a file for it, and it is supported for more than it looks: everything the
pilot does after the opening is driven off the symbol table and the map files,
so grinding, hunting, catching, healing, shopping, taking and the trainer sweep
all work on a hack nobody has described.

What does not work is `bootstrap`, and this is where saying so is worth the
file. Starting a new game means knowing which tile a starter is on and which
doorway leads out of the bedroom, and guessing at those is not a degraded
service -- it is a pilot pressing A at a wall for four hundred taps and then
reporting that the intro never finished. `can_bootstrap` is False, and the task
refuses with a sentence naming what is missing.
"""
from __future__ import annotations

from typing import ClassVar


class Generic:
    id = "generic"
    label = "an undescribed cartridge"
    can_bootstrap = False

    @staticmethod
    def matches(header: str, symbols) -> bool:
        """Last in the list, so this is the fallback rather than a guess."""
        return True

    # Empty rather than absent: the contract checks the *shape* of these, and a
    # profile that omits them fails validation and falls through to... this one.
    starters: ClassVar[dict] = {}
    intro_legs: ClassVar[tuple] = ()
    lab = None
    first_route = None


generic = Generic()
