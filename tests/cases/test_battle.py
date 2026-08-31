"""Battle behaviour.

Every test here corresponds to a bug that was live and silent: the task still
reported success while doing the wrong thing. Move selection fell back to
whatever the cursor happened to sit on; fleeing stopped working entirely and
fought instead; declining a new move still replaced one.
"""
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
        c.close_menus(6)          # back out to the battle menu for the next one
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
