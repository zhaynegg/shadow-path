import threading
from itertools import pairwise

import geopandas as gpd
import networkx as nx
import osmnx as ox
import shapely
from shapely.geometry import Point

from backend.config import CRS, GRAPH_RADIUS

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
WALK_SPEED_MS = 1.35

# The graph is one shared object and a search writes its weights onto it, so
# two searches at once would read each other's. That was survivable while a
# request was one plan; a whole-day scan holds the graph for twenty-four, and
# two of those overlapping would interleave every stamp. Held around the write
# and the read together -- `taken` reads back the same attribute `walk` set --
# and released before measuring, which only touches the scored frame.
GRAPH_LOCK = threading.Lock()


def graph_nodes(graph) -> gpd.GeoDataFrame:
    """The graph's nodes as points, which is all that snapping needs."""
    return ox.graph_to_gdfs(graph, edges=False)


def snap(nodes, lat: float, lon: float) -> int:
    """The graph node closest to a lat/lon, if one is close enough to mean it.

    Takes the node frame rather than the graph because building it is the
    expensive half: 0.36s on the 15 km disc, against A*'s 9ms. It does not
    depend on the sun, so a caller planning the same walk at every hour of the
    day builds it once and snaps once -- see `departures`.
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


def endpoints(nodes, origin, destination) -> tuple[int, int]:
    """Both ends of a walk, snapped, with the degenerate case ruled out."""
    orig = snap(nodes, *origin)
    dest = snap(nodes, *destination)

    # Two clicks a few metres apart snap to the same corner. The path is then a
    # single node, which has no edges to measure, draw, or divide by.
    if orig == dest:
        raise ValueError(
            "Those two points are the same street corner. Pick somewhere further apart."
        )
    return orig, dest


def edge_weights(scored, alpha):
    """What a metre of each edge costs a walker with this preference.

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
    """
    unwanted = 1 - scored["shade_fraction"] if alpha >= 0 else scored["shade_fraction"]
    return scored["length"] * (1 + abs(alpha) * unwanted)


def search(graph, orig: int, dest: int) -> list[int]:
    """The cheapest path under whatever is currently on `shade_weight`."""

    # Straight-line metres between two nodes. Admissible for any alpha because
    # every weight above is at least the edge's own length, which is at least
    # the straight line it spans -- so this can never overestimate.
    def heuristic(u, v):
        return ((graph.nodes[u]["x"] - graph.nodes[v]["x"]) ** 2
        + (graph.nodes[u]["y"] - graph.nodes[v]["y"]) ** 2) ** 0.5

    try:
        return nx.astar_path(graph, orig, dest, heuristic=heuristic, weight="shade_weight")
    except nx.NetworkXNoPath as exc:
        raise ValueError("No walking route connects those two points.") from exc


def taken(graph, path) -> list[tuple]:
    """The (u, v, key) edges the search actually walked.

    Parallel edges between one pair of corners are real -- a street and the
    footway beside it share both ends -- and A* took whichever was cheapest, so
    anything measuring the result afterwards has to read back the same one.
    """
    return [(u, v, min(graph[u][v].items(), key=lambda kv: kv[1]["shade_weight"])[0])
            for u, v in pairwise(path)]


def walk(graph, scored, orig: int, dest: int, alpha: float) -> list[tuple]:
    """Plan one walk between two already-snapped nodes; returns its edges.

    Separated from measuring it because the two are wanted at different rates.
    A day scan plans the direct route once and measures it twenty-four times:
    at alpha 0 the weight is the edge's own length, and a length does not
    depend on where the sun is, so the path cannot either. What changes across
    the day is only how much of that one path happens to lie in shade.
    """
    with GRAPH_LOCK:
        nx.set_edge_attributes(graph, edge_weights(scored, alpha).to_dict(), "shade_weight")
        return taken(graph, search(graph, orig, dest))


def measure(scored, edges) -> dict:
    """What one fixed set of edges is worth under one stamp's scores.

    Read off the scored frame rather than the graph, which is what lets the
    same path be priced at several times of day: the graph carries whichever
    stamp was written to it last, the frames carry one each.
    """
    legs = scored.loc[edges]
    distance = float(legs["length"].sum())
    shaded = float((legs["length"] * legs["shade_fraction"]).sum())
    return {
        "distance_m": distance,
        # Here rather than in the browser, and derived rather than sent
        # alongside, so that the one assumption about how fast a person walks
        # lives in one place. Both endpoints get it for free by going through
        # this function, and neither can drift from the other.
        "duration_s": distance / WALK_SPEED_MS,
        "shade_fraction": shaded / distance,
    }


def geometry(scored, edges):
    """One line for the whole walk, for the map to draw."""
    return shapely.line_merge(shapely.MultiLineString(
        [list(line.coords) for line in scored.loc[edges].geometry]))


def route(graph, scored, origin, destination, alpha) -> dict:
    orig, dest = endpoints(graph_nodes(graph), origin, destination)
    edges = walk(graph, scored, orig, dest, alpha)
    return measure(scored, edges) | {"geometry": geometry(scored, edges)}


def plan(graph, scored, origin, destination, alpha) -> dict:
    # Snapped once for both legs. It is the same two clicks either way, and
    # building the node frame costs more than both searches put together.
    orig, dest = endpoints(graph_nodes(graph), origin, destination)

    def leg(preference):
        edges = walk(graph, scored, orig, dest, preference)
        return measure(scored, edges) | {"geometry": geometry(scored, edges)}

    return {"route": leg(alpha), "baseline": leg(0.0)}


def departures(graph, scored_by_time, origin, destination, alpha) -> dict:
    """The same walk, planned at every stamp of one day.

    "When should I leave?" is the question a shadow map is uniquely able to
    answer, and the expensive part of answering it is already on disk: the
    nightly export scores the whole graph for every stamp, so a scan is a
    parquet read and an A* per hour rather than twenty-four unions of the city.
    Measured on the 15 km graph: 134 ms a stamp, about three seconds for a
    September day.

    No geometry comes back. The answer is a time, and once the reader picks one
    the map asks /api/route for that stamp the way it always did -- sending two
    dozen polylines to draw one of them would be the larger half of the payload
    and none of the point.
    """
    orig, dest = endpoints(graph_nodes(graph), origin, destination)

    times = sorted(scored_by_time)
    first = scored_by_time[times[0]]

    # Searched once, priced at every stamp -- see `walk`. Doing it per stamp
    # would be correct and would return the identical path twenty-four times.
    direct = walk(graph, first, orig, dest, 0.0)

    rows = []
    for at in times:
        scored = scored_by_time[at]
        leg = measure(scored, walk(graph, scored, orig, dest, alpha))
        rows.append({
            "time": at.strftime("%H:%M"),
            "distance_m": leg["distance_m"],
            # Not the same at every hour: the detour the shade is worth changes
            # with the sun, and on a real walk across the centre that is a five
            # minute spread between the shortest hour and the longest.
            "duration_s": leg["duration_s"],
            "shade_fraction": leg["shade_fraction"],
            # The comparison is the product here too. A shade curve alone peaks
            # at dusk on every walk in the city, which is true and is about the
            # sun rather than about the route; read against the direct path it
            # says where detouring actually buys something.
            "baseline_shade_fraction": measure(scored, direct)["shade_fraction"],
        })

    return {
        # Constant across the day, so both are stated once rather than repeated
        # in every row: the direct route is one path, one length, one duration.
        "baseline_distance_m": measure(first, direct)["distance_m"],
        "baseline_duration_s": measure(first, direct)["duration_s"],
        "departures": rows,
    }
