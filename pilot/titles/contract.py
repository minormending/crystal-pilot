"""What a title profile has to be, checked rather than described.

Every profile is a file somebody wrote by hand, and the failures that shape has
are all quiet ones. A `stand` coordinate given as a list instead of a pair, a
map key written as `(24, 5)` where the code wants `"ELMS_LAB"`, a `starters`
table missing the species name -- none of those is a crash at load, which is
exactly why they need finding at load. The alternative is finding them when the
pilot walks confidently into the wrong building.

So this is the contract, and `pick_title` skips a profile that fails it: a hack
with a broken profile falls through to `generic` and keeps a working pilot,
rather than half-driving with a description it cannot trust.

Deliberately not exhaustive about *values*. Whether Elm's lab really is at
(5,2) is not something this can know, and a profile that says the wrong tile is
a profile that talks to a wall -- a bug no checker can see. What it does check
is the shape: that every field is the kind of thing the code will try to use.
"""
from __future__ import annotations

# Two vocabularies, and keeping them apart is the point rather than pedantry.
# A `push` is a button, because that is what `take_warp` presses. An `edge` is a
# compass bearing, because that is how `data/maps/attributes.asm` names a map
# connection and what `world.EDGE_TO_BUTTON` translates.
#
# This distinction was found by the checker, on its first run, in the profile
# written alongside it: `first_route.edge` said "west" and the contract wanted
# "left", so Crystal failed validation and the pilot silently fell through to
# `generic` -- header matched, symbol matched, profile discarded. Which is
# exactly the class of quiet failure the file exists for, arriving before
# anything shipped.
PUSH_DIRECTIONS = ("up", "down", "left", "right")
EDGES = ("north", "south", "east", "west")


def _is_tile(value) -> bool:
    return (isinstance(value, tuple) and len(value) == 2
            and all(isinstance(n, int) and n >= 0 for n in value))


def validate_title(title) -> list[str]:
    """Everything wrong with a profile, as sentences. Empty means it is usable."""
    bad: list[str] = []
    name = getattr(title, "id", None) or "a title"

    def say(msg: str) -> None:
        bad.append(f"{name}: {msg}")

    if title is None:
        return ["a title must be an object"]
    if not isinstance(getattr(title, "id", None), str) or not title.id:
        say("needs a non-empty string `id`")
    if not callable(getattr(title, "matches", None)):
        say("needs a `matches(header, symbols)` callable")

    starters = getattr(title, "starters", None)
    if starters is None:
        say("needs a `starters` mapping (empty is allowed)")
    elif not isinstance(starters, dict):
        say("`starters` must be a mapping")
    else:
        for key, spec in starters.items():
            if not isinstance(key, str) or key != key.lower():
                say(f"`starters` key {key!r} must be a lower-case string")
            if not isinstance(spec, dict):
                say(f"`starters[{key!r}]` must be a mapping")
                continue
            if not _is_tile(spec.get("ball")):
                say(f"`starters[{key!r}].ball` must be an (x, y) tile")
            if not isinstance(spec.get("species"), str):
                say(f"`starters[{key!r}].species` must be a species name")

    legs = getattr(title, "intro_legs", None)
    if legs is None:
        say("needs `intro_legs` (an empty tuple is allowed)")
    elif not isinstance(legs, (list, tuple)):
        say("`intro_legs` must be a sequence")
    else:
        for i, leg in enumerate(legs):
            if not isinstance(leg, dict):
                say(f"`intro_legs[{i}]` must be a mapping")
                continue
            for field in ("on", "expect"):
                if not isinstance(leg.get(field), str) or not leg[field]:
                    say(f"`intro_legs[{i}].{field}` must be a map constant")
            if not _is_tile(leg.get("warp")):
                say(f"`intro_legs[{i}].warp` must be an (x, y) tile")
            push = leg.get("push")
            if push is not None and push not in PUSH_DIRECTIONS:
                say(f"`intro_legs[{i}].push` must be a button, one of "
                    f"{PUSH_DIRECTIONS}")

    lab = getattr(title, "lab", None)
    if lab is not None:
        if not isinstance(lab, dict):
            say("`lab` must be a mapping")
        else:
            if not isinstance(lab.get("map"), str):
                say("`lab.map` must be a map constant")
            if not _is_tile(lab.get("stand")):
                say("`lab.stand` must be an (x, y) tile")
            if not isinstance(lab.get("ball_row"), int):
                say("`lab.ball_row` must be an int")

    first = getattr(title, "first_route", None)
    if first is not None:
        if not isinstance(first, dict):
            say("`first_route` must be a mapping")
        else:
            for field in ("from_map", "route"):
                if not isinstance(first.get(field), str):
                    say(f"`first_route.{field}` must be a map constant")
            if not _is_tile(first.get("warp")):
                say("`first_route.warp` must be an (x, y) tile")
            if first.get("edge") not in EDGES:
                say(f"`first_route.edge` must be a compass bearing, "
                    f"one of {EDGES}")

    # The check that would otherwise wait until somebody ran `bootstrap`: a
    # profile that claims it can start a new game has to carry everything the
    # bootstrap reads, and a profile that cannot has to say so rather than fail
    # halfway through the intro.
    if getattr(title, "can_bootstrap", None) is True:
        for field in ("starters", "intro_legs", "lab", "first_route"):
            if not getattr(title, field, None):
                say(f"claims `can_bootstrap` but has no `{field}`")

    return bad


def usable(title) -> bool:
    return not validate_title(title)
