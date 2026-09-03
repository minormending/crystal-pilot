"""Decisions, tested without a cartridge.

These run everywhere -- including CI, which has the disassembly but no ROM.
That was the gap: 88 of 108 tests skipped there, and the ones that ran only read
data files. The decisions are the part that has actually been wrong.

The fakes are in tests/fake.py; the code under test is the real thing.
"""
from pilot.state import GameStateReader
from pilot.slots import SlotInfo, Slots
from pilot.tasks.catch import UNGENTLE_EFFECTS, Damage

from ..fake import FakeSession, fake_symbols, mark_saved, world
from ..harness import test

CYNDAQUIL, PIDGEY = 155, 16
POKE_BALL, GREAT_BALL = 5, 4


def reader(t, **fields):
    """The real reader over a fake session.

    `gamedata` is the real thing: CI has the disassembly cloned, so parsing the
    game's own data files costs nothing and is worth testing against rather
    than stubbing.
    """
    sym = fake_symbols()
    s = FakeSession(sym, world(sym, **fields))
    return GameStateReader(s, t.gamedata), s, sym


# --- reading the game -------------------------------------------------------
@test("a party reads back with levels, HP and moves intact")
def _(t):
    r, _s, _sym = reader(t, party=[
        {"species": CYNDAQUIL, "level": 14, "hp": 19, "max_hp": 44,
         "moves": [33, 43, 0, 0], "pp": [35, 30, 0, 0]},
        {"species": PIDGEY, "level": 3, "hp": 0, "max_hp": 15},
    ])
    t.eq(r.party_count(), 2, "party size")
    lead = r.mon(0)
    t.eq(lead.level, 14, "lead level")
    t.eq(lead.hp, 19, "lead HP")
    t.eq(lead.max_hp, 44, "lead max HP")
    t.eq(r.mon(1).hp, 0, "a fainted second")


@test("PP is masked to its low six bits, so PP Ups are not read as extra PP")
def _(t):
    # 0xC0 is two PP Ups with no PP left. Reading the byte whole says 192.
    r, _s, _sym = reader(t, party=[{"moves": [33, 0, 0, 0], "pp": [0xC0, 0, 0, 0]}])
    t.eq(r.mon(0).pp[0] & 0x3F, 0, "PP with the PP Up bits stripped")


@test("in_battle and the battle kind come from the mode byte")
def _(t):
    r, _s, _sym = reader(t, battle_mode=0)
    t.false(r.in_battle(), "mode 0 is no battle")

    # Both sides have to be populated for `ready`, which is the point of it:
    # the structs are filled in over several frames, so a half-read battle is a
    # real state and reading it would give plausible rubbish.
    r, _s, _sym = reader(t, battle_mode=1,
                         enemy={"species": PIDGEY, "level": 3, "hp": 15, "max_hp": 15},
                         active={"hp": 40, "max_hp": 44, "level": 14})
    t.true(r.in_battle(), "mode 1 is a wild battle")
    b = r.battle()
    t.true(b.ready, "and the struct is readable")
    t.false(b.is_trainer, "not a trainer")
    t.eq(b.enemy_level, 3, "enemy level")
    t.eq(b.enemy_hp, 15, "enemy HP")

    r, _s, _sym = reader(t, battle_mode=2, enemy={"hp": 20, "max_hp": 20},
                         active={"hp": 40, "max_hp": 44})
    t.true(r.battle().is_trainer, "mode 2 is a trainer")

    # And a battle whose structs are not filled in yet reports itself unready
    # rather than handing back zeros as though they were real.
    half = reader(t, battle_mode=1, enemy={"hp": 15, "max_hp": 15})[0].battle()
    t.true(half.in_battle, "the mode says we are in a battle")
    t.false(half.ready, "but it will not pretend the structs are readable")


@test("the ball pocket counts kinds, not balls")
def _(t):
    # wNumBalls is how many *kinds* are carried; the quantity is the second byte.
    r, _s, _sym = reader(t, balls=[(POKE_BALL, 40), (GREAT_BALL, 10)])
    t.eq(r.ball_count(POKE_BALL), 40, "forty Poke Balls")
    t.eq(r.ball_count(GREAT_BALL), 10, "ten Great Balls")
    t.eq(r.ball_count(1), 0, "and none of a kind not carried")


@test("location reads the map it is on")
def _(t):
    r, _s, _sym = reader(t, map=(24, 3), pos=(53, 12))
    loc = r.location()
    t.eq(loc.key, (24, 3), "map key")
    t.eq((loc.x, loc.y), (53, 12), "position")


# --- the learned-damage guard ----------------------------------------------
@test("the guard blocks a swing the biggest hit so far would finish")
def _(t):
    mem = Damage()
    t.false(mem.could_finish(1), "knowing nothing, it blocks nothing")
    mem.learn(11)
    t.true(mem.could_finish(11), "exactly enough counts as finishing")
    t.true(mem.could_finish(9), "less than the hit is certainly not safe")
    t.false(mem.could_finish(12), "more than the hit is worth a swing")
    mem.learn(4)
    t.eq(mem.biggest_hit, 11, "a smaller hit does not lower the guard")


@test("the moves whose power byte lies are excluded by effect")
def _(t):
    # Gen 2 stores these at power 0 or 1 because their damage is computed, so
    # ranking by power puts every one ahead of TACKLE. Asked for the gentlest
    # damaging move, the obvious implementation returns a one-hit KO.
    for effect in ("EFFECT_OHKO", "EFFECT_SUPER_FANG", "EFFECT_LEVEL_DAMAGE",
                   "EFFECT_PSYWAVE", "EFFECT_COUNTER", "EFFECT_MIRROR_COAT"):
        t.true(effect in UNGENTLE_EFFECTS, f"{effect} must not be weakened with")
    t.false("EFFECT_NORMAL_HIT" in UNGENTLE_EFFECTS, "a normal hit is fine")
    # Not the same as "fixed damage": this one stores its damage as its power.
    t.false("EFFECT_STATIC_DAMAGE" in UNGENTLE_EFFECTS, "DRAGON RAGE stays in")


# --- slots, without a cartridge --------------------------------------------
@test("a slot describes itself, and an empty one says so")
def _(t):
    slots = Slots(t.scratch("slot-desc"), log=lambda *a, **k: None)
    t.true(slots.info("1") is None, "nothing kept yet")
    info = SlotInfo(slot="2", when=0, where="Route 29", lead="CYNDAQUIL Lv5",
                    job="grind")
    said = info.describe()
    t.contains(said, "Route 29", "where")
    t.contains(said, "CYNDAQUIL Lv5", "who")
    t.contains(said, "before grind", "and what it was before")


@test("slots refuse a name that is not one of them")
def _(t):
    slots = Slots(t.scratch("slot-names"), log=lambda *a, **k: None)
    t.raises(ValueError, lambda: slots.check("4"), "slot 4")
    t.raises(ValueError, lambda: slots.check("quicksave"), "a made-up name")
    for good in ("1", "2", "3", "undo"):
        t.eq(slots.check(good), good, f"{good} is a real slot")


@test("an empty slot loads nothing rather than half a game")
def _(t):
    sym = fake_symbols()
    session = FakeSession(sym, world(sym))
    slots = Slots(t.scratch("slot-empty"), log=lambda *a, **k: None)
    t.false(slots.load("3", session), "loading an empty slot returns False")


# --- the save-validity markers ---------------------------------------------
@test("a battery with no save in it is not mistaken for one that has")
def _(t):
    sym = fake_symbols()
    blank = bytearray(32768)
    marked = mark_saved(bytearray(32768), sym)
    bank, addr = sym.banked("sCheckValue1")
    at1 = bank * 0x2000 + (addr - 0xA000)
    bank, addr = sym.banked("sCheckValue2")
    at2 = bank * 0x2000 + (addr - 0xA000)
    t.eq(blank[at1], 0, "a blank battery has no marker")
    t.eq(marked[at1], 99, "a saved one has the first")
    t.eq(marked[at2], 127, "and the second")


# --- the capture loop, with the button-pressing stubbed out ----------------
# What is under test is the decision made between the presses, which is where
# every capture bug has been. Same approach as the mobile port's tests.
def capture_task(t, *, enemy_hp=20, enemy_max=20, party=1, balls=10,
                 chip="ok", throw=True, watch=None):
    from pilot.tasks.catch import CatchTask

    sym = fake_symbols()
    mons = [{"species": CYNDAQUIL, "level": 14, "hp": 40, "max_hp": 44,
             "moves": [33, 0, 0, 0], "pp": [35, 0, 0, 0]} for _ in range(party)]
    live = {"hp": enemy_hp}

    def wram():
        return world(sym, battle_mode=1, party=mons,
                     balls=[(POKE_BALL, balls)] if balls else [],
                     enemy={"species": PIDGEY, "level": 3, "hp": live["hp"],
                            "max_hp": enemy_max},
                     active={"hp": 40, "max_hp": 44, "level": 14},
                     menu_items=34, menu_top=12, menu=(1, 1))

    session = FakeSession(sym, wram())
    r = GameStateReader(session, t.gamedata)

    class _Control:
        def __init__(self):
            self.thrown = 0

        def throw_ball(self, ball_id):
            self.thrown += 1
            return throw

        def close_menus(self, times=6):
            pass

    task = CatchTask.__new__(CatchTask)
    task.s, task.r, task.c = session, r, _Control()
    task.gd, task.log = t.gamedata, lambda *a, **k: None
    task.calls = []

    def _chip(res):
        task.calls.append("chip")
        outcome = chip(live) if callable(chip) else chip
        session.wram = wram()
        return outcome

    def _watch(before_party):
        task.calls.append("watch")
        return watch(task) if callable(watch) else watch

    task._chip = _chip
    task._watch_throw = _watch
    # Keep the live snapshot in step with whatever a stub changed.
    task._refresh = lambda: setattr(session, "wram", wram())
    return task, live, session


@test("the guard refuses a swing the biggest hit so far would finish")
def _(t):
    # 13 of 14 HP, so it is well above the threshold and the threshold alone
    # would order a swing. A swing has been seen to take 15, which is more than
    # it has.
    task, _live, _s = capture_task(t, enemy_hp=13, enemy_max=14, watch="got_away")
    res = _Result()
    outcome, used = task._try_capture(None, POKE_BALL, "POKE_BALL", 0.5, 3,
                                      res, memory=Damage(biggest_hit=15))
    t.eq(task.calls.count("chip"), 0, "no swing was taken")
    t.gte(task.c.thrown, 1, "a ball was thrown instead")
    t.true(any("stopped weakening" in n for n in res.notes),
           f"and it says why ({res.notes})")


@test("a knockout while weakening is reported as one and teaches the guard")
def _(t):
    task, _live, _s = capture_task(t, enemy_hp=30, enemy_max=40,
                                   chip=lambda live: "fainted")
    mem = Damage()
    outcome, used = task._try_capture(None, POKE_BALL, "POKE_BALL", 0.5, 3,
                                      _Result(), memory=mem)
    t.eq(outcome, "fainted", "the outcome names what happened")
    t.eq(used, 0, "no ball was spent on it")
    t.gte(mem.biggest_hit, 30, "the knockout is itself a measurement")


@test("our own attack ending the battle is a knockout, not a getaway")
def _(t):
    # _chip reports "ended" when our attack finishes the battle, because the
    # enemy struct is cleared before a zero can be read out of it. Treating
    # that as "work it out at the top of the loop" turned every knockout into
    # got_away, and the guard learned nothing from any of them.
    task, _live, _s = capture_task(t, enemy_hp=30, enemy_max=40, chip="ended")
    mem = Damage()
    outcome, used = task._try_capture(None, POKE_BALL, "POKE_BALL", 0.5, 3,
                                      _Result(), memory=mem)
    t.eq(outcome, "fainted", "lead still standing means the target went down")
    t.gte(mem.biggest_hit, 30, "and it still learns from it")


@test("weakening is bounded, so a move that keeps missing cannot loop forever")
def _(t):
    # Weakening spends no ball, so without a bound a missing move loops until
    # the session budget dies with the ball budget never moving.
    #
    # The stub raises past the ceiling rather than letting the test assert
    # afterwards. Written the obvious way, removing the bound made this hang
    # instead of fail -- and a test that can only hang is worse than one that
    # passes wrongly, because CI just times out with nothing to read.
    from pilot.tasks.catch import MAX_CHIPS

    # The ceiling is a literal, deliberately not MAX_CHIPS. Deriving it from
    # the constant under test made the stub useless the moment that constant
    # was the thing broken: raising the bound raised the guard rail with it and
    # the test hung again. A guard rail cannot be the thing it guards.
    CEILING = 50

    class Unbounded(Exception):
        pass

    def endless(live):
        if len([c for c in task.calls if c == "chip"]) > CEILING:
            raise Unbounded(f"weakened more than {CEILING} times")
        return "ok"                       # never lands a hit, never faints it

    task, _live, _s = capture_task(t, enemy_hp=40, enemy_max=40,
                                   chip=endless, watch="got_away")
    try:
        task._try_capture(None, POKE_BALL, "POKE_BALL", 0.5, 2, _Result(),
                          memory=Damage())
    except Unbounded as e:
        raise AssertionError(str(e)) from None
    t.true(task.calls.count("chip") <= MAX_CHIPS,
           f"at most {MAX_CHIPS} swings, took {task.calls.count('chip')}")


class _Result:
    """Stands in for TaskResult, collecting notes so a test can read them."""

    def __init__(self):
        self.notes = []

    def note(self, msg):
        self.notes.append(msg)
