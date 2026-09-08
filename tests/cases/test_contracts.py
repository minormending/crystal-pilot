"""The shapes this code passes between its own halves.

Half the modules here answer with a dict -- `restock`, `take_here`, `heal_up`,
`buy_from_clerk` -- and the task wrappers above them read those dicts by
subscript. That is a contract with nothing checking it: a `return` on one path
that omits a key the caller subscripts is a `KeyError` on a route the tests do
not walk, months later, in the middle of an errand.

One was already there. `take_here` returned `{"ok", "took", "message"}` when the
map had nothing left and `{"ok", "took", "empty", "unreachable", "message"}`
otherwise, and `TakeTask.run` read `out["unreachable"]` unguarded. It never
fired, because `TakeTask.run` checks `things_here()` before calling and the two
calls cannot disagree with no frames in between -- so it was not a bug yet, and
would not have been found by running anything.

These read the source rather than the program, which is the only way to see a
path no test takes. Every number they check is derived from the tree: nothing
here says "there are four of these", because a hardcoded count is a second copy
of the answer that stops agreeing with the first.
"""
from __future__ import annotations

import ast
from pathlib import Path

from ..harness import test

PILOT = Path(__file__).resolve().parent.parent.parent / "pilot"


def _modules() -> list[tuple[Path, ast.Module]]:
    out = []
    for path in sorted(PILOT.rglob("*.py")):
        out.append((path, ast.parse(path.read_text())))
    return out


def _own_returns(fn) -> list[ast.Return]:
    """This function's `return`s, not a nested function's.

    `ast.walk` does not respect that boundary, and several functions here define
    a closure that returns something of its own shape -- `_battle_healer`
    inside `heal_up` is the obvious one. Attributing a closure's return to its
    parent would invent a disagreement that is not in the code.
    """
    out: list[ast.Return] = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(child, ast.Return):
                out.append(child)
            walk(child)

    walk(fn)
    return out


def _dict_shape(fn) -> tuple[frozenset[str], frozenset[str]] | None:
    """(keys on every dict return, keys on only some), or None.

    None when the function does not answer with dict literals, or when one of
    them uses `**other` or a computed key -- in which case the literal keys are
    not the whole shape and claiming otherwise would be worse than declining.
    """
    shapes = []
    for ret in _own_returns(fn):
        if not isinstance(ret.value, ast.Dict):
            continue
        if any(k is None for k in ret.value.keys):        # {**other}
            return None
        keys = [k.value for k in ret.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if len(keys) != len(ret.value.keys):              # a computed key
            return None
        shapes.append(frozenset(keys))
    if not shapes:
        return None
    return (frozenset.intersection(*shapes), frozenset().union(*shapes))


def promises() -> dict[str, frozenset[str]]:
    """function name -> the keys it returns on *every* path.

    Keyed by bare name, which is enough here and honest about when it is not: a
    name defined twice with two different shapes is dropped rather than guessed
    at, because a subscript at a call site names only the method.
    """
    always: dict[str, frozenset[str]] = {}
    clashes: set[str] = set()
    for _path, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            shape = _dict_shape(node)
            if shape is None:
                continue
            if node.name in always and always[node.name] != shape[0]:
                clashes.add(node.name)
            always[node.name] = shape[0]
    for name in clashes:
        del always[name]
    return always


def subscripts() -> list[tuple[str, int, str, str]]:
    """Every `x = ...foo(); x["k"]` in the project.

    -> (file, line, called function name, subscripted key). Only assignments to
    a plain local name are followed, and only string-constant subscripts: a key
    computed at runtime cannot be checked from here, and pretending otherwise
    would make this test lie in the reassuring direction.
    """
    out = []
    for path, tree in _modules():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # name -> the function whose result it holds, within this body.
            bound: dict[str, str] = {}
            for node in ast.walk(fn):
                if (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)
                        and isinstance(node.value, ast.Call)):
                    called = node.value.func
                    if isinstance(called, ast.Attribute):
                        bound[node.targets[0].id] = called.attr
                    elif isinstance(called, ast.Name):
                        bound[node.targets[0].id] = called.id
                    else:
                        bound.pop(node.targets[0].id, None)
                elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)):
                    # Rebound to something that is not a call: stop trusting it.
                    bound.pop(node.targets[0].id, None)
            for node in ast.walk(fn):
                if not isinstance(node, ast.Subscript):
                    continue
                if not (isinstance(node.value, ast.Name)
                        and node.value.id in bound):
                    continue
                idx = node.slice
                if isinstance(idx, ast.Constant) and isinstance(idx.value, str):
                    rel = path.relative_to(PILOT.parent)
                    out.append((str(rel), node.lineno,
                                bound[node.value.id], idx.value))
    return out


@test("every key a caller subscripts is returned on every path")
def _(t):
    promised = promises()
    checked = 0
    for where, line, func, key in subscripts():
        if func not in promised:
            continue
        checked += 1
        # The message names the fix, because the fix is always the same one:
        # add the key to the early return, do not make the caller defensive.
        # A dict whose keys depend on which `return` produced it is the defect.
        t.true(key in promised[func],
               f"{where}:{line} reads [{key!r}] from {func}(), which does not "
               f"return it on every path (always: {sorted(promised[func])})")
    # A static check that finds nothing passes, which is the failure mode of
    # every static check. These two floors are only against the walk going
    # blind -- a renamed attribute, an `ast` change -- so they sit well below
    # the real figures rather than restating them; the sharp guard is the
    # named-function test at the bottom of this file, which fails if the three
    # shapes these rules exist for stop being visible.
    t.gt(checked, 10, f"the walk found call sites to check ({checked})")
    t.gt(len(promised), 5, f"and functions that promise a shape ({len(promised)})")


@test("a key returned on only some paths is never read by subscript")
def _(t):
    """The same rule from the other side, and the one that names the offender.

    `restock` genuinely has optional detail -- `spent` and `shop` only exist
    once there was a shop to spend it at -- and `ShopTask` reads exactly those
    with `.get`. That is fine and stays fine; what is not fine is the same key
    read with a subscript.

    The rule is absolute on purpose, including for a subscript a `.get` on the
    line above happens to make safe. `ShopTask` had exactly that --
    `if out.get("shop"): res.stats["shop"] = out["shop"]` -- which was correct
    and stayed correct only as long as the two lines stayed together. Reading it
    once into a local is both simpler and checkable, and a rule with an
    exception for "safe if you read the previous line" is neither.
    """
    optional: dict[str, frozenset[str]] = {}
    for _path, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            shape = _dict_shape(node)
            if shape and shape[1] - shape[0]:
                optional[node.name] = shape[1] - shape[0]
    t.gt(len(optional), 0, "some function has optional keys, or this checks nothing")
    for where, line, func, key in subscripts():
        if func in optional:
            t.false(key in optional[func],
                    f"{where}:{line} subscripts [{key!r}] on {func}(), which "
                    f"returns it on only some paths -- use .get()")


@test("the source walk sees the functions it is supposed to be checking")
def _(t):
    """A guard on the tooling, not on the code.

    Both tests above are only as good as `promises()` finding the functions.
    Naming three that must be in it makes a silent regression in the walk fail
    here rather than turn the other two green.
    """
    promised = promises()
    for name, key in (("take_here", "unreachable"),
                      ("restock", "ok"),
                      ("take_here", "empty")):
        t.true(name in promised, f"{name}() has a known shape")
        t.true(key in promised[name], f"{name}() always returns {key!r}")
    # And the fix that prompted all of this, stated as the claim it is.
    t.eq(promised["take_here"],
         frozenset({"ok", "took", "empty", "unreachable", "message"}),
         "take_here's shape is the same on both paths")
