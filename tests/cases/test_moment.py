"""The three commands that act on the situation you are already in.

What these guard is narrower than "does it work". Each of the three has a way of
appearing to succeed while doing nothing: a battle reported won that was
resolved by a stray press rather than by play, a capture reporting a ball count
the bag disagrees with, a heal that reports a round trip it never took. The
assertions below are about the numbers underneath the message.
"""
from ..harness import test


# --- battle ----------------------------------------------------------------
@test("battle refuses when there is no battle, and dispatches nothing")
def _(t):
    p = t.pilot("grass_cyndaquil")
    t.false(p.reader.in_battle(), "the fixture should start out of battle")
    res = p.battle()
    t.eq(res.status, "blocked", f"outcome ({res.message})")
    t.contains(res.message, "not in a battle", "the reason")


@test("battle plays out the battle it is given, and counts the turns it took")
def _(t):
    p = t.pilot("grass_cyndaquil")
    battle = t.into_wild_battle(p)
    if battle is None:
        t.skip("no wild encounter turned up")
    foe = p.gamedata.species_name(battle.enemy_species)

    res = p.battle()
    t.note(f"{foe}: {res.status} - {res.message} {res.stats}")
    t.eq(res.status, "completed", f"outcome ({res.message})")
    t.false(p.reader.in_battle(), "the battle should be over")

    # The one that matters. BattleEngine.run takes menu_open, and by the time a
    # person reaches for this command the battle menu's hook has already fired
    # -- so telling the engine to wait for one waits for an event that will not
    # come again. It still *wins*, because the engine nudges with A when the
    # game goes quiet, but it wins without playing a turn. Measured on this
    # fixture: menu_open=False reported 0 turns, True reported the 1 it took.
    # A zero here means that detection has regressed.
    t.gte(res.stats.get("turns", 0), 1, "turns actually played")


@test("battle names the kind of battle it fought")
def _(t):
    p = t.pilot("grass_cyndaquil")
    if t.into_wild_battle(p) is None:
        t.skip("no wild encounter turned up")
    res = p.battle()
    t.eq(res.stats.get("kind"), "wild", "battle kind")


# --- capture ---------------------------------------------------------------
@test("capture refuses when there is no battle")
def _(t):
    p = t.pilot("grass_cyndaquil")
    t.give_balls(p)
    res = p.capture()
    t.eq(res.status, "blocked", f"outcome ({res.message})")
    t.contains(res.message, "not in a battle", "the reason")


@test("capture refuses with an empty bag, before touching the battle")
def _(t):
    p = t.pilot("grass_cyndaquil")
    if t.into_wild_battle(p) is None:
        t.skip("no wild encounter turned up")
    res = p.capture()
    t.eq(res.status, "blocked", f"outcome ({res.message})")
    t.contains(res.message, "no Poke Balls", "the reason")
    t.true(p.reader.in_battle(), "the battle should be left alone")


@test("capture takes the Pokemon in front of it")
def _(t):
    p = t.pilot("grass_cyndaquil")
    t.give_balls(p)
    battle = t.into_wild_battle(p)
    if battle is None:
        t.skip("no wild encounter turned up")
    foe = p.gamedata.species_name(battle.enemy_species)
    before = p.reader.party_count()

    # No --weaken-to, which is the command's default. Against this fixture it
    # is also the only sane choice: a Lv15 Quilava one-shots a Lv2 route
    # Pokemon with its gentlest move, so chipping first knocks out the thing it
    # was told to catch. See the note in docs/CODE.md -- unlike the mobile
    # port, nothing here remembers how hard it hits.
    res = p.capture(save_when_done=False)
    t.note(f"{foe}: {res.status} - {res.message} {res.stats}")
    if res.status != "completed":
        # A break-out or a knockout is the game being the game, not a defect.
        # The claim being tested is only that it reports what happened.
        t.eq(p.reader.party_count(), before, "nothing gained on a failure")
        t.skip(f"did not catch it: {res.message}")

    t.eq(p.reader.party_count(), before + 1, "party size")
    t.eq(p.reader.mon(p.reader.party_count() - 1).species_name, foe,
         "the caught species is the one that was in front of us")


@test("capture reports exactly the number of balls it spent")
def _(t):
    p = t.pilot("grass_cyndaquil")
    t.give_balls(p)
    if t.into_wild_battle(p) is None:
        t.skip("no wild encounter turned up")
    poke_ball = p.gamedata.item_id("POKE_BALL")
    before = p.reader.ball_count(poke_ball)

    res = p.capture(save_when_done=False)
    spent = before - p.reader.ball_count(poke_ball)
    reported = res.stats.get("balls", 0)
    t.note(f"{res.status}: reported {reported}, actually spent {spent}")
    # Whatever the outcome, the count has to be honest -- a stray A press used
    # to desync the pack and burn a ball nothing counted.
    t.eq(reported, spent, "balls reported vs balls actually consumed")


# --- heal ------------------------------------------------------------------
@test("heal calls an already-healthy party done rather than an error")
def _(t):
    p = t.pilot("grass_cyndaquil")
    t.true(all(m.hp == m.max_hp for m in p.reader.party()),
           "the fixture should start at full health")
    res = p.heal()
    t.eq(res.status, "completed", f"outcome ({res.message})")
    t.eq(res.stats.get("healed"), 0, "nothing needed healing")


@test("heal refuses mid-battle instead of walking")
def _(t):
    p = t.pilot("grass_cyndaquil")
    if t.into_wild_battle(p) is None:
        t.skip("no wild encounter turned up")
    res = p.heal()
    t.eq(res.status, "blocked", f"outcome ({res.message})")
    t.contains(res.message, "battle", "the reason")
    t.true(p.reader.in_battle(), "the battle should be left alone")


@test("heal --force makes the round trip and comes back")
def _(t):
    p = t.pilot("grass_cyndaquil", timeout=300.0)
    origin = p.traveler.current_const()
    where = p.reader.location()

    res = p.heal(force=True)
    t.note(f"{res.status}: {res.message} {res.stats}")
    t.eq(res.status, "completed", f"outcome ({res.message})")
    # The message is not evidence on its own: heal_round_trip returns True only
    # after getting back, so what is checked is that we ended up where we
    # started rather than parked in the Center.
    t.eq(p.traveler.current_const(), origin, "back on the map we left")
    t.eq((p.reader.location().x, p.reader.location().y), (where.x, where.y),
         "back on the tile we left")
