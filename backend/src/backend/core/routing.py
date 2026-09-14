"""Planning a walk against a sun that moves while you are walking under it.

The router used to price a whole walk at one instant: you asked for 17:40, and
the last kilometre was weighted by 17:40's shadows even though you reach it at
18:25, by which time they have gone. That is a fine approximation for a walk of
twenty minutes at midday -- a +20 minute step there moves 3.6% of the network --
and a poor one for three quarters of an hour at dusk, where the same step moves
a quarter of it and an hour moves 59%.

So the search now carries a clock. See core/search.py for the mechanism and
core/day.py for the table it reads the sun out of; what is left here is the two
things that are about walking rather than about graphs -- what a metre costs a
walker with a preference, and how fast they get through it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import osmnx as ox
import shapely
from shapely.geometry import Point

from backend.config import CRS, GRAPH_RADIUS
from backend.core import search
from backend.core.day import MINUTES_IN_DAY, Day

# A click a little off a pavement is normal; a click across town is not. Beyond
# this, the nearest node is not a reasonable stand-in for what the user meant.
MAX_SNAP_M = 300.0

# The strongest preference the slider offers, in either direction. Nothing in
# the maths needs an upper bound -- the API takes one so that a signed alpha
# still has something to reject, nan and inf included.
MAX_ALPHA = 12.0

# Metres a second on the flat, which Astana is -- it is built on steppe, so
# nothing here needs a slope model. 1.35 m/s is 4.9 km/h, the ordinary adult
# pace every routing engine defaults to within a rounding error.
#
# One number for everybody, and it is the optimistic one. Ice underfoot for four
# months of the year and heat that is the reason this app exists both cost more
# than this admits, and so does a pram, a queue at a crossing, or being 70. Read
# the minutes as the walk itself rather than as a promise about the clock.
#
# It is load-bearing twice over now. It is what turns metres into minutes for
# the panel, and it is what turns distance walked into time of day for the
# search -- which only works because it does not depend on shade. A walker does
# not speed up in the sun, so where they are in the day is a function of how far
# they have come and nothing else, and the search never has to solve for it.
WALK_SPEED_MS = 1.35
MINUTES_PER_M = 1 / WALK_SPEED_MS / 60

# For the plain shortest path, which has no sun in it: one row, and every minute
# of the day pointing at it.
FLAT_CLOCK = np.zeros(MINUTES_IN_DAY + 1, dtype="int16")


@dataclass(frozen=True)
class Streets:
    """Everything about the walk network that does not depend on the sun.

    Three views of one graph, built together because they are indexed together:
    `network` numbers the edges, and `edges` is what those numbers mean when it
    is time to draw the answer. Built once per process.
    """

    network: search.Network
    # Points, for snapping a click to a corner.
    nodes: gpd.GeoDataFrame
    # Lines, for turning a list of edge numbers back into something to draw.
    edges: gpd.GeoDataFrame


def lay_out(edges: gpd.GeoDataFrame, nodes: gpd.GeoDataFrame) -> Streets:
    return Streets(network=search.flatten(edges, nodes), nodes=nodes, edges=edges)


def graph_nodes(graph) -> gpd.GeoDataFrame:
    """The graph's nodes as points, which is all that snapping needs."""
    return ox.graph_to_gdfs(graph, edges=False)


def snap(nodes, lat: float, lon: float) -> int:
    """The graph node closest to a lat/lon, if one is close enough to mean it.

    Takes the node frame rather than the graph because building it is the
    expensive half: 0.36s on the 15 km disc, against the search's few ms. It
    does not depend on the sun, so a caller planning the same walk at every hour
    of the day builds it once and snaps once -- see `departures`.
    """
    point = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(CRS).iloc[0]

    distances = nodes.distance(point)
    node = distances.idxmin()
    if distances[node] > MAX_SNAP_M:
        # Spelled from the constant rather than described in prose: this
        # message said "only covers the city centre" for as long as the graph
        # was a 1.7 km disc, and went on saying it after the graph was not.
        raise ValueError(
            f"That point is {distances[node] / 1000:.1f} km from the nearest mapped "
            f"street. The walking network reaches {GRAPH_RADIUS / 1000:.0f} km from "
            "the centre of Astana."
        )
    return node


def nearest_node(graph, lat: float, lon: float) -> int:
    """`snap`, for a caller holding a graph and doing it only once."""
    return snap(graph_nodes(graph), lat, lon)


def endpoints(streets: Streets, origin, destination) -> tuple[int, int]:
    """Both ends of a walk, snapped and numbered for the search."""
    orig = snap(streets.nodes, *origin)
    dest = snap(streets.nodes, *destination)

    # Two clicks a few metres apart snap to the same corner. The path is then a
    # single node, which has no edges to measure, draw, or divide by.
    if orig == dest:
        raise ValueError(
            "Those two points are the same street corner. Pick somewhere further apart."
        )
    return streets.network.at[orig], streets.network.at[dest]


def edge_weights(shade: np.ndarray, length: np.ndarray, alpha: float) -> np.ndarray:
    """What a metre of each edge costs a walker with this preference, per stamp.

    Alpha is signed: positive seeks shade, negative seeks sun. Both are the
    same rule -- a detour is worth it in proportion to how much of the edge is
    the wrong thing -- so the only difference is which half of the edge counts
    as wrong.

    Reading the formula literally instead -- `1 + alpha * (1 - shade)` with a
    negative alpha -- makes a sunlit edge cost less than nothing, and then
    "cheapest path" stops meaning anything: pacing one sunny street back and
    forth pays out every time, so the cost has no floor to find. A* cannot see
    that. It assumes a settled node can never get cheaper and returns a path
    regardless. Keeping the multiplier non-negative is what leaves it a real
    question to answer, not a matter of taste.

    The second thing it guarantees is what lets the search keep its heuristic:
    every weight here is at least the edge's own length, at every stamp, so
    straight-line distance can never overestimate whichever sun ends up pricing
    an edge.

    That is why the table comes out float64 while `day.shade` is float32. The
    multiplier is never less than 1, so in float64 the product is never less
    than the length it came from -- but rounding the result down to float32
    puts it under by an ulp on every edge the multiplier is exactly 1 for,
    which is every fully-shaded street to a shade-seeker. Nothing would visibly
    break; the heuristic would simply stop being provably admissible, which is
    a poor trade for 15 MB a request that is freed again immediately.
    """
    unwanted = 1 - shade if alpha >= 0 else shade
    return length * (1 + abs(alpha) * unwanted)


def shortest(streets: Streets, start: int, goal: int) -> list[int]:
    """The plain shortest path, as a list of edges.

    At alpha 0 the weight is the edge's own length and no sun enters it, so this
    is one path for every departure of every day -- searched once here and
    re-dated by `restamp` for each one. It is the baseline the whole product is
    sold against, and it is also the only walk in the app a moving sun cannot
    change the shape of.
    """
    flat = streets.network.length.reshape(1, -1)
    return [edge for edge, _ in
            search.walk(streets.network, flat, FLAT_CLOCK, start, goal, 0.0, MINUTES_PER_M)]


def restamp(streets: Streets, day: Day, edges: list[int], depart: float) -> list[tuple[int, int]]:
    """The same edges, re-dated for a different departure.

    A fixed path walked at a different hour is a different walk, and this is the
    cheap half of saying so: no search, just the clock running forward at
    walking pace over a path that is already chosen.
    """
    length = streets.network.length
    table = day.stamp_of_minute
    dusk = len(table) - 1

    walked = 0.0
    dated = []
    for edge in edges:
        clock = depart + walked * MINUTES_PER_M
        dated.append((edge, int(table[int(clock) if clock < dusk else dusk])))
        walked += length[edge]
    return dated


def measure(streets: Streets, day: Day, dated: list[tuple[int, int]]) -> dict:
    """What a walk is worth, each edge priced at the sun it is walked under.

    This is the whole difference the moving clock makes to what gets reported.
    The shade fraction is no longer "what this path would be if you could be
    everywhere along it at once at the moment you set off" -- it is the walk.
    """
    length = streets.network.length
    shade = day.shade

    distance = float(sum(length[edge] for edge, _ in dated))
    shaded = float(sum(length[edge] * shade[stamp][edge] for edge, stamp in dated))

    return {
        "distance_m": distance,
        # Derived here, in the one function every leg on every endpoint passes
        # through, so the pace cannot be assumed twice and differently.
        "duration_s": distance / WALK_SPEED_MS,
        "shade_fraction": shaded / distance,
    }


def geometry(streets: Streets, dated: list[tuple[int, int]]):
    """One line for the whole walk, for the map to draw."""
    lines = streets.edges.geometry.to_numpy()[[edge for edge, _ in dated]]
    return shapely.line_merge(shapely.MultiLineString([list(line.coords) for line in lines]))


def leg(streets: Streets, day: Day, dated: list[tuple[int, int]]) -> dict:
    return measure(streets, day, dated) | {"geometry": geometry(streets, dated)}


def plan(streets: Streets, day: Day, origin, destination,
         alpha: float, depart: float) -> dict:
    """The walk asked for and the plain shortest one, both as they are walked."""
    start, goal = endpoints(streets, origin, destination)

    weights = edge_weights(day.shade, streets.network.length, alpha)
    wanted = search.walk(streets.network, weights, day.stamp_of_minute,
                         start, goal, depart, MINUTES_PER_M)

    return {
        "route": leg(streets, day, wanted),
        "baseline": leg(streets, day, restamp(streets, day, shortest(streets, start, goal), depart)),
    }


def departures(streets: Streets, day: Day, origin, destination, alpha: float) -> dict:
    """The same walk, planned at every stamp of one day.

    "When should I leave?" is the question a shadow map is uniquely able to
    answer, and the expensive part of answering it is already on disk: the
    nightly export scores the whole graph for every stamp, so a scan is a search
    per hour rather than twenty-four unions of the city.

    Every row is now planned against the sun as it moves through that walk, not
    against the one it starts under -- so the hour a row recommends is an hour
    whose route was chosen knowing where you would be by the end of it.

    No geometry comes back. The answer is a time, and once the reader picks one
    the map asks /api/route for that stamp the way it always did -- sending two
    dozen polylines to draw one of them would be the larger half of the payload
    and none of the point.
    """
    start, goal = endpoints(streets, origin, destination)
    weights = edge_weights(day.shade, streets.network.length, alpha)

    # Searched once, re-dated per departure -- see `shortest`.
    direct = shortest(streets, start, goal)

    rows = []
    for at in day.times:
        depart = at.hour * 60 + at.minute
        wanted = search.walk(streets.network, weights, day.stamp_of_minute,
                             start, goal, depart, MINUTES_PER_M)
        walk_of = measure(streets, day, wanted)
        rows.append({
            "time": at.strftime("%H:%M"),
            "distance_m": walk_of["distance_m"],
            # Not the same at every hour: the detour the shade is worth changes
            # with the sun, and on a real walk across the centre that is a five
            # minute spread between the shortest hour and the longest.
            "duration_s": walk_of["duration_s"],
            "shade_fraction": walk_of["shade_fraction"],
            # The comparison is the product here too. A shade curve alone peaks
            # at dusk on every walk in the city, which is true and is about the
            # sun rather than about the route; read against the direct path it
            # says where detouring actually buys something.
            "baseline_shade_fraction":
                measure(streets, day, restamp(streets, day, direct, depart))["shade_fraction"],
        })

    # The direct route is one path, so its length and its duration are stated
    # once rather than repeated in every row. Its *shade* is not constant, which
    # is why that one stays in the rows.
    settled = measure(streets, day, restamp(streets, day, direct, 0))
    return {
        "baseline_distance_m": settled["distance_m"],
        "baseline_duration_s": settled["duration_s"],
        "departures": rows,
    }


def minutes_past_midnight(at: dt.time) -> float:
    return at.hour * 60 + at.minute
