"""Pick up what the map is holding.

Item balls and fruit trees. The disassembly names both, and for a ball it also
names the event flag that says whether it is still there -- so unlike the mobile
port, which has to walk over and press A to find out, this can decide before the
walk. What a tree has today is still only answerable by pressing A, because a
tree regrows and has no flag.
"""
from __future__ import annotations

import time

from ..session import PilotTimeout
from .base import TaskResult


class TakeTask:
    """Walk to everything this map is holding and pick it up."""

    name = "take"

    def __init__(self, session, reader, control, nav, world, gamedata,
                 traveler, saver, backups, log=print):
        self.r = reader
        self.trav = traveler
        self.log = log

    def run(self, max_things: int = 8) -> TaskResult:
        res = TaskResult()
        if self.r.in_battle():
            res.status = "blocked"
            res.message = "finish the battle first"
            return res

        where = self.trav.current_const()
        waiting = self.trav.things_here()
        if not waiting:
            # Nothing left is a completed errand, not a failure: it is the
            # state the errand exists to reach, and running it twice on the
            # same route should not report an error the second time.
            res.status = "completed"
            res.message = f"{where} has nothing left to pick up"
            res.stats = {"took": 0}
            return res

        started = time.time()
        try:
            out = self.trav.take_here(max_things=max_things)
        except PilotTimeout:
            res.status = "timeout"
            res.message = "ran out of budget before reaching everything"
            return res

        res.stats = {
            "where": where,
            "offered": len(waiting),
            "took": len(out["took"]),
            "seconds": f"{time.time() - started:.1f}",
        }
        if out["took"]:
            res.stats["items"] = ", ".join(out["took"])
        if out["unreachable"]:
            res.stats["unreachable"] = len(out["unreachable"])
        res.status = "completed" if out["ok"] else "blocked"
        res.message = out["message"]
        return res
