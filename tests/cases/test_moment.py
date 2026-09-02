"""The three commands that act on the situation you are already in.

What these guard is narrower than "does it work". Each of the three has a way of
appearing to succeed while doing nothing: a battle reported won that was
resolved by a stray press rather than by play, a capture reporting a ball count
the bag disagrees with, a heal that reports a round trip it never took. The
assertions below are about the numbers underneath the message.
"""
from pilot.tasks.base import TaskResult

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


# --- the learned-damage guard ----------------------------------------------
# Weakening is a bet that the next swing leaves the target alive. Nothing here
# reads the damage formula, so the only evidence for that bet is what earlier
# swings actually did. These guard the two halves of that: remembering, and
# not picking a move that makes the memory irrelevant.
@test("the guard stops a swing that the biggest hit so far would finish")
def _(t):
    from pilot.tasks.catch import Damage
    mem = Damage()
    t.false(mem.could_finish(1), "knowing nothing, it blocks nothing")

    mem.learn(11)
    t.true(mem.could_finish(11), "exactly enough to finish counts as finishing")
    t.true(mem.could_finish(9), "less than the hit is certainly not safe")
    t.false(mem.could_finish(12), "more than the hit is worth a swing")

    mem.learn(4)
    t.eq(mem.biggest_hit, 11, "a smaller hit does not lower the guard")


@test("a knockout teaches the guard the floor on one swing")
def _(t):
    from pilot.tasks.catch import Damage
    # The measurement that costs a target. It had 11 HP, one swing took all of
    # it: a swing therefore does at least 11, which is what protects the next
    # one. Nothing else in a knockout is recoverable, so this is the whole
    # reason a hunt keeps one guard across every encounter.
    mem = Damage()
    mem.learn(11)
    t.true(mem.could_finish(11), "the next 11 HP target is now protected")


@test("weakening never picks a move whose power byte lies about it")
def _(t):
    p = t.pilot("grass_cyndaquil")
    from pilot.tasks.catch import CatchTask
    task = CatchTask(p.session, p.reader, p.control, p.nav, p.world,
                     p.gamedata, p.traveler, p.saver, p.backups,
                     log=lambda *a, **k: None)

    by_name = {i["name"].replace(" ", "_").upper(): i
               for i in p.gamedata.move_info.values()}

    def info_for(name):
        found = by_name.get(name)
        if found is None:
            t.skip(f"{name} not found in the move table")
        return found

    # Gen 2 stores these as power 0 or 1, so ranking by power puts every one of
    # them ahead of TACKLE -- the move chosen "to avoid a knockout" would have
    # been GUILLOTINE, a one-hit KO. Each of these takes half the bar, your
    # level in HP, or the whole thing.
    lethal = ["GUILLOTINE", "HORN_DRILL", "FISSURE", "SUPER_FANG",
              "SEISMIC_TOSS", "NIGHT_SHADE", "PSYWAVE", "COUNTER"]
    for name in lethal:
        info = info_for(name)
        t.false(task._is_gentle(info), f"{name} (power {info['power']}) is gentle")

    # And it still has to pick *something*, or weakening never happens.
    for name in ["TACKLE", "SCRATCH", "EMBER"]:
        info = info_for(name)
        t.true(task._is_gentle(info), f"{name} should be usable for weakening")

    # Status moves stay out for the opposite reason: ranked by power they are
    # the gentlest thing available and they weaken nothing, forever.
    for name in ["LEER", "GROWL", "SMOKESCREEN"]:
        info = info_for(name)
        t.false(task._is_gentle(info), f"{name} weakens nothing")


@test("a guarded weakening throws instead of swinging at something fragile")
def _(t):
    """The behaviour the guard exists for, in a live battle.

    The documented failure was `capture --weaken-to` knocking out the Pokemon
    it was told to catch: a Lv15 Quilava one-shots a Lv2 route Pokemon even
    with its gentlest move. The guard cannot save the *first* target -- nothing
    knows how hard the party hits until it has hit something -- so this seeds
    what a real hunt would have learned from an earlier encounter, and asks for
    weakening that the old code would certainly have attempted.
    """
    from pilot.tasks.catch import CatchTask, Damage
    p = t.pilot("grass_cyndaquil")
    t.give_balls(p)
    battle = t.into_wild_battle(p)
    if battle is None:
        t.skip("no wild encounter turned up")

    task = CatchTask(p.session, p.reader, p.control, p.nav, p.world,
                     p.gamedata, p.traveler, p.saver, p.backups,
                     log=lambda *a, **k: None)
    poke_ball = p.gamedata.item_id("POKE_BALL")
    hp_before = p.reader.battle().enemy_hp
    balls_before = p.reader.ball_count(poke_ball)

    # weaken_to=0.99 means "weaken anything not already almost dead", so the
    # threshold alone would order a swing. The seeded guard says that swing
    # takes at least 999 HP, which is more than this target has.
    res = TaskResult()
    memory = Damage(biggest_hit=999)
    outcome, used = task._try_capture(battle, poke_ball, "POKE_BALL",
                                      0.99, 5, res, memory=memory)
    t.note(f"{outcome} after {used} ball(s); notes={res.notes}")

    t.ne(outcome, "fainted", "it must not knock out what it was told to catch")
    t.gte(used, 1, "a ball has to actually get thrown")
    t.gt(balls_before - p.reader.ball_count(poke_ball), 0, "balls left the bag")
    # The proof it never swung: something with hp_before HP still had all of it
    # when the ball went out. A single chip here would have emptied that bar.
    t.true(any("stopped weakening" in n for n in res.notes),
           f"the guard should say why it stopped ({res.notes})")
    # And it never swung: if the ball failed and the battle is still on, the
    # target has every point of HP it started with. A single chip here would
    # have emptied that bar instead.
    if p.reader.in_battle():
        now = p.reader.battle()
        if now.ready:
            t.eq(now.enemy_hp, hp_before, "target HP is untouched")


@test("a knockout while weakening is reported as one, and is not thrown away")
def _(t):
    """The bug a live hunt found, which the tests above did not.

    `_chip` reports "ended" when our own attack finishes the battle, because a
    knockout *is* how a battle ends and the enemy struct is cleared before
    anyone could read a zero out of it. Treating that as "work out what
    happened at the top of the loop" turned every knockout into `got_away`:
    twelve encounters, zero balls thrown, seven targets quietly killed and
    misreported -- and the guard learned nothing from any of them, so it
    happened again every time.
    """
    from pilot.tasks.catch import CatchTask, Damage
    p = t.pilot("grass_cyndaquil")
    t.give_balls(p)
    battle = t.into_wild_battle(p)
    if battle is None:
        t.skip("no wild encounter turned up")

    task = CatchTask(p.session, p.reader, p.control, p.nav, p.world,
                     p.gamedata, p.traveler, p.saver, p.backups,
                     log=lambda *a, **k: None)
    res = TaskResult()
    memory = Damage()                     # cold, as a first encounter is
    hp = p.reader.battle().enemy_hp
    poke_ball = p.gamedata.item_id("POKE_BALL")

    # weaken_to=0.99 with a cold guard is the unguardable first swing: a Lv14
    # Quilava's gentlest move takes more than a Lv3 route Pokemon has.
    outcome, used = task._try_capture(battle, poke_ball, "POKE_BALL",
                                      0.99, 5, res, memory=memory)
    t.note(f"{outcome} after {used} ball(s), target had {hp} HP; "
           f"guard learned {memory.biggest_hit}")

    # The test has to be written around what actually happened, but without
    # letting the broken case slip through as a skip. No ball was thrown and
    # the battle is over: with weaken_to at 0.99 the only thing that can have
    # ended it is our own attack, so this is a knockout however it was labelled.
    if used == 0 and not p.reader.in_battle():
        t.eq(outcome, "fainted",
             "a battle our own attack ended is a knockout, not a getaway")
        t.gte(memory.biggest_hit, hp,
              "the knockout has to teach the guard what one swing does")
        return
    # It survived the swing, which is the guard's good case, not this test's.
    t.skip(f"the swing did not end the battle ({outcome}, {used} ball(s))")
