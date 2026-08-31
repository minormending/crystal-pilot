"""The world graph: connections, warps, nurses and trainers from the map files."""
from ..harness import test


@test("map connections are parsed")
def _(t):
    w = t.world
    t.eq(w.connections.get("NEW_BARK_TOWN"),
         {"west": "ROUTE_29", "east": "ROUTE_27"}, "New Bark Town")
    t.eq(w.connections.get("ROUTE_29", {}).get("west"), "CHERRYGROVE_CITY",
         "Route 29 west")


@test("warps are parsed, including the one into a Pokemon Center")
def _(t):
    w = t.world
    warps = w.warps.get("CHERRYGROVE_CITY", [])
    centre = [x for x in warps if x["to"] == "CHERRYGROVE_POKECENTER_1F"]
    t.eq(len(centre), 1, "one Pokecenter door in Cherrygrove")
    t.eq((centre[0]["x"], centre[0]["y"]), (29, 3), "its coordinates")


@test("every Pokemon Center has a nurse recorded")
def _(t):
    w = t.world
    centres = [c for c in w.warps if c.endswith("POKECENTER_1F")]
    t.gte(len(centres), 10, "Pokecenters found")
    missing = [c for c in centres if c not in w.nurses]
    t.eq(missing, [], "Centers with no nurse (healing would fail there)")
    t.eq(w.nurses["CHERRYGROVE_POKECENTER_1F"], (3, 1), "nurse position")


@test("nearest Pokemon Center is found through the world graph")
def _(t):
    w = t.world
    path = w.nearest_pokecenter("ROUTE_29")
    t.true(path is not None, "Route 29 should have a reachable Center")
    t.eq(path[-1][1], "CHERRYGROVE_POKECENTER_1F", "which Center")
    t.eq(len(path), 2, "Route 29 -> Cherrygrove -> Center is two hops")


@test("route_to can be told to avoid a link that is not walkable")
def _(t):
    w = t.world
    # Route 29 and Route 46 adjoin, but the way through is a gate building.
    direct = w.route_to("ROUTE_29", lambda c: c == "ROUTE_46")
    t.eq(len(direct), 1, "the graph offers the direct edge first")
    around = w.route_to("ROUTE_29", lambda c: c == "ROUTE_46",
                        avoid_hops={("ROUTE_29", "north", "ROUTE_46")})
    t.true(around is not None, "there should still be a way round")
    t.gt(len(around), 1, "the way round is longer")


@test("trainers are parsed with positions and sight ranges")
def _(t):
    w = t.world
    t.gte(len(w.trainers), 50, "maps with trainers")
    r30 = w.trainers.get("ROUTE_30", [])
    t.eq(len(r30), 3, "Route 30 trainer count")
    joey = [x for x in r30 if x["script"] == "TrainerYoungsterJoey"]
    t.eq(len(joey), 1, "Joey is listed")
    t.eq((joey[0]["x"], joey[0]["y"]), (2, 28), "Joey's position")
    t.eq(joey[0]["sight"], 3, "Joey's sight range")
    # Joey only appears once a story flag is cleared; the sweep relies on
    # knowing which trainers are conditional.
    t.eq(joey[0]["event"], "EVENT_ROUTE_30_YOUNGSTER_JOEY", "Joey's event gate")
    always = [x for x in r30 if x["event"] is None]
    t.eq(len(always), 2, "the two ungated Route 30 trainers")
