"""The command line: that every command is wired, and wired to the right thing.

None of this needed a ROM and none of it was tested. main() was 342 lines of
`if args.cmd ==` at 35% coverage, so a command's argument wiring could only be
exercised by running the whole CLI -- which meant a typo in `args.foo` surfaced
when somebody used it.

Now that each command is its own function behind a table, the wiring is
checkable. The most valuable test here is the dullest: that the table and the
parser agree. A command added to one and not the other is the exact mistake the
refactor could have introduced, and it would look like nothing at all until you
typed that command.
"""
from pilot import cli

from ..harness import test


def subcommands() -> list[str]:
    parser = cli.build_parser()
    for action in parser._actions:
        if getattr(action, "dest", None) == "cmd" and action.choices:
            return sorted(action.choices)
    raise AssertionError("the parser has no subcommands")


@test("every subcommand has a handler, and every handler is reachable")
def _(t):
    names = subcommands()
    t.gt(len(names), 10, "the parser offers a good few commands")
    known = set(cli.STANDALONE) | set(cli.IN_GAME) | {"bootstrap"}
    for name in names:
        t.true(name in known, f"{name!r} is in the parser but has no handler")
    for name in sorted(known):
        t.true(name in names, f"{name!r} has a handler but is not in the parser")


@test("the two groups do not overlap, so a command has one lifecycle")
def _(t):
    # A command in both tables would take whichever branch main() checks first,
    # which is the kind of thing that works until it does not.
    both = set(cli.STANDALONE) & set(cli.IN_GAME)
    t.eq(sorted(both), [], "no command is in both groups")
    t.false("bootstrap" in cli.STANDALONE, "bootstrap wants a pilot")
    t.false("bootstrap" in cli.IN_GAME, "but not a loaded game")


@test("every handler takes the shape its group is called with")
def _(t):
    import inspect
    for name, fn in sorted(cli.STANDALONE.items()):
        params = list(inspect.signature(fn).parameters)
        t.eq(params, ["args"], f"{name} is called as handler(args)")
    for name, fn in sorted(cli.IN_GAME.items()):
        params = list(inspect.signature(fn).parameters)
        t.eq(params, ["pilot", "args"], f"{name} is called as handler(pilot, args)")


@test("each command parses its own arguments with the documented defaults")
def _(t):
    # Reading these off the parser rather than the README, so a default that
    # changes cannot quietly disagree with what is written down.
    p = cli.build_parser()
    grind = p.parse_args(["grind", "--to-level", "12"])
    t.eq(grind.cmd, "grind", "the command")
    t.eq(grind.to_level, 12, "the level asked for")
    t.eq(grind.heal_below, 0.40, "the heal threshold default")

    catch = p.parse_args(["catch", "--species", "SENTRET"])
    t.eq(catch.species, "SENTRET", "the species")
    t.true(catch.weaken_to is None, "weakening is off unless asked for")
    t.eq(catch.max_balls, 40, "the ball budget default")

    battle = p.parse_args(["battle"])
    t.eq(battle.flee_below, 0.0, "battle plays out rather than fleeing")

    slot = p.parse_args(["save", "--slot", "2"])
    t.eq(slot.slot, "2", "the slot")


@test("a missing ROM is refused before anything is started")
def _(t):
    # Exit 2, not 1: the ROM being absent is a usage problem, not a task that
    # failed. And it must not reach make_pilot, which would try to boot it.
    code = cli.main(["--rom", "/nonexistent/nothing.gbc", "status"])
    t.eq(code, 2, "a usage exit code")


@test("--help works for every subcommand")
def _(t):
    # Cheap, and it catches an argparse mistake in a command nobody has run:
    # a bad `choices`, a duplicate option string, a bad default.
    import contextlib, io
    for name in subcommands():
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                cli.build_parser().parse_args([name, "--help"])
        except SystemExit as e:
            t.eq(e.code, 0, f"{name} --help exits cleanly")
        text = buf.getvalue()
        t.true(len(text) > 40, f"{name} --help prints something useful")


# --- the task lifecycle -----------------------------------------------------
@test("every task guards its wrap-up, so cleanup cannot lose the result")
def _(t):
    """The inconsistency a shared lifecycle removes.

    Four tasks each caught PilotTimeout around their work, then wrapped the
    wrap-up in a second guard -- because tidying up drives the emulator too and
    can run out of budget itself. Three did. `hunt` did not, so a timeout while
    it put the battle away escaped run(): the caller got an exception where
    every other task returns a TaskResult, and the CLI printed a traceback.
    """
    import inspect

    from pilot.tasks.base import TaskLifecycle
    from pilot.tasks.catch import CatchTask
    from pilot.tasks.grind import GrindTask
    from pilot.tasks.hunt import HuntTask
    from pilot.tasks.trainers import TrainerSweepTask

    for cls in (GrindTask, HuntTask, CatchTask, TrainerSweepTask):
        t.true(issubclass(cls, TaskLifecycle),
               f"{cls.__name__} shares the lifecycle")
        body = inspect.getsource(cls.run)
        t.contains(body, "self.budgeted(", f"{cls.__name__} takes its backup "
                                           f"and guards its work through it")
        t.contains(body, "self.wrapping(", f"{cls.__name__} guards its wrap-up")


@test("a timeout during cleanup is noted, not raised")
def _(t):
    # Driven directly, because the interesting case is the one that used to
    # escape: the work is done, the answer is already on the result, and the
    # tidying is what runs out of budget.
    from pilot.session import PilotTimeout
    from pilot.tasks.base import TaskLifecycle, TaskResult

    class Task(TaskLifecycle):
        pass

    task = Task()
    res = TaskResult()
    res.status, res.message = "completed", "found a SENTRET"
    with task.wrapping(res):
        raise PilotTimeout("out of frames")
    t.eq(res.status, "completed", "the answer already reached is kept")
    t.true(any("cleanup" in n for n in res.notes),
           f"and the cleanup timeout is noted ({res.notes})")


@test("a task returns a result even when its cleanup times out")
def _(t):
    """The trap in guarding a wrap-up: where the return sits.

    `wrapping` swallows a PilotTimeout so a finished job is not lost to a slow
    tidy-up. If the `return res` sits *inside* that block, swallowing means
    falling off the end of the function and handing the caller None -- which is
    worse than the exception it replaced, because None has no status to read.
    Every run() must therefore return at method level.
    """
    import ast
    import inspect

    from pilot.tasks.catch import CatchTask
    from pilot.tasks.grind import GrindTask
    from pilot.tasks.hunt import HuntTask
    from pilot.tasks.trainers import TrainerSweepTask

    for cls in (GrindTask, HuntTask, CatchTask, TrainerSweepTask):
        src = inspect.getsource(cls.run)
        tree = ast.parse(inspect.cleandoc(src) if src.startswith("def") else src.lstrip())
        fn = tree.body[0]
        last = fn.body[-1]
        t.true(isinstance(last, ast.Return),
               f"{cls.__name__}.run ends in a return at method level, "
               f"not inside a guard (ends in {type(last).__name__})")
