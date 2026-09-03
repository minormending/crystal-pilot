"""Command line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pilot import DEFAULT_SOURCE, Pilot
from .session import Budget
from .timeline import Timeline

DEFAULT_ROM = DEFAULT_SOURCE / "pokecrystal.gbc"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="crystal-pilot",
        description="Auto-pilot for grinding in Pokemon Crystal. Backs up the "
                    "save before every task, saves in-game after, and gives up "
                    "on a timeout rather than running forever.",
    )
    ap.add_argument("--rom", default=str(DEFAULT_ROM), help="path to the .gbc ROM")
    ap.add_argument("--source", default=str(DEFAULT_SOURCE),
                    help="pokecrystal disassembly checkout (for names and data)")
    ap.add_argument("--sym", default=None, help="symbol file (default: <rom>.sym)")
    ap.add_argument("--sav", default=None, help="save file (default: <rom>.sav)")
    ap.add_argument("--backup-dir", default=None,
                    help="where to keep save backups (default: <rom dir>/pilot-backups)")
    ap.add_argument("--timeout", type=float, default=900.0,
                    help="give up after this many seconds of real time (default 900)")
    ap.add_argument("--quiet", action="store_true", help="only print the result")
    ap.add_argument("--record", default=None, metavar="FILE",
                    help="record a sped-up video of the run (.mp4 or .gif)")
    ap.add_argument("--record-speed", type=float, default=30.0,
                    help="how many times faster than real time to play back "
                         "(default 30)")
    ap.add_argument("--record-fps", type=int, default=30,
                    help="output frame rate (default 30)")
    ap.add_argument("--record-scale", type=int, default=3,
                    help="pixel scale, 1 = native 160x144 (default 3)")
    ap.add_argument("--record-crf", type=int, default=23,
                    help="H.264 quality, lower is better/larger (default 23)")
    ap.add_argument("--no-record-hud", action="store_true",
                    help="omit the caption strip under the picture")
    # A plain flag plus a separate directory option: with nargs="?" argparse
    # happily swallows the subcommand name as the flag's value.
    ap.add_argument("--checkpoints", action="store_true",
                    help="write periodic save states so the run can be scrubbed "
                         "and resumed")
    ap.add_argument("--checkpoint-dir", default=None, metavar="DIR",
                    help="where to write them (default: alongside --record, "
                         "else <rom>-timeline)")
    ap.add_argument("--checkpoint-every", type=float, default=None,
                    metavar="SECONDS",
                    help="checkpoint spacing: seconds of video when recording, "
                         "otherwise seconds of game time (default: one per "
                         "second of video, or every 30s of game time)")

    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grind", help="grind a Pokemon to a level on the current route")
    g.add_argument("--species", default=None,
                   help="which Pokemon to train, by name (default: slot 1)")
    g.add_argument("--slot", type=int, default=None,
                   help="which party slot to train, 1-6 (overrides --species)")
    g.add_argument("--to-level", type=int, required=True, help="target level")
    g.add_argument("--heal-below", type=float, default=0.40,
                   help="visit a Pokemon Center below this HP fraction (default 0.40)")
    g.add_argument("--flee-below", type=float, default=0.30,
                   help="run from wild battles below this HP fraction (default 0.30)")
    g.add_argument("--no-evolve", action="store_true",
                   help="cancel evolutions instead of allowing them")
    g.add_argument("--learn-moves", action="store_true",
                   help="accept new moves that replace an existing one "
                        "(default: keep the current moveset)")
    g.add_argument("--no-save", action="store_true", help="do not save when done")
    g.add_argument("--on-timeout", choices=("save", "revert"), default="save",
                   help="on timeout, keep the progress made or roll back (default save)")

    h = sub.add_parser("hunt",
                       help="search the current route for a particular wild Pokemon")
    h.add_argument("--species", default=None,
                   help="what to look for, by name (omit with --shiny to accept "
                        "any shiny)")
    h.add_argument("--shiny", action="store_true",
                   help="only stop for a shiny")
    h.add_argument("--min-level", type=int, default=None,
                   help="only stop at or above this level")
    h.add_argument("--max-encounters", type=int, default=500,
                   help="give up after this many wild encounters (default 500)")
    h.add_argument("--heal-below", type=float, default=0.30,
                   help="visit a Pokemon Center below this HP fraction "
                        "(default 0.30)")
    h.add_argument("--leave", action="store_true",
                   help="run from the battle once found, instead of leaving it "
                        "on screen to pick up")

    # The three that act on the situation you are already in. No target: each
    # reads the game and either does the obvious thing or says why it cannot.
    bt = sub.add_parser("battle",
                        help="play out the battle you are already in "
                             "(wild or trainer)")
    bt.add_argument("--slot", type=int, default=None, metavar="N",
                    help="send this party slot out first, 1-based")
    bt.add_argument("--flee-below", type=float, default=0.0, metavar="F",
                    help="try to run if HP drops under this fraction "
                         "(default 0: play it out)")
    bt.add_argument("--max-turns", type=int, default=60,
                    help="give up after this many turns (default 60)")

    cp = sub.add_parser("capture",
                        help="catch the wild Pokemon in front of you right now "
                             "(use `catch` to go and find one)")
    cp.add_argument("--ball", default=None,
                    help="which ball to throw (default: the cheapest ordinary "
                         "ball you have)")
    cp.add_argument("--weaken-to", type=float, default=None, metavar="F",
                    help="chip it down to this HP fraction first, using the "
                         "weakest damaging move (default: do not attack)")
    cp.add_argument("--max-balls", type=int, default=40,
                    help="give up after throwing this many balls (default 40)")

    hl = sub.add_parser("heal",
                        help="walk to the nearest heal place, heal, and come "
                             "back")
    hl.add_argument("--force", action="store_true",
                    help="go even if nothing is hurt")

    ct = sub.add_parser("catch",
                        help="find and catch a wild Pokemon on the current route")
    ct.add_argument("--species", default=None, help="what to catch, by name")
    ct.add_argument("--shiny", action="store_true", help="only catch a shiny")
    ct.add_argument("--ball", default=None,
                    help="which ball to throw (default: the cheapest ordinary "
                         "ball you have; a Master Ball is never used unless "
                         "named here)")
    ct.add_argument("--weaken-to", type=float, default=None, metavar="F",
                    help="chip it down to this HP fraction first, using the "
                         "weakest damaging move (default: do not attack)")
    ct.add_argument("--max-encounters", type=int, default=500,
                    help="give up after this many wild encounters (default 500)")
    ct.add_argument("--max-balls", type=int, default=40,
                    help="give up after throwing this many balls (default 40)")
    ct.add_argument("--heal-below", type=float, default=0.30,
                    help="visit a Pokemon Center below this HP fraction")
    ct.add_argument("--no-save", action="store_true",
                    help="do not save after a successful catch")

    tr = sub.add_parser("trainers",
                        help="battle every trainer on the current route")
    tr.add_argument("--heal-below", type=float, default=0.60,
                    help="heal before a fight below this HP fraction "
                         "(default 0.60 -- trainer battles cannot be fled)")
    tr.add_argument("--max-trainers", type=int, default=None,
                    help="stop after this many")
    tr.add_argument("--no-evolve", action="store_true")
    tr.add_argument("--learn-moves", action="store_true")
    tr.add_argument("--no-save", action="store_true")

    b = sub.add_parser("bootstrap",
                       help="start a brand-new game and play up to the first route")
    b.add_argument("--starter", default="cyndaquil",
                   choices=("cyndaquil", "totodile", "chikorita"))
    b.add_argument("--no-route", action="store_true",
                   help="stop at Elm's lab instead of walking to Route 29")

    sub.add_parser("status", help="print party, location and nearest Pokemon Center")

    p = sub.add_parser("play", help="playable window with inline task dispatch")
    p.add_argument("--new-game", action="store_true",
                   help="bootstrap a new game first")
    p.add_argument("--starter", default="cyndaquil",
                   choices=("cyndaquil", "totodile", "chikorita"))
    p.add_argument("--no-menu", action="store_true",
                   help="disable the in-game TAB menu; use typed commands only")

    sv = sub.add_parser("serve",
                        help="serve a phone-friendly web UI on your network")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--host", default="0.0.0.0",
                    help="interface to bind (default: all, so a phone can reach it)")
    sv.add_argument("--token", default=None,
                    help="access token (default: a fresh random one per run)")
    sv.add_argument("--no-input", action="store_true",
                    help="do not expose the D-pad; tasks only")
    sv.add_argument("--new-game", action="store_true",
                    help="bootstrap a new game first")
    sv.add_argument("--starter", default="cyndaquil",
                    choices=("cyndaquil", "totodile", "chikorita"))

    tl = sub.add_parser("timeline",
                        help="list the checkpoints of a recorded run")
    tl.add_argument("path", help="checkpoint directory (or the .mp4 next to it)")
    tl.add_argument("--limit", type=int, default=None,
                    help="show only the first N checkpoints")

    rs = sub.add_parser("resume",
                        help="load a checkpoint and carry on from that exact point")
    rs.add_argument("path", help="checkpoint directory (or the .mp4 next to it)")
    rs.add_argument("--at", default="end",
                    help="which point: seconds (42), m:ss (1:05), index (#7), "
                         "level (level:14), or start/end (default: end)")
    rs.add_argument("--headless", action="store_true",
                    help="do not open a window; just load, save and exit")
    rs.add_argument("--snap", action="store_true",
                    help="if that point is mid-battle, move to the nearest one "
                         "the game can save at (useful with --at level:N, which "
                         "often lands inside the battle that caused the level-up)")
    rs.add_argument("--commit", action="store_true",
                    help="save the game in-game at that point, so the .sav "
                         "continues from there in any emulator")

    sl = sub.add_parser("slots", help="list the save slots")
    sl.add_argument("--clear", default=None, metavar="N",
                    help="empty a slot (1, 2, 3 or undo)")

    qs = sub.add_parser("save", help="quick-save the exact moment into a slot")
    qs.add_argument("--slot", default="1", help="1, 2 or 3 (default 1)")

    ql = sub.add_parser("load", help="load a slot back")
    ql.add_argument("--slot", default="1", help="1, 2 or 3 (default 1)")

    sub.add_parser("undo",
                   help="put the game back to just before the last job")

    bk = sub.add_parser("backups", help="list or restore save backups")
    bk.add_argument("action", choices=("list", "restore"))
    bk.add_argument("--name", default=None, help="backup .state to restore")
    return ap


def make_pilot(args, window: str = "null", speed: int = 0) -> Pilot:
    log = (lambda *a, **k: None) if args.quiet else print
    return Pilot(rom=args.rom, source=args.source, sym=args.sym, sav=args.sav,
                 backup_dir=args.backup_dir, window=window, speed=speed,
                 timeout_seconds=args.timeout, log=log)


def checkpoint_dir_for(args, path=None) -> Path | None:
    """Where checkpoints go, given --checkpoints / --record."""
    if not args.checkpoints and not args.checkpoint_dir:
        return None
    if args.checkpoint_dir:
        return Path(args.checkpoint_dir)
    target = path or args.record
    if target:
        return Path(target).with_suffix(".timeline")
    return Path(args.rom).with_name(Path(args.rom).stem + "-timeline")


def start_checkpoints(pilot, args, title: str, path=None):
    """Attach a checkpoint writer if --checkpoints was given."""
    directory = checkpoint_dir_for(args, path=path)
    if directory is None:
        return None
    rec = pilot.session.recorder
    if args.checkpoint_every is not None:
        seconds = max(0.1, args.checkpoint_every)
        # Seconds of video when recording, otherwise seconds of game time.
        frames = (round(seconds * rec.every * rec.fps) if rec
                  else round(seconds * 60))
    elif rec is not None:
        frames = rec.every * rec.fps          # one checkpoint per video second
    else:
        frames = 30 * 60                      # every 30s of game time
    return pilot.start_checkpoints(directory, max(1, frames), task=title)


def start_recording(pilot, args, title: str, path=None):
    """Attach a recorder if --record was given. Returns the recorder or None."""
    target = path or args.record
    if not target:
        return None
    return pilot.start_recording(
        target, speed=args.record_speed, fps=args.record_fps,
        scale=args.record_scale, hud=not args.no_record_hud,
        crf=args.record_crf, title=title,
    )



# --- one handler per command -------------------------------------------------
#
# main() used to be 342 lines and six-deep in `if args.cmd ==`, at 35% coverage
# in a codebase whose median function is eight lines. Nothing about a command's
# argument wiring could be exercised without running the whole CLI, so a typo in
# `args.foo` surfaced when a person used it.
#
# Two kinds of command, and the split is real rather than tidy. The first group
# owns its whole lifecycle -- it builds whatever session it needs and returns.
# The second wants a pilot with a game already loaded, which is the setup they
# were all repeating. `bootstrap` is neither: it wants the pilot and explicitly
# not a loaded game, which is the whole point of it.


def cmd_play(args) -> int:
    pilot = make_pilot(args, window="SDL2", speed=1)
    from .interactive import InteractiveSession
    if args.new_game:
        pilot.bootstrap(starter=args.starter)
    elif not pilot.continue_game():
        print("could not load a save; pass --new-game to start fresh",
              file=sys.stderr)
        return 1
    def record_task(worker, title, take):
        if not args.record:
            return None
        target = Path(args.record)
        # One file per dispatched task, numbered in order.
        numbered = target.with_name(f"{target.stem}-{take}{target.suffix}")
        return start_recording(worker, args, title, path=numbered)

    InteractiveSession(
        pilot, default_timeout=args.timeout,
        worker_factory=lambda: make_pilot(args, window="null", speed=0),
        record=record_task if args.record else None,
        source=args.source, in_game_menu=not args.no_menu,
    ).run()
    return 0



def cmd_serve(args) -> int:
    pilot = make_pilot(args, window="null", speed=0)
    if args.new_game:
        pilot.bootstrap(starter=args.starter)
    elif not pilot.continue_game():
        print("could not load a save; pass --new-game to start fresh",
              file=sys.stderr)
        return 1
    from .webui import WebPilot
    WebPilot(pilot, source=args.source, host=args.host, port=args.port,
             token=args.token, timeout=args.timeout,
             allow_input=not args.no_input).run()
    return 0



def cmd_timeline(args) -> int:
    try:
        print(Timeline(args.path).render(limit=args.limit))
    except (FileNotFoundError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0



def cmd_resume(args) -> int:
    try:
        tl = Timeline(args.path)
        cp = tl.resolve(args.at)
    except (FileNotFoundError, ValueError, LookupError) as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.snap and not cp.savable:
        near = tl.nearest_savable(cp)
        if near is None:
            print("no savable checkpoint exists in this run", file=sys.stderr)
            return 2
        print(f"snapped from #{cp.index} (mid-battle) to #{near.index}")
        cp = near
    window = "null" if args.headless else "SDL2"
    pilot = make_pilot(args, window=window, speed=0 if args.headless else 1)
    # Flushed so it stays ahead of anything written to stderr below, which
    # is unbuffered and would otherwise appear first when piped.
    print(f"resuming at {cp.label()}", flush=True)
    pilot.resume_from(tl, cp)
    if args.commit:
        ok, why = pilot.settle_for_save()
        if not ok:
            print(f"could not save here: {why}", file=sys.stderr)
            near = tl.nearest_savable(cp)
            if near is not None and near.index != cp.index:
                print(f"nearest savable point is #{near.index} "
                      f"({near.summary}) -- retry with --at #{near.index}",
                      file=sys.stderr)
        elif pilot.save():
            print("saved -- the .sav now continues from this point")
        else:
            print("in-game save did not commit", file=sys.stderr)
    if args.headless:
        pilot.stop(save_sram=args.commit)
        return 0
    from .interactive import InteractiveSession
    InteractiveSession(
        pilot, default_timeout=args.timeout,
        worker_factory=lambda: make_pilot(args, window="null", speed=0),
        record=None,
    ).run()
    return 0



def cmd_slots(args) -> int:
    pilot = make_pilot(args)
    try:
        from .slots import UNDO_SLOT, describer
        if args.cmd == "slots":
            if args.clear:
                try:
                    gone = pilot.slots.clear(args.clear)
                except ValueError as e:
                    print(e, file=sys.stderr)
                    return 2
                print(f"slot {args.clear}: "
                      f"{'emptied' if gone else 'was already empty'}")
                return 0
            for slot, info in pilot.slots.list().items():
                name = "undo" if slot == UNDO_SLOT else f"slot {slot}"
                print(f"  {name:<8} {info.describe() if info else 'empty'}")
            return 0

        # The three below act on a running game, so it has to be loaded
        # first -- a quick-save of a game that was never started would
        # snapshot the title screen and call it slot 1.
        if not pilot.continue_game():
            print("could not load a save; start a game first",
                  file=sys.stderr)
            return 2
        if args.cmd == "save":
            try:
                info = pilot.slots.save(
                    args.slot, pilot.session, pilot.reader,
                    describe=describer(pilot.reader, pilot.gamedata))
            except ValueError as e:
                print(e, file=sys.stderr)
                return 2
            print(f"slot {info.slot}: {info.describe()}")
            return 0
        if args.cmd == "load":
            try:
                info = pilot.slots.info(args.slot)
            except ValueError as e:
                print(e, file=sys.stderr)
                return 2
            if info is None:
                print(f"slot {args.slot} is empty", file=sys.stderr)
                return 2
            pilot.slots.load(args.slot, pilot.session)
            print(f"loaded slot {info.slot}: {info.describe()}")
            return 0
        # undo
        info = pilot.undo()
        if info is None:
            print("no undo point: no job has run yet", file=sys.stderr)
            return 2
        print(f"back to before {info.job or 'the last job'}: "
              f"{info.describe()}")
        return 0
    finally:
        pilot.stop(save_sram=False)



def cmd_backups(args) -> int:
    pilot = make_pilot(args)
    try:
        if args.action == "list":
            items = pilot.backups.list()
            if not items:
                print(f"no backups in {pilot.backups.dir}")
            for f in items:
                print(f"  {f.name}")
            return 0
        if not args.name:
            print("--name is required to restore", file=sys.stderr)
            return 2
        from .backup import BackupSet
        state = pilot.backups.dir / args.name
        if not state.exists():
            print(f"no such backup: {state}", file=sys.stderr)
            return 2
        sav = state.with_suffix(".sav")
        # restore() flushes SRAM itself, before it copies the .sav in --
        # flushing afterwards is what used to overwrite the restored bytes.
        exact = pilot.backups.restore(pilot.session, BackupSet(
            label=args.name, state=state,
            sav=sav if sav.exists() else None, when="restore"))
        print(f"restored {args.name}"
              f"{'' if exact else ' (machine state only -- no .sav in the set)'}")
        return 0
    finally:
        pilot.stop(save_sram=False)



def cmd_bootstrap(pilot, args) -> int:
    title = f"new game: {args.starter}"
    start_recording(pilot, args, title)
    start_checkpoints(pilot, args, title)
    try:
        summary = pilot.bootstrap(starter=args.starter,
                                  to_route=not args.no_route)
        print(summary)
        saved = pilot.save()
    finally:
        rec = pilot.stop_recording()
        cps = pilot.stop_checkpoints()
        if args.quiet:
            for r in (rec, cps):
                if r:
                    print(r.describe())   # results, not progress chatter
    print(f"saved: {'yes' if saved else 'no'}")
    return 0 if saved else 1



def cmd_status(pilot, args) -> int:
    print(pilot.status())
    return 0



def cmd_hunt(pilot, args) -> int:
    pilot.session.set_budget(Budget(max_frames=60 * 60 * 60 * 12,
                                    max_wall_seconds=args.timeout))
    target = args.species or ("a shiny" if args.shiny else "anything")
    title = f"hunt {target}"
    start_recording(pilot, args, title)
    start_checkpoints(pilot, args, title)
    result = pilot.hunt(
        species=args.species, shiny=args.shiny,
        min_level=args.min_level, max_encounters=args.max_encounters,
        heal_below=args.heal_below, keep_battle=not args.leave,
    )
    rec = pilot.stop_recording()
    cps = pilot.stop_checkpoints()
    print(result.render())
    if args.quiet:
        for r in (rec, cps):
            if r:
                print(r.describe())
    return 0 if result.ok else 1



def cmd_battle(pilot, args) -> int:
    # A battle is minutes at most, so a much smaller budget than the
    # searching tasks get.
    pilot.session.set_budget(Budget(max_frames=60 * 60 * 20,
                                    max_wall_seconds=args.timeout))
    result = pilot.battle(
        target_slot=None if args.slot is None else args.slot - 1,
        flee_below=args.flee_below, max_turns=args.max_turns,
    )
    print(result.render())
    return 0 if result.ok else 1



def cmd_capture(pilot, args) -> int:
    pilot.session.set_budget(Budget(max_frames=60 * 60 * 20,
                                    max_wall_seconds=args.timeout))
    result = pilot.capture(
        ball=args.ball, weaken_to=args.weaken_to,
        max_balls=args.max_balls, save_when_done=not args.no_save,
    )
    print(result.render())
    return 0 if result.ok else 1



def cmd_heal(pilot, args) -> int:
    pilot.session.set_budget(Budget(max_frames=60 * 60 * 60 * 4,
                                    max_wall_seconds=args.timeout))
    result = pilot.heal(force=args.force)
    print(result.render())
    return 0 if result.ok else 1



def cmd_catch(pilot, args) -> int:
    pilot.session.set_budget(Budget(max_frames=60 * 60 * 60 * 12,
                                    max_wall_seconds=args.timeout))
    target = args.species or ("a shiny" if args.shiny else "anything")
    title = f"catch {target}"
    start_recording(pilot, args, title)
    start_checkpoints(pilot, args, title)
    result = pilot.catch(
        species=args.species, shiny=args.shiny, ball=args.ball,
        weaken_to=args.weaken_to, max_encounters=args.max_encounters,
        max_balls=args.max_balls, heal_below=args.heal_below,
        save_when_done=not args.no_save,
    )
    rec = pilot.stop_recording()
    cps = pilot.stop_checkpoints()
    print(result.render())
    if args.quiet:
        for r in (rec, cps):
            if r:
                print(r.describe())
    return 0 if result.ok else 1



def cmd_trainers(pilot, args) -> int:
    pilot.session.set_budget(Budget(max_frames=60 * 60 * 60 * 12,
                                    max_wall_seconds=args.timeout))
    title = "battle every trainer on the route"
    start_recording(pilot, args, title)
    start_checkpoints(pilot, args, title)
    result = pilot.trainers(
        heal_below=args.heal_below, max_trainers=args.max_trainers,
        allow_evolution=not args.no_evolve,
        learn_new_moves=args.learn_moves,
        save_when_done=not args.no_save,
    )
    rec = pilot.stop_recording()
    cps = pilot.stop_checkpoints()
    print(result.render())
    if args.quiet:
        for r in (rec, cps):
            if r:
                print(r.describe())
    return 0 if result.ok else 1



def cmd_grind(pilot, args) -> int:
    pilot.session.set_budget(Budget(max_frames=60 * 60 * 60 * 12,
                                    max_wall_seconds=args.timeout))
    who = args.species or (f"slot {args.slot}" if args.slot else "slot 1")
    title = f"grind {who} -> Lv{args.to_level}"
    start_recording(pilot, args, title)
    start_checkpoints(pilot, args, title)
    result = pilot.grind(
        species=args.species,
        slot=(args.slot - 1) if args.slot else None,
        to_level=args.to_level,
        heal_below=args.heal_below,
        flee_below=args.flee_below,
        allow_evolution=not args.no_evolve,
        learn_new_moves=args.learn_moves,
        save_when_done=not args.no_save,
        on_timeout=args.on_timeout,
    )
    rec = pilot.stop_recording()
    cps = pilot.stop_checkpoints()
    print(result.render())
    if args.quiet:
        for r in (rec, cps):
            if r:
                print(r.describe())
    return 0 if result.ok else 1


# Commands that manage their own session and return.
STANDALONE = {
    "play": cmd_play, "serve": cmd_serve, "timeline": cmd_timeline,
    "resume": cmd_resume, "backups": cmd_backups,
    # One handler, four command names: they differ only in what they do with a
    # slot, and the parser gives them separate subcommands for the help text.
    "slots": cmd_slots, "save": cmd_slots, "load": cmd_slots, "undo": cmd_slots,
}

# Commands that want a pilot with the existing save loaded.
IN_GAME = {
    "status": cmd_status, "hunt": cmd_hunt, "battle": cmd_battle,
    "capture": cmd_capture, "heal": cmd_heal, "catch": cmd_catch,
    "trainers": cmd_trainers, "grind": cmd_grind,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rom = Path(args.rom)
    if not rom.exists():
        print(f"ROM not found: {rom}\n"
              f"Build it in the pokecrystal checkout with `make`, or pass --rom.",
              file=sys.stderr)
        return 2

    if args.cmd in STANDALONE:
        return STANDALONE[args.cmd](args)

    pilot = make_pilot(args)
    try:
        if args.cmd == "bootstrap":
            return cmd_bootstrap(pilot, args)

        # Everything below needs the existing save loaded.
        if not pilot.continue_game():
            print("could not load a save. Run `crystal-pilot bootstrap` first "
                  "to start a new game.", file=sys.stderr)
            return 1

        handler = IN_GAME.get(args.cmd)
        if handler is None:
            # Unreachable while the table and the parser agree, which a test
            # asserts. Better than falling off the end reporting success.
            print(f"no handler for {args.cmd!r}", file=sys.stderr)
            return 2
        return handler(pilot, args)
    finally:
        pilot.stop(save_sram=True)
    return 0
