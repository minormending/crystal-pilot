"""Game data parsed out of the disassembly.

These are fast and need no emulator, but they guard a bug that was live for a
while and invisible: pokemon_constants.asm restarts its enum at 1 for the
UNOWN_A..Z forms, so a naive parse overwrites species 1-26 and every Pokemon
from Bulbasaur to Raichu reports as an Unown form.
"""
from ..harness import test


@test("species ids survive the UNOWN enum restart")
def _(t):
    gd = t.gamedata
    for name, sid in [("BULBASAUR", 1), ("PIKACHU", 25), ("RAICHU", 26),
                      ("CHIKORITA", 152), ("BAYLEEF", 153), ("CELEBI", 251)]:
        t.eq(gd.species_id(name), sid, f"{name} id")
        t.eq(gd.species_name(sid), name, f"name of id {sid}")


@test("species lookup is forgiving about how you type a name")
def _(t):
    gd = t.gamedata
    t.eq(gd.species_id("pikachu"), 25, "lowercase")
    t.eq(gd.species_id("Mr. Mime"), 122, "punctuation and spaces")
    t.raises(KeyError, lambda: gd.species_id("pikchu"), "typo should raise")


@test("move data matches the ROM's own table")
def _(t):
    gd = t.gamedata
    t.eq(gd.moves["TACKLE"], 33, "TACKLE id")
    t.eq(gd.moves["EMBER"], 52, "EMBER id")
    tackle, ember = gd.move(33), gd.move(52)
    t.eq(tackle["power"], 35, "TACKLE power")
    t.eq(tackle["accuracy"], 95, "TACKLE accuracy")
    t.eq(ember["power"], 40, "EMBER power")
    # The grind fixture leans on EMBER out-ranking TACKLE.
    t.gt(ember["power"], tackle["power"], "EMBER should out-rank TACKLE")


@test("status moves are not treated as damaging")
def _(t):
    gd = t.gamedata
    t.true(gd.is_damaging(gd.moves["TACKLE"]), "TACKLE is damaging")
    t.false(gd.is_damaging(gd.moves["LEER"]), "LEER is not damaging")
    t.false(gd.is_damaging(gd.moves["GROWL"]), "GROWL is not damaging")


@test("ball item ids resolve")
def _(t):
    gd = t.gamedata
    for name, iid in [("MASTER_BALL", 1), ("ULTRA_BALL", 2),
                      ("GREAT_BALL", 4), ("POKE_BALL", 5)]:
        t.eq(gd.item_id(name), iid, f"{name} id")
    t.eq(gd.item_id("great ball"), 4, "fuzzy item lookup")
    t.eq(gd.item_name(18), "POTION", "id 18 is POTION")


@test("map names and dimensions")
def _(t):
    gd = t.gamedata
    t.eq(gd.map_name(24, 3), "ROUTE_29", "24.3")
    t.eq(gd.map_name(24, 5), "ELMS_LAB", "24.5")
    t.eq(gd.map_pretty(24, 3), "Route 29", "pretty name")
    info = gd.find_map("route_29")
    t.eq((info["group"], info["number"]), (24, 3), "reverse lookup")
    # map_constants gives blocks; a block is 2x2 tiles.
    t.eq((info["width"] * 2, info["height"] * 2), (60, 18), "Route 29 tile size")


@test("shiny rule matches CheckShininess")
def _(t):
    from pilot.symbols import dvs_are_shiny
    # Shiny needs Atk DV bit 1 set and Def/Spd/Spc all exactly 10.
    t.true(dvs_are_shiny(0xAA, 0xAA), "Atk10 Def10 Spd10 Spc10")
    t.true(dvs_are_shiny(0x2A, 0xAA), "Atk2 is the lowest qualifying Attack DV")
    t.false(dvs_are_shiny(0x1A, 0xAA), "Atk1 lacks the mask bit")
    t.false(dvs_are_shiny(0xA9, 0xAA), "Def9 disqualifies")
    t.false(dvs_are_shiny(0xFF, 0xFF), "perfect DVs are not shiny")
