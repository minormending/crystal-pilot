"""Pokemon Crystal: the facts about this cartridge that are not in its symbols.

Everything the pilot needs is resolved by name from the .sym file or parsed out
of the disassembly -- except the handful of things that are neither, and those
all belong to the opening. Which tile Elm stands on, which of the three balls is
Cyndaquil, and the four doorways between a bedroom and Route 29 are facts about
*this game's script* rather than about the engine, and they used to live as
module constants in `tasks/bootstrap.py` where nothing could say they were
Crystal's rather than Gen 2's.

They are all measured from the disassembly's own map files: `maps/ElmsLab.asm`
object_events for the lab, `maps/PlayersHouse2F.asm` and its neighbours for the
doorways.
"""
from __future__ import annotations

from typing import ClassVar


class Crystal:
    id = "crystal"
    label = "Pokemon Crystal"
    can_bootstrap = True

    # A pokecrystal hack routinely keeps PM_CRYSTAL in its header, so the
    # header is a necessary condition and never a sufficient one -- claiming
    # every hack as Crystal means walking confidently into a lab that has been
    # moved. A symbol only the Johto tables define is asked for as well.
    #
    # What this cannot do is tell two hacks of the same base apart when neither
    # changed its header nor its symbols. The honest answer there is the
    # `?title=` style override, which `title_by_id` provides.
    @staticmethod
    def matches(header: str, symbols) -> bool:
        return header == "PM_CRYSTAL" and "JohtoGrassWildMons" in symbols

    # maps/ElmsLab.asm: the three Poké Balls sit in a row, and the aide's
    # object is at (5,2) so the tile to talk from is (5,3).
    lab: ClassVar[dict] = {"map": "ELMS_LAB", "stand": (5, 3),
                           "ball_row": 4}
    starters: ClassVar[dict] = {
        "cyndaquil": {"ball": (6, 4), "species": "CYNDAQUIL"},
        "totodile": {"ball": (7, 4), "species": "TOTODILE"},
        "chikorita": {"ball": (8, 4), "species": "CHIKORITA"},
    }

    # Bedroom to lab, one doorway at a time. `talk` marks a leg where a script
    # holds the player up -- Mom on the way out of the house, and Elm's
    # greeting on the way in -- so the walk runs scripts instead of retrying
    # the warp against a cutscene.
    intro_legs = (
        {"on": "PLAYERS_HOUSE_2F", "warp": (7, 0), "push": "up",
         "expect": "PLAYERS_HOUSE_1F", "talk": False},
        {"on": "PLAYERS_HOUSE_1F", "warp": (6, 7), "push": "down",
         "expect": "NEW_BARK_TOWN", "talk": True},
        {"on": "NEW_BARK_TOWN", "warp": (6, 3), "push": "up",
         "expect": "ELMS_LAB", "talk": True},
    )

    # Out of the lab and west onto Route 29's grass.
    first_route: ClassVar[dict] = {
        "from_map": "NEW_BARK_TOWN", "warp": (4, 11),
        "edge": "west", "route": "ROUTE_29"}


crystal = Crystal()
