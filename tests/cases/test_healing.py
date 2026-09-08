"""Healing: the bag first, then the walk.

The order these run in is the feature. A Potion in the bag is instant; the
nearest Pokemon Center from Route 30 is two maps and a gate building away,
through grass, fleeing an encounter every few tiles. And a Potion does not fix
poison, so the cure has to come first or the walk gets skipped as unnecessary
with the poison still there.
"""
from pilot import items as I
from pilot import symbols as S

from ..harness import test


def _give(t, p, *pairs):
    gd = t.gamedata
    t.give_items(p, ((I.ITEM_POCKET,
                      tuple((gd.item_id(n), q) for n, q in pairs)),))


def _hurt(p, hp: int, slot: int = 0) -> None:
    base = p.session.sym.addr("wPartyMon1") + slot * S.PARTY_STRUCT_LEN
    p.session.wb(base + S.MON_HP, hp >> 8)
    p.session.wb(base + S.MON_HP + 1, hp & 0xFF)


def _afflict(p, status: str, slot: int = 0) -> None:
    base = p.session.sym.addr("wPartyMon1") + slot * S.PARTY_STRUCT_LEN
    p.session.wb(base + S.MON_STATUS, S.STATUS_BITS[status])


@test("a Potion in the bag heals without going anywhere")
def _(t):
    p = t.pilot("route30")
    _give(t, p, ("POTION", 3))
    _hurt(p, 24)
    where = p.traveler.current_const()
    t.true(p.traveler.heal_up(), "healed")
    t.eq(p.traveler.healed_via, "bag", "out of the bag, not by walking")
    t.eq(p.reader.mon(0).hp, 44, "back to full")
    t.eq(p.reader.carrying("POTION"), 2, "one Potion spent")
    t.eq(p.traveler.current_const(), where, "and never left the route")


@test("poisoned at full HP is a party that needs healing")
def _(t):
    p = t.pilot("route30")
    _give(t, p, ("ANTIDOTE", 2))
    _afflict(p, "PSN")
    t.eq(p.reader.mon(0).hp, p.reader.mon(0).max_hp, "HP is untouched")
    # This is the case the old code could not see at all: `heal` filtered on
    # `hp < max_hp` alone, so a poisoned party at full HP reported "already at
    # full health" and did nothing -- which is the exact state a grind leaves.
    t.true(p.traveler.party_needs_healing(), "still needs healing")
    t.true(p.traveler.heal_up(), "and it gets it")
    t.eq(p.reader.mon(0).status_name, "OK", "poison cleared")
    t.eq(p.traveler.healed_via, "bag", "out of the bag")


@test("the cure comes before the Potion")
def _(t):
    p = t.pilot("route30")
    _give(t, p, ("ANTIDOTE", 2), ("POTION", 3))
    _hurt(p, 24)
    _afflict(p, "PSN")
    t.true(p.traveler.heal_up(), "healed")
    mon = p.reader.mon(0)
    # Both, and in that order. Healing the HP first and then checking
    # `party_needs_healing` would see a full-HP party, call it done, and leave
    # the poison ticking on the next patch of grass.
    t.eq(mon.status_name, "OK", "cured")
    t.eq(mon.hp, mon.max_hp, "and topped up")
    t.eq(p.reader.carrying("ANTIDOTE"), 1, "an Antidote spent")
    t.eq(p.reader.carrying("POTION"), 2, "and a Potion")


@test("the least wasteful item that finishes the job is the one spent")
def _(t):
    p = t.pilot("route30")
    _give(t, p, ("POTION", 2), ("FULL_RESTORE", 1))
    amounts = I.healers(t.gamedata.root_str)
    # A Full Restore on four missing HP is the failure mode of sorting by
    # price, or by "biggest first". The smallest that covers the hole wins.
    t.eq(p.traveler._best_healer(amounts, 4), "POTION", "4 missing -> Potion")
    t.eq(p.traveler._best_healer(amounts, 20), "POTION", "20 missing -> Potion")
    # Nothing smaller covers 40, so the indivisible one is right after all.
    t.eq(p.traveler._best_healer(amounts, 40), "FULL_RESTORE", "40 -> Full Restore")


@test("an empty bag falls through to the walk rather than claiming success")
def _(t):
    p = t.pilot("route30")
    t.give_items(p, ((I.ITEM_POCKET, ()),))
    _hurt(p, 24)
    t.eq(p.traveler.cure_from_bag(), 0, "nothing to cure with")
    t.eq(p.traveler.heal_from_bag(), 0, "and nothing to heal with")
    t.true(p.traveler.party_needs_healing(), "so the party is still hurt")


@test("a cure for the wrong status is not reached for")
def _(t):
    p = t.pilot("route30")
    _give(t, p, ("ANTIDOTE", 2))
    _afflict(p, "SLP")
    # An Antidote is poison only. Spending it on sleep would be a press the
    # game refuses, and counting that as a cure would skip the walk.
    t.eq(p.traveler.cure_from_bag(), 0, "nothing in the bag wakes it up")
    t.eq(p.reader.mon(0).status_name, "SLP", "still asleep")
    t.eq(p.reader.carrying("ANTIDOTE"), 2, "and the Antidote is untouched")


@test("a full party of healthy Pokemon reports no heals, not six")
def _(t):
    p = t.pilot("route30")
    _give(t, p, ("POTION", 3))
    # Counting a member that needed nothing as "healed" is how the bag would
    # report success for a party it never touched, and then skip the walk.
    t.eq(p.traveler.heal_from_bag(), 0, "nothing needed doing")
    t.eq(p.reader.carrying("POTION"), 3, "and nothing was spent")


def _into_hurt_battle(t, p, potions: int, hp: int = 12):
    """A wild battle with the lead on `hp`, and `potions` in the bag."""
    gd = t.gamedata
    entries = ((gd.item_id("POTION"), potions),) if potions else ()
    t.give_items(p, ((I.ITEM_POCKET, entries),
                     (I.BALL_POCKET, ((gd.item_id("POKE_BALL"), 5),))))
    t.into_wild_battle(p)
    # Both copies: the battle draws from wBattleMonHP, the party struct is what
    # survives the fight.
    addr = p.session.sym.addr("wBattleMonHP")
    p.session.wb(addr, hp >> 8)
    p.session.wb(addr + 1, hp & 0xFF)
    _hurt(p, hp)


@test("the bag is reached for before the thing on the field faints")
def _(t):
    from pilot.battle import BattleEngine, BattlePolicy
    p = t.pilot("route30", timeout=600)
    _into_hurt_battle(t, p, potions=3)
    eng = BattleEngine(p.session, p.reader, p.control, t.gamedata,
                       BattlePolicy(), log=lambda m: None)
    out = eng.run(max_turns=12, menu_open=True)
    # 12/44 is 27%, under both thresholds. The bag is checked first, so this is
    # a fight that gets healed and won rather than fled.
    t.eq(out.result, "won", "won it")
    t.eq(p.reader.carrying("POTION"), 2, "having spent one Potion")
    t.contains(" ".join(out.notes), "mid-fight", "and said so")


@test("an empty bag still flees, which is what the flee threshold is for")
def _(t):
    from pilot.battle import BattleEngine, BattlePolicy
    p = t.pilot("route30", timeout=600)
    _into_hurt_battle(t, p, potions=0)
    eng = BattleEngine(p.session, p.reader, p.control, t.gamedata,
                       BattlePolicy(), log=lambda m: None)
    out = eng.run(max_turns=12, menu_open=True)
    t.eq(out.result, "fled", "left instead")
    t.contains(" ".join(out.notes), "fleeing", "and said why")


@test("the heal threshold sits above the flee threshold or it is dead code")
def _(t):
    from pilot.battle import BattlePolicy
    pol = BattlePolicy()
    # Checked in this order, so a heal threshold at or below the flee threshold
    # can never fire: the flee returns first. This is the whole reason the
    # default is 0.45 against 0.35.
    t.gt(pol.heal_below, pol.flee_below,
         "the bag is reached for before the exit")


@test("a run told not to spend items does not spend them")
def _(t):
    from pilot.battle import BattleEngine, BattlePolicy
    p = t.pilot("route30", timeout=600)
    _into_hurt_battle(t, p, potions=3)
    eng = BattleEngine(p.session, p.reader, p.control, t.gamedata,
                       BattlePolicy(use_items=False), log=lambda m: None)
    out = eng.run(max_turns=12, menu_open=True)
    t.eq(p.reader.carrying("POTION"), 3, "the bag is untouched")
    t.eq(out.result, "fled", "and it flees the way it used to")


@test("heal --force still makes the round trip with a full bag")
def _(t):
    p = t.pilot("route30")
    _give(t, p, ("POTION", 3))
    _hurt(p, 40)
    where = p.traveler.current_const()
    t.true(p.traveler.heal_up(force_walk=True), "went anyway")
    t.eq(p.traveler.healed_via, "walk", "by walking, as asked")
    t.eq(p.reader.carrying("POTION"), 3, "the bag was not touched")
    t.eq(p.traveler.current_const(), where, "and came back")
