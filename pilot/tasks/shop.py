"""Go and buy more of what has run out.

The errand this repository has been telling people to run by hand. `catch.py`
and `ingame.py` have both said "buy some at a Mart" since they were written --
advice that names the place, the item and the reason, and then stops. This is
the rest of that sentence.
"""
from __future__ import annotations

import time

from .. import items as I
from ..session import PilotTimeout
from .base import TaskResult

# What "restock this" means when the caller does not say. Both are in
# preference order, because `restock` breaks a tie between two equally close
# counters on the order it was given.
BALLS = ("POKE_BALL", "GREAT_BALL")
POTIONS = ("POTION", "SUPER_POTION")


class ShopTask:
    """Walk to the nearest counter that stocks something and buy it."""

    name = "shop"

    def __init__(self, session, reader, control, nav, world, gamedata,
                 traveler, saver, backups, log=print):
        self.s = session
        self.r = reader
        self.gd = gamedata
        self.w = world
        self.trav = traveler
        self.log = log

    def run(self, item: str | None = None, want: int = 5) -> TaskResult:
        res = TaskResult()
        if self.r.in_battle():
            res.status = "blocked"
            res.message = "finish the battle first"
            return res

        names = self._resolve(item)
        if names is None:
            res.status = "blocked"
            res.message = (f"no counter in the game stocks {item} -- "
                           f"it is not something a Mart sells")
            return res

        started = time.time()
        where = self.trav.current_const()
        try:
            out = self.trav.restock(names, want=want)
        except PilotTimeout:
            res.status = "timeout"
            res.message = "ran out of budget on the way to the shop"
            return res

        res.stats = {
            "from": where,
            "wanted": f"{want}x {'/'.join(names)}",
            "bought": out.get("bought", 0),
            "spent": out.get("spent", 0),
            "wallet": self.r.money(),
            "seconds": f"{time.time() - started:.1f}",
        }
        # One lookup, not a `.get` guard around a subscript. `shop` is one of
        # the keys `restock` returns only once there was a shop to walk to, and
        # a guard that has to stay next to its subscript to be correct is a
        # thing to keep correct forever.
        shop = out.get("shop")
        if shop:
            res.stats["shop"] = shop
        # `restock` reports "already carrying N" as a success with nothing
        # bought, which it is: the errand is idempotent, and running it twice
        # should not be an error the second time.
        res.status = "completed" if out["ok"] else "blocked"
        res.message = out["message"]
        return res

    def _resolve(self, item: str | None) -> tuple[str, ...] | None:
        """What to buy, as a preference-ordered tuple, or None if unbuyable.

        A bare name is taken literally; the two shorthands cover what the pilot
        actually runs out of. Checked against the mart tables here rather than
        discovered four maps away, because "nothing sells that" is an answer
        that does not need a walk.
        """
        if item is None:
            return BALLS if self.r.carrying("POKE_BALL") == 0 else POTIONS
        key = item.strip().upper().replace(" ", "_").replace("-", "_")
        if key in ("BALL", "BALLS", "POKEBALL", "POKE_BALLS"):
            return BALLS
        if key == "POTIONS":
            return POTIONS
        try:
            self.gd.item_id(key)
        except KeyError:
            return None
        if not self.w.shops_selling(key):
            return None
        if not I.attributes(self.gd.root_str).get(key, {}).get("for_sale"):
            return None
        return (key,)
