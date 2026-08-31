"""Task-level behaviour: what the tasks claim versus what actually happened.

The failure mode these guard against is not a crash. It is a task reporting
success while the numbers underneath it are wrong -- balls consumed that were
never counted, trainers recorded as beaten that were never fought.
"""
from ..harness import test


@test("catch reports exactly the number of balls it spent")
def _(t):
    p = t.pilot("grass_cyndaquil")
    t.give_balls(p)
    poke_ball = p.gamedata.item_id("POKE_BALL")
    before = p.reader.ball_count(poke_ball)

    res = p.catch(species="sentret", save_when_done=False, max_encounters=60)
    after = p.reader.ball_count(poke_ball)
    spent = before - after
    reported = res.stats.get("balls_thrown", 0)
    t.note(f"{res.status}: reported {reported}, actually spent {spent}")
    t.eq(res.status, "completed", f"catch outcome ({res.message})")
    # A stray A press used to desync the pack and burn an uncounted ball.
    t.eq(reported, spent, "balls reported vs balls actually consumed")
    t.gte(spent, 1, "at least one ball thrown")


@test("a successful catch adds exactly one Pokemon to the party")
def _(t):
    p = t.pilot("grass_cyndaquil")
    t.give_balls(p)
    before = p.reader.party_count()
    res = p.catch(species="pidgey", save_when_done=False, max_encounters=60)
    t.eq(res.status, "completed", f"catch outcome ({res.message})")
    t.eq(p.reader.party_count(), before + 1, "party size")
    t.eq(p.reader.mon(p.reader.party_count() - 1).species_name, "PIDGEY",
         "the caught species")


@test("catch refuses up front when the bag has no balls")
def _(t):
    p = t.pilot("grass_cyndaquil")
    res = p.catch(species="pidgey", save_when_done=False)
    t.eq(res.status, "blocked", "should refuse, not hunt and then fail")
    t.contains(res.message.lower(), "no poke balls", "reason given")


@test("catch will not silently box a Pokemon when the party is full")
def _(t):
    p = t.pilot("grass_cyndaquil")
    t.give_balls(p)
    # Six party members is the cap; a further catch goes to the PC, which the
    # task does not handle.
    p.session.wb("wPartyCount", 6)
    res = p.catch(species="pidgey", save_when_done=False)
    t.eq(res.status, "blocked", "should refuse with a full party")
    t.contains(res.message.lower(), "party is full", "reason given")


@test("hunt flees the encounters it does not want")
def _(t):
    p = t.pilot("grass_cyndaquil")
    res = p.hunt(species="hoppip", max_encounters=200)
    t.note(f"{res.status}: {res.stats}")
    t.eq(res.status, "completed", f"hunt outcome ({res.message})")
    t.gt(res.stats["encounters"], 1, "should have rejected some encounters")
    # Fighting every rejection burns HP and PP for nothing; that regression
    # showed up as fled=0, fought=N.
    t.gt(res.stats["fled"], 0, "encounters fled")
    t.eq(res.stats["fought"], 0, "encounters fought (should be none)")


@test("hunt says what it did see when the target is not on the route")
def _(t):
    p = t.pilot("grass_cyndaquil")
    res = p.hunt(species="pikachu", max_encounters=25)
    t.eq(res.status, "blocked", "PIKACHU is not on Route 29")
    t.eq(res.stats["encounters"], 25, "should use its whole budget")
    seen = " ".join(res.notes)
    t.contains(seen, "seen:", "should report what turned up instead")


@test("grind reaches the level it was asked for")
def _(t):
    p = t.pilot("grass_cyndaquil")
    start = p.reader.mon(0).level
    res = p.grind(slot=0, to_level=start + 2, save_when_done=False)
    t.note(f"{res.status}: Lv{start} -> Lv{p.reader.mon(0).level}")
    t.eq(res.status, "completed", f"grind outcome ({res.message})")
    t.gte(p.reader.mon(0).level, start + 2, "level reached")


@test("grind refuses a target it cannot identify")
def _(t):
    p = t.pilot("grass_cyndaquil")
    res = p.grind(species="pikachu", to_level=20, save_when_done=False)
    t.eq(res.status, "blocked", "PIKACHU is not in the party")
    t.contains(res.message, "PIKACHU", "names the species asked for")
    t.contains(res.message, "Party:", "lists what is actually in the party")


@test("grind that is already done says so without fighting")
def _(t):
    p = t.pilot("grass_cyndaquil")
    level = p.reader.mon(0).level
    res = p.grind(slot=0, to_level=level - 1, save_when_done=False)
    t.eq(res.status, "completed", "already at the target")
    t.eq(res.stats.get("battles"), 0, "no battles fought")


@test("a timed-out grind keeps the progress it made")
def _(t):
    from pilot.session import Budget
    p = t.pilot("grass_cyndaquil")
    p.session.set_budget(Budget(max_frames=60 * 60 * 60, max_wall_seconds=4))
    start = p.reader.mon(0).level
    res = p.grind(slot=0, to_level=start + 40, save_when_done=False)
    t.note(f"{res.status}: Lv{start} -> Lv{p.reader.mon(0).level} "
           f"after {res.stats.get('battles')} battles")
    t.eq(res.status, "timeout", "should give up rather than run forever")
    t.gte(p.reader.mon(0).level, start, "progress is kept, not rolled back")
    t.false(p.reader.in_battle(), "should leave the battle it was in")


@test("saving commits and survives a reload")
def _(t):
    rom = t.rom_copy("save")
    p = t.pilot_on(rom, "grass_cyndaquil")
    level = p.reader.mon(0).level
    ok, why = p.settle_for_save()
    t.true(ok, f"should be able to save here: {why}")
    t.true(p.save(), "in-game save should commit")
    sav = p.session.sav_path
    t.true(sav.exists(), "a .sav should be written")
    # Gen 2 battery saves are 32 KB; another size would not load elsewhere.
    t.eq(sav.stat().st_size, 32768, ".sav size")

    again = t.pilot_on(rom)
    t.true(again.continue_game(), "the save should load")
    t.eq(again.reader.mon(0).level, level, "level after reloading")


@test("the trainer sweep tells absent apart from unreachable")
def _(t):
    p = t.pilot("route30")
    res = p.trainers(save_when_done=False)
    t.note(f"{res.status}: {res.stats}")
    t.eq(res.stats["trainers"], 3, "Route 30 trainer count")
    # Early on, Joey's object_event is gated behind a story flag and the only
    # corridor north is held by a scripted scene. Both are real, and the report
    # must not call either of them "already beaten".
    t.eq(res.stats["already_beaten"], 0, "nothing should be claimed as beaten")
    t.eq(res.stats["not_present"] + res.stats["unreachable"], 3,
         "every trainer accounted for as absent or unreachable")
    t.gte(res.stats["not_present"], 1, "the story-gated trainer is detected")
