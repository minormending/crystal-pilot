"""The in-game TAB menu, driven by a scripted key sequence.

There is no window and nobody pressing keys here, so the menu takes its input
from a canned sequence. What is being checked is that the choices on screen turn
into the task the player actually asked for.
"""
from pilot.ingame import (KEY_CLOSE, KEY_CONFIRM, KEY_DOWN, KEY_UP,
                          InGameMenu, ScriptedKeys)

from ..harness import test


class _Result:
    ok = True
    message = "done"
    stats = {"battles": 3}
    saved = False

    def render(self):
        return "done"


class _Recorder:
    """Stands in for the Pilot a task would run against."""

    def __init__(self):
        self.calls = []

    def _record(self, name):
        def fn(**kwargs):
            self.calls.append((name, kwargs))
            return _Result()
        return fn

    def __getattr__(self, name):
        if name in ("grind", "hunt", "catch", "trainers"):
            return self._record(name)
        raise AttributeError(name)


def _menu(t, keys, fixture="grass_cyndaquil"):
    p = t.pilot(fixture)
    rec = _Recorder()
    # The runner is what interactive mode supplies; here it just runs the task
    # function against a recorder so we can see what was asked for.
    menu = InGameMenu(p, lambda fn, title, on_log: fn(rec), t.source,
                      log=lambda *a, **k: None, keys=ScriptedKeys(keys))
    menu.open()
    return p, rec


@test("choosing GRIND dispatches the right party slot and level")
def _(t):
    # GRIND -> first party member -> level +1 -> up once -> confirm -> close
    p, rec = _menu(t, [KEY_CONFIRM, KEY_CONFIRM, KEY_UP, KEY_CONFIRM,
                       KEY_CONFIRM, KEY_CLOSE])
    t.eq(len(rec.calls), 1, "one task dispatched")
    name, kwargs = rec.calls[0]
    t.eq(name, "grind", "task chosen")
    t.eq(kwargs["slot"], 0, "party slot")
    # The spinner starts one above the current level, then one press of up.
    t.eq(kwargs["to_level"], 16, "target level")


@test("choosing HUNT offers this route's species and dispatches one")
def _(t):
    # down to HUNT -> confirm -> first real species -> confirm -> close
    p, rec = _menu(t, [KEY_DOWN, KEY_CONFIRM, KEY_CONFIRM, KEY_CONFIRM,
                       KEY_CLOSE])
    t.eq(len(rec.calls), 1, "one task dispatched")
    name, kwargs = rec.calls[0]
    t.eq(name, "hunt", "task chosen")
    t.false(kwargs["shiny"], "not the shiny entry")
    from pilot.wild import species_on
    here = species_on(t.source, "ROUTE_29")
    t.contains(here, kwargs["species"], "species offered is one that lives here")


@test("the shiny entry dispatches a shiny hunt with no species")
def _(t):
    # down to HUNT -> confirm -> up onto ANY SHINY -> confirm -> close
    p, rec = _menu(t, [KEY_DOWN, KEY_CONFIRM, KEY_UP, KEY_CONFIRM,
                       KEY_CONFIRM, KEY_CLOSE])
    name, kwargs = rec.calls[0]
    t.eq(name, "hunt", "task chosen")
    t.true(kwargs["shiny"], "shiny requested")
    t.eq(kwargs["species"], None, "no particular species")


@test("CATCH refuses when the bag has no balls, without dispatching")
def _(t):
    # down twice to CATCH -> confirm -> dismiss the message -> close
    p, rec = _menu(t, [KEY_DOWN, KEY_DOWN, KEY_CONFIRM, KEY_CONFIRM, KEY_CLOSE])
    t.eq(rec.calls, [], "nothing should be dispatched with no balls")


@test("CATCH dispatches once the bag has balls")
def _(t):
    p = t.pilot("grass_cyndaquil")
    t.give_balls(p)
    rec = _Recorder()
    menu = InGameMenu(p, lambda fn, title, on_log: fn(rec), t.source,
                      log=lambda *a, **k: None,
                      keys=ScriptedKeys([KEY_DOWN, KEY_DOWN, KEY_CONFIRM,
                                         KEY_CONFIRM, KEY_CONFIRM, KEY_CLOSE]))
    menu.open()
    t.eq(len(rec.calls), 1, "one task dispatched")
    t.eq(rec.calls[0][0], "catch", "task chosen")


@test("TRAINERS says so when the map has none, and dispatches when it does")
def _(t):
    # Route 29 has no trainers: the menu should say so rather than dispatch.
    p, rec = _menu(t, [KEY_DOWN, KEY_DOWN, KEY_DOWN, KEY_CONFIRM,
                       KEY_CONFIRM, KEY_CLOSE])
    t.eq(rec.calls, [], "no trainers on Route 29, so nothing to do")

    # Route 30 has three, behind a confirm.
    p2, rec2 = _menu(t, [KEY_DOWN, KEY_DOWN, KEY_DOWN, KEY_CONFIRM,
                         KEY_UP, KEY_CONFIRM, KEY_CONFIRM, KEY_CLOSE],
                     fixture="route30")
    t.eq(len(rec2.calls), 1, "one task dispatched on a map with trainers")
    t.eq(rec2.calls[0][0], "trainers", "task chosen")


@test("backing out of the menu dispatches nothing")
def _(t):
    p, rec = _menu(t, [KEY_CLOSE])
    t.eq(rec.calls, [], "closing immediately should do nothing")


@test("STATUS is a read-only panel")
def _(t):
    # down x4 to STATUS -> confirm -> dismiss -> close
    p, rec = _menu(t, [KEY_DOWN, KEY_DOWN, KEY_DOWN, KEY_DOWN, KEY_CONFIRM,
                       KEY_CONFIRM, KEY_CLOSE])
    t.eq(rec.calls, [], "status should not dispatch a task")


@test("the menu never advances the game while it is open")
def _(t):
    p = t.pilot("grass_cyndaquil")
    before_frame = p.session.frame
    before_loc = p.reader.location()
    rec = _Recorder()
    menu = InGameMenu(p, lambda fn, title, on_log: fn(rec), t.source,
                      log=lambda *a, **k: None,
                      keys=ScriptedKeys([KEY_DOWN, KEY_UP, KEY_DOWN,
                                         KEY_CLOSE]))
    menu.open()
    # Freezing the game is the whole point: the keys pressed in here must not
    # reach it, and no frames may pass.
    t.eq(p.session.frame, before_frame, "frames advanced while the menu was open")
    t.eq(p.reader.location(), before_loc, "the player moved while the menu was open")
