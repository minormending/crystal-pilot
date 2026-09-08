"""Battle behaviour.

Every test here corresponds to a bug that was live and silent: the task still
reported success while doing the wrong thing. Move selection fell back to
whatever the cursor happened to sit on; fleeing stopped working entirely and
fought instead; declining a new move still replaced one.
"""
from pilot import symbols as S
from pilot.battle import BattleEngine, BattlePolicy
from pilot.control import FIGHT, PACK, PKMN

from ..harness import test


def _pp(mon):
    """{move_id: current_pp} for the moves a Pokemon knows."""
    return {m: p & 0x3F for m, p in zip(mon.moves, mon.pp) if m}


def _into_battle(t, p, policy=None):
    """Walk into a wild battle and pump it to the point it wants a decision."""
    if not p.reader.in_battle():
        p.nav.find_grass()
        p.nav.pace_until_battle(max_steps=500)
    t.true(p.reader.in_battle(), "should have walked into a wild battle")
    eng = BattleEngine(p.session, p.reader, p.control, p.gamedata,
                       policy or BattlePolicy(), log=lambda *a, **k: None)
    t.eq(eng.next_decision(), "menu", "battle should reach the action menu")
    return eng


@test("the strongest available move is the one that gets used")
def _(t):
    p = t.pilot("grass_cyndaquil")
    gd = p.gamedata
    ember, tackle = gd.moves["EMBER"], gd.moves["TACKLE"]
    before = _pp(p.reader.mon(0))
    t.gt(before.get(ember, 0), 0, "fixture must have EMBER PP to spend")

    eng = _into_battle(t, p)
    eng.run(target_slot=0, menu_open=True)

    after = _pp(p.reader.mon(0))
    used_ember = before[ember] - after.get(ember, 0)
    used_tackle = before[tackle] - after.get(tackle, 0)
    t.note(f"EMBER {before[ember]}->{after.get(ember)}, "
           f"TACKLE {before[tackle]}->{after.get(tackle)}")
    # EMBER (power 40) out-ranks TACKLE (35). Spending TACKLE means the choice
    # fell through to the cursor's resting position instead of the ranking.
    t.gte(used_ember, 1, "EMBER uses")
    t.eq(used_tackle, 0, "TACKLE uses (should be none)")


@test("status moves are not chosen while a damaging move has PP")
def _(t):
    p = t.pilot("grass_cyndaquil")
    gd = p.gamedata
    before = _pp(p.reader.mon(0))
    eng = _into_battle(t, p)
    eng.run(target_slot=0, menu_open=True)
    after = _pp(p.reader.mon(0))
    for name in ("LEER", "SMOKESCREEN"):
        mid = gd.moves[name]
        if mid in before:
            t.eq(before[mid] - after.get(mid, 0), 0, f"{name} uses")


@test("the best move stays chosen across a run of battles")
def _(t):
    p = t.pilot("grass_cyndaquil")
    gd = p.gamedata
    ember, tackle = gd.moves["EMBER"], gd.moves["TACKLE"]
    before = _pp(p.reader.mon(0))
    fought = 0
    # One battle is not enough: the menu cursor holds its *previous* value when
    # the BattleMenu hook fires, so the "menu looks ready but is not" bug only
    # shows from the second turn onward. Several battles back to back, with no
    # Pokemon Center visit in between to refill PP and hide it.
    for _ in range(6):
        if p.reader.mon(0).fainted:
            break
        eng = _into_battle(t, p)
        eng.run(target_slot=0, menu_open=True)
        fought += 1
    after = _pp(p.reader.mon(0))
    t.note(f"{fought} battles; EMBER {before[ember]}->{after.get(ember)}, "
           f"TACKLE {before[tackle]}->{after.get(tackle)}")
    t.gte(fought, 3, "battles fought")
    t.gte(before[ember] - after.get(ember, 0), 3, "EMBER uses")
    t.eq(before[tackle] - after.get(tackle, 0), 0, "TACKLE uses (should be none)")


@test("the battle menu cursor lands on the action asked for")
def _(t):
    p = t.pilot("grass_cyndaquil")
    s, c = p.session, p.control
    eng = _into_battle(t, p)
    # The menu is a 2x2 grid that wraps and remembers where it was left, so each
    # action has to be reachable from the previous one -- which is exactly what
    # "press up twice to normalise" got wrong.
    for action, label in [(FIGHT, "FIGHT"), (PKMN, "PKMN"),
                          (PACK, "PACK"), (FIGHT, "FIGHT again")]:
        t.true(c._await_menu_cursor(), f"battle menu should be up before {label}")
        t.true(c.choose_battle_action(action), f"cursor should reach {label}")
        s.tick(20)
        t.eq(s.rb("wBattleMenuCursorPosition"), action,
             f"committed choice for {label}")
        # `back_out`, not `close_menus`: inside a battle wWindowStackSize
        # reads 0 for move select, so the checked version returns having
        # pressed nothing and the next action is chosen from the wrong screen.
        c.back_out(6)             # back out to the battle menu for the next one
    eng.p.always_flee = True
    eng.run(target_slot=None, max_turns=25)


@test("an unwanted wild battle is fled, not fought")
def _(t):
    p = t.pilot("grass_cyndaquil")
    before_hp = p.reader.mon(0).hp
    eng = _into_battle(t, p, BattlePolicy(always_flee=True))
    # menu_open matters: the BattleMenu hook fires once per turn, so an engine
    # that pumps again never sees it and falls back to nudging A, which picks
    # FIGHT. That regression made hunt fight every rejected encounter.
    out = eng.run(target_slot=None, max_turns=25, menu_open=True)
    t.note(f"result={out.result} turns={out.turns} hp {before_hp}->{p.reader.mon(0).hp}")
    t.eq(out.result, "fled", "escaping a wild battle")


@test("declining a new move keeps the moveset intact")
def _(t):
    p = t.pilot("pre_learn_chikorita")
    before = [m for m in p.reader.mon(0).moves if m]
    res = p.grind(species="chikorita", to_level=15, save_when_done=False,
                  learn_new_moves=False)
    after = [m for m in p.reader.mon(0).moves if m]
    names = lambda ms: [p.gamedata.move_name(m) for m in ms]
    t.note(f"{names(before)} -> {names(after)}")
    t.true(res.ok or res.status == "timeout", f"grind status: {res.status}")
    t.eq(after, before, "moveset after a level-up that offered a new move")


@test("--learn-moves does accept the new move")
def _(t):
    p = t.pilot("pre_learn_chikorita")
    before = [m for m in p.reader.mon(0).moves if m]
    p.grind(species="chikorita", to_level=15, save_when_done=False,
            learn_new_moves=True)
    after = [m for m in p.reader.mon(0).moves if m]
    names = lambda ms: [p.gamedata.move_name(m) for m in ms]
    t.note(f"{names(before)} -> {names(after)}")
    t.ne(after, before, "moveset should change when learning is allowed")
    t.eq(len(after), 4, "still four moves")


@test("--no-evolve actually cancels the evolution")
def _(t):
    p = t.pilot("pre_learn_chikorita")
    species_before = p.reader.mon(0).species
    # Chikorita evolves at 16; the cancel needs B *held* across the animation.
    res = p.grind(species="chikorita", to_level=17, save_when_done=False,
                  allow_evolution=False)
    mon = p.reader.mon(0)
    t.note(f"{p.gamedata.species_name(species_before)} -> "
           f"{mon.species_name} Lv{mon.level} ({res.status})")
    t.gte(mon.level, 16, "should have passed the evolution level")
    t.eq(mon.species, species_before, "species after cancelling evolution")


@test("evolution is allowed by default")
def _(t):
    p = t.pilot("pre_learn_chikorita")
    species_before = p.reader.mon(0).species
    p.grind(species="chikorita", to_level=17, save_when_done=False,
            allow_evolution=True)
    mon = p.reader.mon(0)
    t.note(f"{p.gamedata.species_name(species_before)} -> {mon.species_name}")
    t.ne(mon.species, species_before, "species should change when allowed")
    # The target is tracked by party slot, so the task keeps training the same
    # Pokemon through the species change.
    t.eq(mon.slot, 0, "still slot 1")


@test("blacking out is reported as a loss, not as a win")
def _(t):
    p = t.pilot("route30", timeout=600)
    t.give_balls(p)
    t.into_wild_battle(p)
    s = p.session
    # A Lv14 lead on 1HP against something it cannot one-shot. Without the
    # strong enemy the fixture's Quilava wins before it can be knocked out,
    # which is why this path had never been exercised.
    s.wb("wEnemyMonLevel", 60)
    for sym in ("wEnemyMonHP", "wEnemyMonMaxHP"):
        addr = s.sym.addr(sym)
        s.wb(addr, 300 >> 8)
        s.wb(addr + 1, 300 & 0xFF)
    hp = s.sym.addr("wBattleMonHP")
    s.wb(hp, 0)
    s.wb(hp + 1, 1)
    base = s.sym.addr("wPartyMon1")
    s.wb(base + S.MON_HP, 0)
    s.wb(base + S.MON_HP + 1, 1)

    out = BattleEngine(s, p.reader, p.control, p.gamedata,
                       BattlePolicy(use_items=False, flee_below=0.0),
                       log=lambda m: None).run(max_turns=20, menu_open=True)
    # Blacking out *ends* the battle, so wBattleMode clears on the same tick
    # LostBattle fires -- and an "is it over?" test that runs before the events
    # are scanned returns "ended", which `run` maps to won. This is the worst
    # available way to be wrong: the white-out heals the party and moves it to
    # a Pokemon Center, so even the HP afterwards looks like a win.
    t.eq(out.result, "lost", "the party was wiped")
    t.contains(" ".join(out.notes), "blacked out", "and it says so")


@test("a grind that blacks out says that, not that it lost the grass")
def _(t):
    # The report before this read "wandered off the route and could not
    # return", because a white-out moves the player to a Pokemon Center and
    # `_ensure_grass` then fails. True, and not the reason -- and with the
    # party healed by the white-out, nothing else in the result gave it away.
    import pilot.battle as B
    from pilot.battle import BattleOutcome
    p = t.pilot("route30", timeout=300)
    real = B.BattleEngine.run

    def wiped(self, *a, **k):
        out = BattleOutcome()
        out.result = "lost"
        out.note("blacked out")
        return out

    B.BattleEngine.run = wiped
    try:
        res = p.grind(slot=0, to_level=40, save_when_done=False,
                      on_timeout="none")
    finally:
        B.BattleEngine.run = real
    t.contains(res.message, "blacked out", f"says what happened: {res.message}")
    t.eq(res.stats.get("lost"), 1, "and counts it")
    t.eq(res.stats.get("won", 0), 0, "without counting it as a win")


def _doomed_lead(p):
    """Put the lead on 1 HP against something it cannot beat.

    The fixture's Quilava wins an ordinary wild battle before anything can
    knock it out, which is why every path that runs *after* a faint had never
    been exercised. Same setup the blackout test uses, and for the same reason.
    """
    s = p.session
    s.wb("wEnemyMonLevel", 60)
    for sym in ("wEnemyMonHP", "wEnemyMonMaxHP"):
        addr = s.sym.addr(sym)
        s.wb(addr, 300 >> 8)
        s.wb(addr + 1, 300 & 0xFF)
    hp = s.sym.addr("wBattleMonHP")
    s.wb(hp, 0)
    s.wb(hp + 1, 1)
    base = s.sym.addr("wPartyMon1")
    s.wb(base + S.MON_HP, 0)
    s.wb(base + S.MON_HP + 1, 1)


@test("a switch sends out the party slot that was asked for")
def _(t):
    from pilot.battle import BattleOutcome
    # Unreachable until now: every fixture has a party of one, so there was
    # nothing to switch to and `_switch_to` had never run. It used to press UP
    # six times to normalise the cursor and then DOWN, on a list that wraps.
    p = t.pilot("route30", timeout=600)
    slot = t.clone_lead(p)
    eng = _into_battle(t, p)
    t.eq(p.reader.battle().active_slot, 0, "the lead is out to start with")
    out = BattleOutcome()
    eng._switch_to(slot, out)
    t.note(f"active slot after the switch: {p.reader.battle().active_slot}")
    t.eq(p.reader.battle().active_slot, slot,
         f"slot {slot} is what came out ({'; '.join(out.notes) or 'no notes'})")


@test("a lead that faints is replaced by the next member that can fight")
def _(t):
    p = t.pilot("route30", timeout=900)
    slot = t.clone_lead(p)
    eng = _into_battle(t, p, BattlePolicy(use_items=False, flee_below=0.0,
                                          switch_to_target=False))
    _doomed_lead(p)
    # Spied rather than inferred from the result. How this battle *ends* is up
    # to a Lv14 Quilava against a Lv60 with 300 HP and is not the claim; the
    # claim is that the faint asked for a replacement, that the cursor reached
    # the row, and that the mon which came out is the one that was picked.
    from pilot.battle import BattleEngine
    real = BattleEngine._send_next_mon
    seen = []

    def spy(self, out):
        got = real(self, out)
        seen.append((got, self.r.battle().active_slot))
        return got

    BattleEngine._send_next_mon = spy
    try:
        out = eng.run(max_turns=30, menu_open=True)
    finally:
        BattleEngine._send_next_mon = real

    notes = "; ".join(out.notes)
    t.note(f"result={out.result} turns={out.turns} replacements={seen}")
    t.gte(len(seen), 1, f"the faint asked for a replacement ({out.result}: {notes})")
    t.eq(seen[0][0], "sent", f"and it went out ({notes})")
    t.eq(seen[0][1], slot, f"and it is slot {slot} that came out ({notes})")


@test("a party list the pilot cannot drive is not recorded as a blackout")
def _(t):
    # Both of `_send_next_mon`'s failures used to be one `False`, and the caller
    # turned that into `result = "lost"`. So a party list that would not draw,
    # or a cursor that would not reach the slot, wrote a defeat into the log
    # that never happened -- with the party still standing and the battle still
    # running. The same shape of mistake as a win and a wipe sharing a path.
    p = t.pilot("route30", timeout=300)
    eng = _into_battle(t, p)

    def stuck(out):
        out.note("the party list never drew; not pressing anything into it")
        return "stuck"

    eng._send_next_mon = stuck
    eng.next_decision = lambda: "replace"
    out = eng.run(max_turns=5, menu_open=False)
    t.ne(out.result, "lost", "a menu that will not drive is not a defeat")
    t.eq(out.result, "timeout", f"it is the pilot giving up ({out.result})")
    t.contains("; ".join(out.notes), "never drew", "and the note says why")


def _replacement_with(t, p, break_it):
    """Run a battle whose lead faints, sabotaging one control call at the faint.

    `break_it(control)` is applied at the moment `_send_next_mon` is entered,
    so the rest of the battle drives normally and only the replacement menu
    fails -- which is the only way to reach code that exists for a menu that
    does not behave. -> (the list of returns seen, the outcome).
    """
    from pilot.battle import BattleEngine
    eng = _into_battle(t, p, BattlePolicy(use_items=False, flee_below=0.0,
                                          switch_to_target=False))
    _doomed_lead(p)
    real = BattleEngine._send_next_mon
    seen = []

    def sabotage(self, out):
        break_it(self.c)
        got = real(self, out)
        seen.append(got)
        return got

    BattleEngine._send_next_mon = sabotage
    try:
        return seen, eng.run(max_turns=30, menu_open=True)
    finally:
        BattleEngine._send_next_mon = real


@test("a party list that never draws is reported as stuck, not as a defeat")
def _(t):
    p = t.pilot("route30", timeout=900)
    t.clone_lead(p)

    def never_draws(c):
        c._await_menu_cursor = lambda *a, **k: False

    seen, out = _replacement_with(t, p, never_draws)
    t.note(f"returns={seen} result={out.result}")
    t.eq(seen, ["stuck"], "the pilot said it could not drive the list")
    # The party still has a healthy member and the battle is still running.
    # Calling that a blackout is the thing this guards.
    t.ne(out.result, "lost", "a list that will not draw is not a defeat")
    t.contains("; ".join(out.notes), "never drew", "and the note says why")


@test("a cursor that never reaches the slot stops the switch before the A press")
def _(t):
    p = t.pilot("route30", timeout=900)
    t.clone_lead(p)

    def cursor_stuck(c):
        c.drive_menu_cursor = lambda *a, **k: False

    seen, out = _replacement_with(t, p, cursor_stuck)
    t.note(f"returns={seen} result={out.result}")
    # Not "sent". This function used to hold its own copy of the cursor loop
    # with no check at the end, so a cursor that never arrived still got an A
    # -- sending out whatever was highlighted, on a list that wraps.
    t.eq(seen, ["stuck"], "the switch stopped rather than pressing A blind")
    t.contains("; ".join(out.notes), "could not reach party slot",
               "and it names the slot it wanted")
    t.ne(out.result, "lost", "and it is still not a defeat")
