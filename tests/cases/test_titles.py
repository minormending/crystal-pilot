"""Which cartridge this is, and the contract a profile has to keep.

Most of this needs no emulator: the contract is a check on a hand-written file
and the point is that it runs at load rather than when somebody presses A at a
wall.
"""
from pilot import titles
from pilot.titles.contract import EDGES, PUSH_DIRECTIONS, validate_title
from pilot.titles.crystal import crystal
from pilot.titles.generic import generic

from ..harness import test


@test("the profiles that ship pass their own contract")
def _(t):
    for title in titles.TITLES:
        t.eq(validate_title(title), [], f"{title.id} is clean")


@test("push directions and map edges are different vocabularies")
def _(t):
    # Found by the checker on its first run, in the profile written beside it:
    # `first_route.edge` said "west" while the contract wanted "left", so
    # Crystal failed validation and the pilot silently fell through to generic
    # -- header matched, symbol matched, profile discarded.
    t.eq(crystal.first_route["edge"], "west", "an edge is a compass bearing")
    t.contains(EDGES, "west", "and the contract agrees")
    t.false("west" in PUSH_DIRECTIONS, "a push is not a bearing")
    for leg in crystal.intro_legs:
        t.contains(PUSH_DIRECTIONS, leg["push"], f"{leg['on']} pushes a button")


@test("a profile with a mistyped field is rejected, not half-used")
def _(t):
    class Broken:
        id = "broken"
        can_bootstrap = True
        starters = {"Cyndaquil": {"ball": [6, 4]}}   # capitalised, list, no species
        intro_legs = ({"on": "", "warp": (1,), "push": "north"},)
        lab = {"map": 24, "stand": None, "ball_row": "4"}
        first_route = {"from_map": "X", "warp": (0, 0), "edge": "left",
                       "route": "Y"}

        @staticmethod
        def matches(header, symbols):
            return True

    bad = validate_title(Broken())
    said = " ".join(bad)
    t.gt(len(bad), 5, "every one of these is caught")
    t.contains(said, "lower-case", "the capitalised starter key")
    t.contains(said, "species", "the missing species name")
    t.contains(said, "(x, y) tile", "the malformed tiles")
    t.contains(said, "compass bearing", "the edge given as a button")
    t.contains(said, "button", "the push given as a bearing")


@test("a profile claiming it can start a game must carry the whole opening")
def _(t):
    class Liar:
        id = "liar"
        can_bootstrap = True
        starters = {}
        intro_legs = ()
        lab = None
        first_route = None

        @staticmethod
        def matches(header, symbols):
            return True

    # The check that would otherwise wait until somebody ran `bootstrap` and
    # got halfway through the intro.
    said = " ".join(validate_title(Liar()))
    t.contains(said, "can_bootstrap", "it is called out")
    t.contains(said, "starters", "and told what is missing")


@test("an object that is not a profile at all says so")
def _(t):
    t.eq(validate_title(None), ["a title must be an object"], "None")
    t.gt(len(validate_title(object())), 2, "and something shapeless")


@test("the generic profile matches anything and admits what it cannot do")
def _(t):
    t.true(generic.matches("ANYTHING AT ALL", frozenset()), "matches anything")
    t.false(generic.can_bootstrap, "and does not claim to start a game")
    # Last in the registry, so it is a fallback rather than a guess.
    t.eq(titles.TITLES[-1].id, "generic", "generic is last")


@test("naming a profile by hand goes through the same gate")
def _(t):
    quiet = []
    t.eq(titles.title_by_id("crystal", log=quiet.append).id, "crystal", "by id")
    # Naming one by hand is the path a profile author would use to try their
    # own file, so putting it past the check would be the worst place to skip it.
    t.eq(titles.title_by_id("nope", log=quiet.append), None, "an unknown id")
    t.contains(" ".join(quiet), "no profile called", "and it says so")


@test("this cartridge is recognised as Crystal, header and symbol both")
def _(t):
    p = t.pilot("route30")
    t.eq(titles.header_title(p.session), "PM_CRYSTAL", "the header")
    # NUL-terminated in practice: the sixteen header bytes are "PM_CRYSTAL"
    # then a manufacturer code, so reading all of them gives "PM_CRYSTALBYTE"
    # and no profile matches.
    t.false("BYTE" in titles.header_title(p.session), "stops at the NUL")
    t.eq(p.title.id, "crystal", "and the profile picked")
    t.true(p.title.can_bootstrap, "which can start a new game")


@test("a header alone is not enough to claim a cartridge")
def _(t):
    # A pokecrystal hack routinely keeps PM_CRYSTAL in its header, so matching
    # on that alone claims every hack as Crystal and then walks into a lab that
    # has been moved.
    t.false(crystal.matches("PM_CRYSTAL", frozenset()),
            "the header without the symbol is refused")
    t.false(crystal.matches("SOMEHACK", frozenset({"JohtoGrassWildMons"})),
            "and the symbol without the header")
    t.true(crystal.matches("PM_CRYSTAL", frozenset({"JohtoGrassWildMons"})),
           "both together")


@test("the bootstrap refuses on an undescribed cartridge, before pressing A")
def _(t):
    from pilot.tasks.bootstrap import Bootstrap, BootstrapError
    p = t.pilot("route30")
    b = Bootstrap(p.session, p.reader, p.control, p.nav, generic,
                  log=lambda *a: None)
    # Four hundred taps of A followed by "the intro never finished" is the
    # alternative, and it blames the ROM for the profile's silence.
    t.raises(BootstrapError, b.run_intro, "the intro refuses")
    t.raises(BootstrapError, b.walk_to_lab, "so does the walk")
    t.raises(BootstrapError, lambda: b.get_starter("cyndaquil"),
             "and the starter")


@test("a new game still starts, driven entirely from the profile")
def _(t):
    # There was no test for this path at all, which is an odd gap for the one
    # task that cannot be checked any other way -- and a bad gap to still have
    # while moving every coordinate it uses into another file. A private ROM
    # copy has no .sav, so this is a genuinely fresh game.
    rom = t.rom_copy("bootstrap")
    p = t.pilot_on(rom, timeout=900)
    t.eq(p.title.id, "crystal", "recognised before anything is pressed")
    p.bootstrap(starter="cyndaquil", to_route=True)
    party = p.reader.party()
    t.eq(len(party), 1, "one Pokemon")
    t.eq(party[0].species_name, "CYNDAQUIL", "the one asked for")
    # The name is the tell that the NAME menu was taken rather than mashed
    # through: mashing A opens the letter grid and spells AAAAA.
    t.ne(p.reader.player_name(), "AAAAA", "the letter grid was not mashed")
    t.gt(len(p.reader.player_name()), 0, "and a name was taken")
    t.eq(p.traveler.current_const(), "ROUTE_29", "and it reached the grass")
    t.true(p.reader.on_grass(), "standing on it")


@test("the coordinates the bootstrap used to hardcode still describe Crystal")
def _(t):
    # These moved out of tasks/bootstrap.py into the profile. They are facts
    # about Crystal's script rather than about the Gen 2 engine, and as module
    # constants nothing could say so.
    t.eq(crystal.lab["map"], "ELMS_LAB", "the lab")
    t.eq(crystal.lab["stand"], (5, 3), "the tile to talk to the aide from")
    t.eq(crystal.starters["cyndaquil"]["ball"], (6, 4), "Cyndaquil's ball")
    t.eq(crystal.starters["totodile"]["ball"], (7, 4), "Totodile's")
    t.eq(crystal.starters["chikorita"]["ball"], (8, 4), "Chikorita's")
    t.eq(len(crystal.intro_legs), 3, "three doorways from bedroom to lab")
    t.eq(crystal.first_route["route"], "ROUTE_29", "and out onto Route 29")


@test("the symbol table loaded a whole build, not the first few lines of one")
def _(t):
    """`len(sym)` against the `.sym` file's own line count.

    `SymbolTable.__len__` existed and nothing called it, which
    `tools/coverage --dead` pointed out. Using it is better than deleting it,
    because the number is worth checking: a truncated or half-written `.sym`
    resolves the handful of symbols it does contain and raises `KeyError` on
    the rest, which surfaces much later as one unrelated feature not working.

    Derived from the file rather than a figure written here -- the real count is
    around 30,000 and would be another number to keep up to date.
    """
    p = t.pilot()
    sym_file = p.session.sym.path
    lines = [ln for ln in sym_file.read_text(errors="replace").splitlines()
             if ln.strip() and not ln.lstrip().startswith(";")]
    loaded = len(p.session.sym)
    t.note(f"{loaded} symbols from {len(lines)} lines of {sym_file.name}")
    t.gt(loaded, 1000, "a real build defines thousands of symbols")
    # First definition wins, so duplicates make `loaded` smaller than the file's
    # line count -- never larger, which would mean symbols from nowhere.
    t.lte(loaded, len(lines), "and none were invented")
    t.gt(loaded, len(lines) * 0.5, "while most lines produced one")
