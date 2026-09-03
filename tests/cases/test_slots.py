"""Save slots, and the undo point taken before every job.

The claim worth guarding is that a slot restores the *exact* moment rather than
something close to it -- that is the whole difference from the mobile port,
whose emulator will not put a machine state back and so keeps battery saves
instead. If a load here silently returned you to a save point, the desktop would
have quietly become the mobile behaviour and nothing would have said so.
"""

from pilot.slots import ALL_SLOTS, UNDO_SLOT, Slots, describer

from ..harness import test


@test("a slot restores the exact frame, not merely a similar game")
def _(t):
    rom = t.rom_copy("slot-exact")
    p = t.pilot_on(rom, "grass_cyndaquil")
    slots = Slots(t.scratch("slots"), log=lambda *a, **k: None)

    def wram():
        # A window of work RAM wide enough that 600 frames of an overworld
        # cannot leave all of it untouched: coordinates, the frame counters and
        # the RNG all live in here.
        return bytes(p.session.rb(0xD000 + i) for i in range(256))

    slots.save("1", p.session, p.reader,
               describe=describer(p.reader, p.gamedata))
    at_save = wram()

    p.session.tick(600)
    moved = wram()
    t.ne(moved, at_save, "600 frames should change something")

    t.true(slots.load("1", p.session), "the slot should load")
    t.eq(wram(), at_save, "restored memory is byte-identical to the snapshot")


@test("a slot can be taken and restored mid-battle")
def _(t):
    """The thing a battery save cannot do, and the reason states are used here.

    A mobile slot is a save point, so it cannot exist inside a battle at all.
    This asserts the desktop really does better, because "slots" meaning two
    different things across the two halves is only defensible if the stronger
    one actually is stronger.
    """
    rom = t.rom_copy("slot-battle")
    p = t.pilot_on(rom, "grass_cyndaquil")
    slots = Slots(t.scratch("slots"), log=lambda *a, **k: None)
    if t.into_wild_battle(p) is None:
        t.skip("no wild encounter turned up")

    battle = p.reader.battle()
    foe = p.gamedata.species_name(battle.enemy_species)
    slots.save("2", p.session, p.reader)
    t.true(p.reader.in_battle(), "we should be in a battle at save time")

    # Leave the battle, then come back to it.
    p.battle()
    t.false(p.reader.in_battle(), "the battle should be over")

    t.true(slots.load("2", p.session), "the slot should load")
    t.true(p.reader.in_battle(), "restored mid-battle")
    now = p.reader.battle()
    t.eq(p.gamedata.species_name(now.enemy_species), foe,
         "and facing the same Pokemon")


@test("every job marks an undo point, and undo goes back to it")
def _(t):
    rom = t.rom_copy("slot-undo")
    p = t.pilot_on(rom, "grass_cyndaquil")
    # The facade writes into pilot.slots; point it somewhere disposable.
    p.slots = Slots(t.scratch("undo"), log=lambda *a, **k: None)
    t.true(p.slots.info(UNDO_SLOT) is None, "no undo point before any job")

    level_before = p.reader.mon(0).level
    res = p.battle()
    t.note(f"battle: {res.status} - {res.message}")

    info = p.slots.info(UNDO_SLOT)
    t.true(info is not None, "the job should have left an undo point")
    t.eq(info.job, "battle", "and it should say which job")

    back = p.undo()
    t.true(back is not None, "undo should find the point")
    t.eq(p.reader.mon(0).level, level_before,
         "undo returns the party to what it was")


@test("undo reports honestly when no job has run")
def _(t):
    rom = t.rom_copy("slot-noundo")
    p = t.pilot_on(rom, "grass_cyndaquil")
    p.slots = Slots(t.scratch("noundo"), log=lambda *a, **k: None)
    t.true(p.undo() is None, "nothing to undo, and it says so rather than "
                             "restoring something arbitrary")


@test("slots refuse a name that is not one of them")
def _(t):
    slots = Slots(t.scratch("slot-names"), log=lambda *a, **k: None)
    t.raises(ValueError, lambda: slots.check("4"), "slot 4")
    t.raises(ValueError, lambda: slots.check("quicksave"), "a made-up name")
    for good in ALL_SLOTS:
        t.eq(slots.check(good), good, f"{good} is a real slot")


@test("an empty slot loads nothing rather than half a game")
def _(t):
    rom = t.rom_copy("slot-empty")
    p = t.pilot_on(rom, "grass_cyndaquil")
    slots = Slots(t.scratch("empty"), log=lambda *a, **k: None)
    t.false(slots.load("3", p.session), "loading an empty slot returns False")
    t.true(slots.info("3") is None, "and it still reads as empty")
