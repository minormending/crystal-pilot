"""Cross-map travel, and the Pokemon Center round trip that makes unattended
grinding possible.

Routes come from the world graph (edge connections + warps), so this works
anywhere in the game rather than from a hardcoded list of places.
"""
from __future__ import annotations

from . import items as I
from . import symbols as S
from .battle import BattleEngine, BattlePolicy

# How many separate items to spend on one Pokemon before giving up on the bag.
# Twelve Potions is 240HP, which covers anything the pilot will be grinding;
# the bound exists so a refused item cannot loop rather than to ration.
MAX_HEALS_PER_MON = 12


class Traveler:
    def __init__(self, session, reader, control, nav, world, gamedata, log=print):
        self.s = session
        self.r = reader
        self.c = control
        self.n = nav
        self.w = world
        self.gd = gamedata
        self.log = log
        # Which route the last `heal_up` took: "bag", "walk", or None.
        self.healed_via: str | None = None
        # Anything encountered while travelling is an obstacle, not an
        # opportunity -- we are usually travelling *because* HP is low.
        self._flee = BattleEngine(session, reader, control, gamedata,
                                  BattlePolicy(always_flee=True), log=log)

    def current_const(self) -> str:
        loc = self.r.location()
        return self.gd.map_name(loc.group, loc.number)

    def _handle_battle(self) -> None:
        if self.r.in_battle():
            self._flee.run(target_slot=None, max_turns=25)
            self.n.settle()

    def walk_hop(self, kind: str, target: str, warp: dict | None,
                 attempts: int = 8) -> bool:
        """Execute one BFS hop: an edge crossing or a warp.

        Crossing a route means walking through grass, so wild encounters
        interrupt constantly. Each interruption is fled and the hop retried --
        the walk resumes from wherever it got to, so progress accumulates
        rather than restarting.
        """
        before = self.current_const()
        for _ in range(attempts):
            self._handle_battle()
            if kind == "warp" and warp is not None:
                self.n.take_warp(warp["x"], warp["y"],
                                 on_battle=self._handle_battle)
            else:
                self.n.cross_edge(kind, on_battle=self._handle_battle)
            if self.r.in_battle():
                self._handle_battle()
                if self.current_const() == target:
                    return True
                continue          # retry the hop from where we stopped
            self.n.settle()
            now = self.current_const()
            if now == target:
                return True
            if now != before:
                self.log(f"  travel: ended up in {now}, expected {target}")
                return False
        return self.current_const() == target

    def travel_to(self, dest_const: str, max_hops: int = 14) -> bool:
        """Walk to `dest_const` using the world graph, re-planning after each hop.

        Hops that turn out not to be walkable are remembered and excluded, so the
        search falls back to another way round instead of retrying the same
        impassable link.
        """
        failed: set[tuple[str, str, str]] = set()
        for _ in range(max_hops):
            here = self.current_const()
            if here == dest_const:
                return True
            path = self.w.route_to(here, lambda c: c == dest_const, max_depth=8,
                                   avoid_hops=failed)
            if path is None:
                self.log(f"  travel: no route {here} -> {dest_const}"
                         + (" (after ruling out impassable links)" if failed else ""))
                return False
            kind, target, warp = path[0]
            if not self.walk_hop(kind, target, warp):
                if self.current_const() == here:
                    self.log(f"  travel: {here} -> {target} is not walkable; "
                             f"looking for another way")
                    failed.add((here, kind, target))
        return self.current_const() == dest_const

    # --- healing -----------------------------------------------------------
    def party_needs_healing(self) -> bool:
        return any(m.hp < m.max_hp or m.status_name != "OK" for m in self.r.party())

    def heal_up(self, force_walk: bool = False) -> bool:
        """Get the party back to full, cheapest way first. -> did it work.

        The order is the whole point, and it is three things deep:

        1. **Cures before HP.** A Potion does not fix poison, so a party that is
           poisoned *and* hurt has to be cured first -- otherwise the HP goes
           back up, the walk is skipped as unnecessary, and the poison is still
           there ticking down on the next patch of grass.
        2. **The bag before the walk.** A Potion already in the bag is free and
           instant; the nearest Pokemon Center on Route 30 is two maps and a
           gate building away, through grass, fleeing an encounter every few
           tiles. Reaching for the bag first is the single biggest thing the
           mobile port had that this did not.
        3. **The walk when the bag cannot finish.** Which is the ordinary case
           early on, and the reason the round trip is not going anywhere.

        `force_walk` skips the bag, for `heal --force` and for a caller that
        wants the Center's guarantee rather than the bag's best effort.

        Sets `healed_via` to "bag" or "walk" so a caller can say which it was.
        A row that reads "healed" without saying how is a row that cannot tell
        a two-second bag heal from a two-minute round trip.
        """
        self.healed_via = None
        if not force_walk:
            cured = self.cure_from_bag()
            healed = self.heal_from_bag()
            if (cured or healed) and not self.party_needs_healing():
                self.healed_via = "bag"
                return True
        went = self.heal_round_trip()
        if went:
            self.healed_via = "walk"
        return went

    def cure_from_bag(self) -> int:
        """Clear what a Potion cannot, out of the bag. -> how many were cured.

        Fainted members are skipped: nothing in the item pocket revives one, so
        offering a cure to a corpse is a press that cannot work. A Revive is
        `ITEMMENU_PARTY` too, which is exactly why the filter is on the mon and
        not only on the item.
        """
        cured = 0
        for mon in self.r.party():
            if mon.fainted:
                continue
            status = mon.status_name
            if status == "OK":
                continue
            name = self._carried(I.cures(self.gd.root_str, status))
            if name is None:
                self.log(f"  heal: nothing in the bag cures {status} "
                         f"on {mon.species_name}")
                continue
            self.log(f"  heal: {name} on {mon.species_name} ({status})")
            self.c.use_item_on(self.gd.item_id(name), mon.slot)
            # The mon is re-read rather than assumed: the game refuses an item
            # it considers pointless without spending it, and a cure that did
            # not land must not be counted as one.
            if self.r.mon(mon.slot).status_name == "OK":
                cured += 1
            else:
                self.log(f"  heal: {name} did not clear {status}")
        return cured

    def heal_from_bag(self) -> int:
        """Put HP back out of the bag. -> how many members were topped up.

        Picks the *least wasteful* item that finishes the job -- the smallest
        one whose amount covers what is missing -- and falls back to the largest
        available when nothing covers it, because two Potions on a 40HP hole is
        better than none. Sorting by price instead is how a Full Restore gets
        spent on four missing HP.
        """
        healed = 0
        amounts = I.healers(self.gd.root_str)
        for slot in range(self.r.party_count()):
            start = self.r.mon(slot)
            # Already full, or beyond the bag's help. Counting a member that
            # needed nothing as "healed" would have made a full party of six
            # report six heals and no items spent.
            if start.fainted or start.hp >= start.max_hp:
                continue
            for _ in range(MAX_HEALS_PER_MON):
                mon = self.r.mon(slot)
                if mon.hp >= mon.max_hp:
                    break
                name = self._best_healer(amounts, mon.max_hp - mon.hp)
                if name is None:
                    break
                self.log(f"  heal: {name} on {mon.species_name} "
                         f"({mon.hp}/{mon.max_hp})")
                self.c.use_item_on(self.gd.item_id(name), slot)
                if self.r.mon(slot).hp <= mon.hp:
                    # Either the game refused it or the press missed. Trying the
                    # same item again would loop, so stop and let the walk
                    # answer instead.
                    self.log(f"  heal: {name} moved no HP; leaving it to the walk")
                    break
            after = self.r.mon(slot)
            if after.hp >= after.max_hp:
                healed += 1
        return healed

    # --- the counter -------------------------------------------------------
    def restock(self, names, want: int = 5) -> dict:
        """Go and buy more of what has run out. -> a dict describing what happened.

        `names` is what would do, in preference order -- ("POKE_BALL",
        "GREAT_BALL") or the cures for a status. The nearest counter that
        stocks *any* of them wins, on legs first and preference second, which
        is why a caller lists them in the order it actually wants them.

        The wallet is checked before the walk, not at the counter. Walking four
        maps to discover you cannot afford one Potion is the same amount of
        walking as affording it, and the answer is more useful before the trip
        than after.
        """
        held = max((self.r.carrying(n) for n in names), default=0)
        if held >= want:
            return {"ok": True, "bought": 0, "held": held,
                    "message": f"already carrying {held}"}
        here = self.current_const()
        found = self.w.nearest_shop_for(here, names)
        if found is None:
            return {"ok": False, "bought": 0, "held": held,
                    "message": f"nothing sold anywhere stocks {', '.join(names)}"}
        shop, item, route = found
        need = want - held
        unit = I.price(self.gd.root_str, item)
        money = self.r.money()
        if unit and money < unit:
            return {"ok": False, "bought": 0, "held": held, "shop": shop,
                    "item": item,
                    "message": (f"a {item} costs {unit} and the wallet holds "
                                f"{money}")}
        # Buy what can be paid for rather than refusing the whole errand: three
        # Potions is a better outcome than a walk that ends in a refusal.
        afford = need if not unit else min(need, money // unit)
        self.log(f"  shop: {shop} for {afford}x {item} "
                 f"({len(route)} legs, {money} in the wallet)")
        if shop != here and not self.travel_to(shop):
            return {"ok": False, "bought": 0, "held": held, "shop": shop,
                    "item": item, "message": f"could not reach {shop}"}
        if not self.talk_to_clerk():
            return {"ok": False, "bought": 0, "held": held, "shop": shop,
                    "item": item, "message": "could not get the clerk's attention"}
        before_money, before_held = self.r.money(), self.r.carrying(item)
        refused = self.c.buy_from_clerk(self.gd.item_id(item), afford)
        self.c.close_menus()
        # **The money is the evidence.** Not the presses landing, and not the
        # pocket, which lags a purchase the same way it lags a use -- measured
        # on a heal, where wItems still listed a spent Potion until the pack
        # closed. The wallet moved on the same read as the item arriving.
        spent = before_money - self.r.money()
        got = self.r.carrying(item) - before_held
        if spent <= 0 and got <= 0:
            why = refused or "the counter took nothing"
            # Where else to try, which is the useful half of "not stocking it".
            # Cherrygrove keeps Poké Balls behind the Mystery Egg flag, so its
            # listed stock and its real stock differ for the whole early game --
            # and the honest answer to that is a place name, not a shrug.
            elsewhere = [s for s in self.w.shops_selling(item) if s != shop]
            hint = f"; {elsewhere[0]} also lists it" if elsewhere else ""
            return {"ok": False, "bought": 0, "held": before_held, "shop": shop,
                    "item": item, "message": f"{shop} is {why}{hint}"}
        return {"ok": True, "bought": got, "spent": spent, "shop": shop,
                "item": item, "held": self.r.carrying(item),
                "message": (f"bought {got}x {item} for {spent} at {shop}"
                            if got else
                            f"spent {spent} at {shop}; the bag has not caught up")}

    def talk_to_clerk(self) -> bool:
        """Stand at the counter and open the shop menu.

        Not the nurse's approach, and the difference is the counter. A nurse
        stands *behind* a desk you walk up to from below; a Mart clerk stands
        behind a one-tile counter you talk *across*, and the tile between is a
        wall. Measured in Cherrygrove: the clerk is at (1,3), (2,3) is the
        counter, and the player has to be at (3,3) facing left. The tile below
        the clerk is not walkable at all, so the nurse's "stand under it and
        press up" reaches nothing and reports no counter.

        So every tile that could see the clerk is tried -- the four adjacent
        ones and the four two away along an axis, which is the across-a-counter
        case -- nearest first, each facing toward the clerk. The confirmation
        is the shop's own box, never the press landing.
        """
        here = self.current_const()
        shop = self.w.shops.get(here)
        if shop is None:
            self.log(f"  shop: no counter recorded for {here}")
            return False
        cx, cy = shop["clerk"]
        loc = self.r.location()
        cm = self.n.collision
        spots = []
        for dist in (1, 2):
            spots += [((cx, cy + dist), "up"), ((cx, cy - dist), "down"),
                      ((cx + dist, cy), "left"), ((cx - dist, cy), "right")]
        if cm is not None and cm.calibrated:
            walkable = [s for s in spots if cm.walkable(*s[0])]
            spots = walkable or spots
        spots.sort(key=lambda s: abs(s[0][0] - loc.x) + abs(s[0][1] - loc.y))
        for (sx, sy), facing in spots:
            self.n.walk_to(sx, sy)
            at = self.r.location()
            if (at.x, at.y) != (sx, sy):
                continue
            self.n.face(facing)
            self.s.tap("a")
            self.s.tick(45)
            if self.c.await_box(S.BOX_SHOP_MENU, tries=20):
                return True
            # Wrong tile, or the press opened somebody else's dialogue. Clear
            # it before trying the next tile, or the next A answers this one.
            self.c.advance_text(max_taps=20, quiet_frames=60)
            self.c.close_menus()
        return False

    def _carried(self, names) -> str | None:
        """The first of `names` actually in the bag, in the order given."""
        for name in names:
            if self.r.carrying(name) > 0:
                return name
        return None

    def _best_healer(self, amounts: dict, missing: int) -> str | None:
        """The least wasteful HP item in the bag for a hole of `missing`.

        `FULL` (a Max Potion or Full Restore) sorts as bigger than any hole, so
        it is only chosen when nothing smaller is carried -- which is the right
        answer for the item that cannot be divided.
        """
        held = [(n, a) for n, a in amounts.items() if self.r.carrying(n) > 0]
        if not held:
            return None
        enough = [(n, a) for n, a in held if a is None or a >= missing]
        if enough:
            return min(enough, key=lambda na: (na[1] is None,
                                               na[1] if na[1] is not None else 0,
                                               na[0]))[0]
        return max(held, key=lambda na: (na[1] or 0, na[0]))[0]

    def heal_round_trip(self) -> bool:
        """Walk to the nearest Pokemon Center, heal, and come back.

        Returns True only if the party is actually at full HP afterwards.
        """
        origin = self.current_const()
        origin_loc = self.r.location()
        path = self.w.nearest_pokecenter(origin)
        if path is None:
            self.log(f"  heal: no Pokemon Center reachable from {origin}")
            return False
        center = path[-1][1]
        self.log(f"  heal: {origin} -> {center} ({len(path)} hops)")
        if not self.travel_to(center):
            self.log("  heal: could not reach the Center")
            return False
        if not self.talk_to_nurse():
            return False
        self.log(f"  heal: healed, returning to {origin}")
        if not self.travel_to(origin):
            self.log(f"  heal: healed but could not get back to {origin}")
            return False
        # Get back into the grass we were grinding in.
        self.n.walk_to(origin_loc.x, origin_loc.y)
        self._handle_battle()
        return True

    def talk_to_nurse(self) -> bool:
        here = self.current_const()
        nurse = self.w.nurses.get(here)
        if nurse is None:
            self.log(f"  heal: no nurse recorded for {here}")
            return False
        nx, ny = nurse
        for attempt in range(3):
            # Stand directly below the counter and face her.
            self.n.walk_to(nx, ny + 2)
            self.n.walk_to(nx, ny + 1)
            self.n.face("up")
            self.s.tap("a")
            self.c.run_scripts()
            self.n.settle()
            if not self.party_needs_healing():
                return True
            self.log(f"  heal: nurse attempt {attempt + 1} did not heal")
        return not self.party_needs_healing()
