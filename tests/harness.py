"""A small self-contained test runner.

No pytest: the suite needs one thing pytest does not give for free -- cheap,
shared emulator setup -- and avoiding the dependency means `./run-tests` keeps
working without anyone remembering to install anything.

Tests register with @test and receive a context that hands out ready-made Pilots
loaded from save-state fixtures. Booting a session costs ~0.15s and loading a
state ~6ms, while parsing the disassembly costs ~0.09s, so the parsed game data
is shared across the whole run and only the emulator is rebuilt per test.
"""
from __future__ import annotations

import gzip
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))

DEFAULT_SOURCE = Path(
    os.environ.get("POKECRYSTAL_DIR", Path.home() / "projects" / "pokecrystal")
)
DEFAULT_ROM = Path(os.environ.get("POKECRYSTAL_ROM",
                                  DEFAULT_SOURCE / "pokecrystal.gbc"))

_REGISTRY: list[tuple[str, str, callable]] = []
_shared = {}


class Failure(AssertionError):
    pass


class Skipped(Exception):
    """Raised when a test cannot run here -- currently, only a missing ROM.

    Skipping rather than failing lets CI run everything that does not need an
    emulator without a hand-maintained list of test names, which is what broke:
    a filter of "gamedata|world|timeline" also matched a navigation test called
    "reaches a live overworld".
    """


def test(name: str, group: str = ""):
    def wrap(fn):
        _REGISTRY.append((group or fn.__module__.split(".")[-1], name, fn))
        return fn
    return wrap


# --- assertions ------------------------------------------------------------
class Check:
    """Assertions that say what they expected and what they got."""

    def eq(self, got, want, what: str = ""):
        if got != want:
            raise Failure(f"{what or 'value'}: expected {want!r}, got {got!r}")

    def ne(self, got, unwanted, what: str = ""):
        if got == unwanted:
            raise Failure(f"{what or 'value'}: expected anything but {unwanted!r}")

    def true(self, cond, what: str = ""):
        if not cond:
            raise Failure(what or "expected true, got false")

    def false(self, cond, what: str = ""):
        if cond:
            raise Failure(what or "expected false, got true")

    def gt(self, got, floor, what: str = ""):
        if not got > floor:
            raise Failure(f"{what or 'value'}: expected > {floor!r}, got {got!r}")

    def gte(self, got, floor, what: str = ""):
        if not got >= floor:
            raise Failure(f"{what or 'value'}: expected >= {floor!r}, got {got!r}")

    def contains(self, haystack, needle, what: str = ""):
        if needle not in haystack:
            raise Failure(f"{what or 'value'}: {needle!r} not found in {haystack!r}")

    def raises(self, exc, fn, what: str = ""):
        try:
            fn()
        except exc:
            return
        except Exception as e:  # noqa: BLE001
            raise Failure(f"{what}: expected {exc.__name__}, got "
                          f"{type(e).__name__}: {e}") from None
        raise Failure(f"{what or 'call'}: expected {exc.__name__}, nothing raised")


# --- context ---------------------------------------------------------------
@dataclass
class Ctx(Check):
    rom: Path = DEFAULT_ROM
    source: Path = DEFAULT_SOURCE
    _open: list = field(default_factory=list)
    _tmpdirs: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def skip(self, why: str):
        """Bail out of a test without failing it.

        Raised rather than returned, so a caller does not have to remember to
        stop afterwards. Used where the *game* did not cooperate -- no wild
        encounter turned up in the tries allowed -- as distinct from the code
        being wrong, which is a failure.
        """
        raise Skipped(why)

    def note(self, msg: str) -> None:
        """Attach context to the test's line in the report."""
        self.notes.append(msg)

    # -- shared, read-only game data (parsed once per run) ------------------
    @property
    def gamedata(self):
        if "gamedata" not in _shared:
            from pilot.gamedata import GameData
            _shared["gamedata"] = GameData(self.source)
        return _shared["gamedata"]

    @property
    def world(self):
        if "world" not in _shared:
            from pilot.world import World
            _shared["world"] = World(self.gamedata, self.source)
        return _shared["world"]

    # -- emulator ------------------------------------------------------------
    def require_rom(self) -> None:
        if not Path(self.rom).exists():
            raise Skipped(
                f"ROM not found: {self.rom}\n"
                f"Build it from the pokecrystal disassembly with `make`, or set "
                f"POKECRYSTAL_ROM. Tests that only read the disassembly's data "
                f"files do not need one."
            )

    def pilot(self, fixture: str | None = None, timeout: float = 300.0):
        """A Pilot, optionally restored to a fixture's exact machine state.

        Backups go to a temp directory, never the real one beside the ROM.
        Tasks take a backup on entry and prune to the newest few dozen, so a
        test run used to churn through a real player's saves: a full suite added
        a dozen sets and deleted a dozen others. Found the hard way, having
        restored a save from a backup that a later test run then pruned.
        """
        self.require_rom()
        from pilot.pilot import Pilot
        p = Pilot(rom=self.rom, source=self.source, window="null", speed=0,
                  backup_dir=self.scratch("backups"),
                  timeout_seconds=timeout, log=lambda *a, **k: None)
        self._open.append(p)
        if fixture:
            p.session.restore(load_fixture(fixture))
            p.collision._calibrated = False
            p.calibrate()
        return p

    def scratch(self, tag: str = "case"):
        """A temp directory that goes away when the test finishes."""
        import tempfile
        d = Path(tempfile.mkdtemp(prefix=f"crystal-pilot-{tag}-"))
        self._tmpdirs.append(d)
        return d

    def rom_copy(self, tag: str = "case"):
        """A private ROM+sym copy in a temp dir, so save tests never touch the
        real .sav. Cleaned up when the test finishes."""
        self.require_rom()
        import shutil
        import tempfile
        d = Path(tempfile.mkdtemp(prefix=f"crystal-pilot-{tag}-"))
        self._tmpdirs.append(d)
        rom = d / self.rom.name
        shutil.copy2(self.rom, rom)
        shutil.copy2(self.rom.with_suffix(".sym"), rom.with_suffix(".sym"))
        return rom

    def pilot_on(self, rom, fixture: str | None = None, timeout: float = 300.0):
        """Like pilot(), but against a specific (usually temporary) ROM copy."""
        from pilot.pilot import Pilot
        p = Pilot(rom=rom, source=self.source, window="null", speed=0,
                  timeout_seconds=timeout, log=lambda *a, **k: None)
        self._open.append(p)
        if fixture:
            p.session.restore(load_fixture(fixture))
            p.collision._calibrated = False
            p.calibrate()
        return p

    def give_balls(self, p, entries=((5, 40), (4, 10))) -> None:
        """Test-only: write balls straight into the bag.

        The early game has none (Elm's aide hands them over later), and buying
        them would mean driving a Mart, which is not what these tests are for.
        """
        base = p.session.sym.addr("wBalls")
        p.session.wb("wNumBalls", len(entries))
        for i, (item, qty) in enumerate(entries):
            p.session.wb(base + i * 2, item)
            p.session.wb(base + i * 2 + 1, qty)
        p.session.wb(base + len(entries) * 2, 0xFF)

    def into_wild_battle(self, p, tries: int = 3):
        """Test-only: walk the grass until a wild battle is under way.

        The other half of what build_fixtures deliberately does not store. It
        returns at a decision point with the battle structs populated, which is
        the state a person is in when they reach for `battle` or `capture` --
        and that matters for more than convenience: the battle menu's hook has
        already fired by then, which is exactly the situation those two commands
        have to detect rather than assume.

        Returns the BattleState, or None if nothing turned up.
        """
        from pilot.tasks.search import SearchStats, WildSearch
        search = WildSearch(p.session, p.reader, p.control, p.nav, p.gamedata,
                            p.traveler, log=lambda *a, **k: None)
        route = p.reader.location().key
        for _ in range(tries):
            if not search.ensure_grass(route):
                return None
            battle = search.next_encounter(route, SearchStats())
            if battle is not None and p.reader.in_battle():
                return battle
        return None

    def close(self) -> None:
        import shutil
        for p in self._open:
            try:
                p.stop(save_sram=False)
            except Exception:
                pass
        self._open.clear()
        for d in self._tmpdirs:
            shutil.rmtree(d, ignore_errors=True)
        self._tmpdirs.clear()


# --- fixtures --------------------------------------------------------------
def fixture_path(name: str) -> Path:
    return FIXTURES / f"{name}.state.gz"


def load_fixture(name: str) -> bytes:
    path = fixture_path(name)
    if not path.exists():
        raise FileNotFoundError(
            f"missing fixture {name!r} ({path}).\n"
            f"Build the fixtures first:  ./run-tests --build-fixtures"
        )
    return gzip.decompress(path.read_bytes())


def save_fixture(name: str, blob: bytes) -> Path:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = fixture_path(name)
    path.write_bytes(gzip.compress(blob, 6))
    return path


# --- runner ----------------------------------------------------------------
def run(pattern: str | None = None, verbose: bool = False) -> int:
    if not DEFAULT_SOURCE.exists():
        print(f"pokecrystal disassembly not found: {DEFAULT_SOURCE}\n"
              f"Clone it, or set POKECRYSTAL_DIR.", file=sys.stderr)
        return 2

    selected = _REGISTRY
    if pattern:
        rx = re.compile(pattern, re.I)
        selected = [t for t in _REGISTRY if rx.search(t[0]) or rx.search(t[1])]
    if not selected:
        print(f"no tests match {pattern!r}")
        return 1

    passed, skipped, failed, current_group = 0, [], [], None
    t_all = time.monotonic()
    for group, name, fn in selected:
        if group != current_group:
            current_group = group
            print(f"\n{group}")
        ctx = Ctx()
        t0 = time.monotonic()
        try:
            fn(ctx)
        except Skipped as e:
            skipped.append((group, name, str(e)))
            status, detail = "skip", ""
        except Failure as e:
            failed.append((group, name, str(e), None))
            status, detail = "FAIL", str(e)
        except Exception as e:  # noqa: BLE001
            failed.append((group, name, f"{type(e).__name__}: {e}",
                           traceback.format_exc()))
            status, detail = "ERROR", f"{type(e).__name__}: {e}"
        else:
            passed += 1
            status, detail = "ok", ""
        finally:
            ctx.close()
        secs = time.monotonic() - t0
        mark = {"ok": "  ok  ", "skip": " skip "}.get(status, f" {status} ")
        print(f" {mark} {name}  ({secs:.1f}s)")
        for n in ctx.notes:
            if verbose or status != "ok":
                print(f"         {n}")
        if detail:
            print(f"         -> {detail}")

    elapsed = time.monotonic() - t_all
    summary = f"{passed} passed"
    if skipped:
        summary += f", {len(skipped)} skipped"
    summary += f", {len(failed)} failed"
    print(f"\n{summary}  ({elapsed:.1f}s)")
    if skipped:
        # Say loudly what did not run, so a green summary is never mistaken for
        # full coverage.
        print(f"  skipped: {skipped[0][2].splitlines()[0]}")
        print(f"  ({len(skipped)} tests need a ROM built from the disassembly)")
    if failed and verbose:
        for group, name, msg, tb in failed:
            if tb:
                print(f"\n--- {group}: {name} ---\n{tb}")
    return 0 if not failed else 1


def discover() -> None:
    """Import every tests/cases/test_*.py so their @test registrations run."""
    import importlib
    cases = Path(__file__).resolve().parent / "cases"
    for f in sorted(cases.glob("test_*.py")):
        importlib.import_module(f"tests.cases.{f.stem}")
