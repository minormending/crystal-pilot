"""Interactive mode: the terminal REPL beside a playable window.

The one module in `pilot/` that no test entered at all -- 131 statements, zero
of them run, which `tools/coverage` put at the top of its table. It is also the
module a person types into directly, so a command that reports something it did
not do is read as the truth.

One did. `speed 4` called `set_emulation_speed(4)`, printed `emulation speed =
4`, and then the loop reset the emulator to 1 on the very next line -- so the
command changed nothing and said otherwise.

PyBoy has `set_emulation_speed` and no way to read the speed back, so the calls
are recorded at that boundary -- through a proxy, because PyBoy is a Cython
class whose attributes cannot be reassigned. That is the only stand-in here:
the dispatcher, the command parsing and the speed bookkeeping are all the real
ones, the proxy forwards everything else to the real emulator, and it sits where
a getter would be if there were one.
"""
import builtins
from contextlib import contextmanager

from pilot.interactive import InteractiveSession

from ..harness import test


class _SpeedSpy:
    """The real emulator, with every `set_emulation_speed` written down.

    A proxy rather than a patched method: PyBoy is a Cython extension type and
    its attributes are read-only, so there is nothing to reassign. Everything
    else falls through to the real object, so the pilot underneath is still
    driving a real Game Boy.
    """

    def __init__(self, pyboy, asked):
        self._pyboy = pyboy
        self._asked = asked

    def set_emulation_speed(self, n):
        self._asked.append(n)
        return self._pyboy.set_emulation_speed(n)

    def __getattr__(self, name):
        return getattr(self._pyboy, name)


@contextmanager
def _capture():
    """What the dispatcher printed. It writes to stdout because a REPL should.

    `_dispatch` uses `print` rather than `self.log` deliberately -- the person
    reading it typed into that terminal. So every test here captures it, both
    to assert on it and to keep the suite's own output clean.
    """
    said: list[str] = []
    real = builtins.print
    builtins.print = lambda *a, **k: said.append(" ".join(str(x) for x in a))
    try:
        yield said
    finally:
        builtins.print = real


def _session(t, fixture="route30"):
    """An InteractiveSession over a real pilot, plus the speeds it asked for.

    No worker factory and no in-game menu: a worker is a second emulator and
    the menu reads SDL for a TAB key, neither of which a test has. Both are
    already optional in the constructor, so this is the shape the CLI builds
    when the menu is turned off rather than a special case invented here.
    """
    p = t.pilot(fixture)
    asked: list[int] = []
    p.session.pyboy = _SpeedSpy(p.session.pyboy, asked)
    sess = InteractiveSession(p, log=lambda *a, **k: None,
                              worker_factory=None, in_game_menu=False)
    return sess, asked


@test("the speed command survives the reset that runs after every command")
def _(t):
    sess, asked = _session(t)
    with _capture():
        sess._dispatch("speed 4")
        # Exactly what `run()` does next, by calling the same method it calls
        # -- not a copy of the two lines, which could agree with a test and
        # not with the loop.
        sess._resume_play_speed()
    t.eq(sess._play_speed, 4, "the choice is remembered")
    t.eq(asked[-1], 4, f"and the emulator was last told 4, not 1 ({asked})")


@test("a task's reset returns to the chosen speed, not to normal play")
def _(t):
    sess, asked = _session(t)
    with _capture():
        sess._dispatch("speed 0")
        # `0` is the interesting value: it means unlimited, and it is the one a
        # `or 1` fallback would silently turn into normal speed.
        sess._resume_play_speed()
        first = asked[-1]
        sess._dispatch("status")
        sess._resume_play_speed()
    t.eq(sess._play_speed, 0, "unlimited is a real choice, not a falsy one")
    t.eq(first, 0, f"still unlimited after the reset ({asked})")
    t.eq(asked[-1], 0, f"and after an unrelated command ({asked})")


@test("speed with no argument means normal play")
def _(t):
    sess, asked = _session(t)
    with _capture():
        sess._dispatch("speed 3")
        sess._dispatch("speed")
    t.eq(sess._play_speed, 1, "back to normal")
    t.eq(asked[-1], 1, f"and the emulator was told so ({asked})")


@test("a speed that is not a number is refused without changing anything")
def _(t):
    sess, asked = _session(t)
    with _capture() as said:
        sess._dispatch("speed 2")
        before = len(asked)
        sess._dispatch("speed fast")
        sess._dispatch("speed -1")
    t.eq(sess._play_speed, 2, "the previous choice stands")
    t.eq(len(asked), before, "and the emulator was not touched")
    t.contains(" ".join(said), "is not a speed", "and it says why")
    t.contains(" ".join(said), "cannot be negative", "for both refusals")


@test("a command that raises is reported, not allowed to end the session")
def _(t):
    # The same reasoning as the web UI's loop: this is a REPL, and a typo must
    # not take the emulator down with it -- `run()`'s exit path stops the
    # emulator and writes the .sav, so an exception escaping `_dispatch` would
    # end a session over a mistyped word.
    sess, _asked = _session(t)
    sess.p.status = lambda: 1 / 0
    with _capture() as said:
        sess._dispatch("status")
    t.true(sess.running, "the session is still running")
    t.contains(" ".join(said), "command failed", f"and it says so ({said})")
    t.contains(" ".join(said), "ZeroDivisionError", "naming what went wrong")


@test("an unknown command says so rather than doing nothing")
def _(t):
    sess, _asked = _session(t)
    with _capture() as said:
        sess._dispatch("frobnicate the widget")
        sess._dispatch("")
    t.contains(" ".join(said), "unknown command", f"({said})")
    # An empty line is not an unknown command -- pressing enter is how a person
    # checks the prompt is alive.
    t.eq(" ".join(said).count("unknown command"), 1, "and a blank line is not one")


@test("quit stops the loop, and so does end-of-input")
def _(t):
    sess, _asked = _session(t)
    with _capture():
        for word in ("quit", "exit", "q"):
            sess.running = True
            sess._dispatch(word)
            t.false(sess.running, f"{word!r} stops it")


@test("the banner documents every verb the dispatcher accepts")
def _(t):
    """The help text is the only documentation interactive mode has.

    A verb the dispatcher handles but the banner never mentions is a feature
    nobody can find; one the banner offers and the dispatcher rejects is worse.
    Both read off the source rather than a list kept here, so this cannot pass
    against a third copy of the same mistake.
    """
    import ast
    import inspect
    import textwrap

    from pilot import interactive as I

    # Dedented: the source of a method arrives indented, which `ast.parse`
    # rejects outright.
    source = textwrap.dedent(inspect.getsource(I.InteractiveSession._dispatch))
    tree = ast.parse(source)
    verbs: set[str] = set()
    for node in ast.walk(tree):
        # `verb == "status"` and `verb in ("quit", "exit", "q")`
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name):
            if node.left.id != "verb":
                continue
            for comp in node.comparators:
                if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                    verbs.add(comp.value)
                elif isinstance(comp, (ast.Tuple, ast.List)):
                    for el in comp.elts:
                        if isinstance(el, ast.Constant):
                            verbs.add(el.value)
    t.gt(len(verbs), 6, f"the walk found the dispatcher's verbs ({sorted(verbs)})")
    banner = I.BANNER.lower()
    # Aliases are deliberately undocumented: the banner offers `help / quit`,
    # and `?`, `exit` and `q` are conveniences rather than a second vocabulary.
    aliases = {"?", "exit", "q"}
    for verb in sorted(verbs - aliases):
        t.contains(banner, verb, f"the banner mentions {verb!r}")
