"""Answering "this grind is slow" with somewhere to go, or an hour to wait for.

Two facts have been sitting next to each other unused. `wild.level_range` knows
what a route's grass tops out at; `world.routes_from` knows which named maps the
graph can reach from here and how far. Neither alone answers the question a slow
grind actually raises, which is *then where should I go* -- and the honest
version of that answer sometimes turns out to be *nowhere, wait an hour*.

Deliberately not a model of experience. "At or above the level being trained" is
one fact about two numbers, and it is the difference between a battle that pays
and one that does not. Anything more would be a guess wearing arithmetic.
"""
from __future__ import annotations

from . import wild

# Time-of-day block names, in wTimeOfDay order, for the sentences below.
HOUR_NAMES = ("in the morning", "during the day", "after dark")

# How far to look for somewhere better. Three maps is about ninety seconds of
# walking with encounters; past that a slow grind here beats a fast one there,
# which is the trade the ranking exists to make and not one to make silently.
MAX_LEGS = 3

# How far above the lead's level a route's *lowest* encounter may be.
#
# Without this the rule "tops out at or above the lead" recommends walking a
# Lv5 into anything. Measured, and it is the reason this number exists: a Lv5
# on Route 29 was told to go to Route 27, which the graph really does reach in
# two maps through New Bark -- and which gives Lv28-32, so the advice was to
# take a Lv5 Chikorita somewhere it would be knocked out by the first thing it
# met. A route that pays is not the same as a route that can be survived.
OVER_LEVEL = 3


def better_grind(gamedata, world, here: str, level: int,
                 max_legs: int = MAX_LEGS) -> dict | None:
    """Somewhere reachable whose grass out-levels `level`. -> a dict, or None.

    Three conditions, and each one is a bug this had before it had the
    condition:

      * **It pays.** The grass tops out at or above the level being trained --
        the one fact about two numbers that separates a battle worth having
        from one that is not.
      * **It beats here.** A higher ceiling than the map already being stood
        on. Otherwise a Lv2 on Route 29 is advised to walk to Route 46, which
        gives exactly the same Lv2-3, and the advice is a wasted journey.
        `better_hour` needed the same correction for the same reason.
      * **It can be survived.** Its lowest encounter is no more than
        `OVER_LEVEL` above the lead. See that constant: the version without it
        sent a Lv5 two maps to a Lv28-32 route.

    Nearest first, because a route three maps away is not an improvement on a
    slow grind; ties go to the higher ceiling. None when nothing reachable is
    better, which is the common case late on and has to read as silence rather
    than as a recommendation to stay put.
    """
    src = gamedata.root_str
    mine = wild.level_range(src, here)
    floor = mine[1] if mine else 0
    reachable = world.routes_from(here, _named_maps(world), max_depth=max_legs)
    best = None
    for const, route in reachable.items():
        span = wild.level_range(src, const)
        if span is None:
            continue
        low, high = span
        if high < level or high <= floor or low > level + OVER_LEVEL:
            continue
        cand = {"map": const, "legs": len(route), "low": low, "high": high}
        if best is None or (cand["legs"], -cand["high"]) < (best["legs"],
                                                            -best["high"]):
            best = cand
    if best is None:
        return None
    best["message"] = (f"{_pretty(gamedata, best['map'])} gives "
                       f"Lv{best['low']}-{best['high']}, "
                       f"{_legs(best['legs'])} away")
    return best


def better_hour(gamedata, here: str, level: int, now: int) -> dict | None:
    """An hour of *this* grass that out-levels `level` when now does not.

    Ahead of any walk, because waiting costs no route and no legs.

    It has to beat this hour too, and that is a correction rather than a
    refinement: the first version asked only whether an hour paid the lead, and
    Crystal's three blocks carry the *same* levels on every Johto route -- so it
    advised coming back in the morning while standing in an indistinguishable
    afternoon. Caught by measuring it on the cartridge rather than by reading
    the code. Measured again afterwards: null at every level on every Crystal
    route, which is the honest thing to say about this half of the feature.
    """
    blocks = wild.hours(gamedata.root_str, here)
    if blocks is None:
        return None
    ceilings = [max((high for _, (_, high) in block.items()), default=0)
                for block in blocks]
    if not ceilings or now >= len(ceilings):
        return None
    mine = ceilings[now]
    for hour, ceiling in enumerate(ceilings):
        if hour == now or ceiling < level or ceiling <= mine:
            continue
        return {"hour": hour, "high": ceiling,
                "message": (f"this grass gives Lv{ceiling} "
                            f"{HOUR_NAMES[hour]}; come back then")}
    return None


def species_hours(gamedata, here: str, now: int) -> dict:
    """What the clock is doing to the species list here.

    Two answers, and they are different sentences:

      `gone`  species that live here but not at this hour -- the one change to
              the offered list that nobody made and nothing explains. Dropping
              them in silence is what this exists to stop.
      `later` species out at some *other* hour and not now.

    Empty lists where the hours are the same, which on Crystal's Johto routes
    is most of them.
    """
    blocks = wild.hours(gamedata.root_str, here)
    if blocks is None or now >= len(blocks):
        return {"gone": [], "later": []}
    mine = set(blocks[now])
    others: dict[str, list[int]] = {}
    for hour, block in enumerate(blocks):
        if hour == now:
            continue
        for name in block:
            if name not in mine:
                others.setdefault(name, []).append(hour)
    later = sorted(others)
    return {"gone": later, "later": [(n, others[n]) for n in later]}


def gone_message(gamedata, here: str, now: int, wanted: str | None) -> str | None:
    """One sentence about the clock, or None when there is nothing to say.

    Prefers the species actually being hunted, because that is the one whose
    disappearance is confusing. Falls back to naming two and counting the rest,
    which is the most a row can hold.
    """
    found = species_hours(gamedata, here, now)
    if not found["later"]:
        return None
    by_name = dict(found["later"])
    if wanted and wanted in by_name:
        when = ", ".join(HOUR_NAMES[h] for h in by_name[wanted])
        return f"{wanted} is here {when}, not now"
    named = found["gone"][:2]
    extra = len(found["gone"]) - len(named)
    parts = [f"{n} {HOUR_NAMES[by_name[n][0]]}" for n in named]
    tail = f", and {extra} more" if extra > 0 else ""
    return "also here: " + ", ".join(parts) + tail


def _named_maps(world) -> list[str]:
    """Every map the graph knows, as the candidate set for one search."""
    seen = set(world.connections)
    for targets in world.connections.values():
        seen.update(targets.values())
    for entries in world.warps.values():
        seen.update(w["to"] for w in entries)
    return sorted(seen)


def _pretty(gamedata, const: str) -> str:
    info = gamedata.maps_by_name.get(const)
    if info is None:
        return const
    return gamedata.map_pretty(info["group"], info["number"])


def _legs(n: int) -> str:
    return "one map" if n == 1 else f"{n} maps"
