import geopandas as gpd
import networkx as nx
import osmnx as ox
import shapely
from shapely.geometry import Point

from backend.config import CRS


# A click a little off a pavement is normal; a click across town is not. Beyond
# this, the nearest node is not a reasonable stand-in for what the user meant.
MAX_SNAP_M = 300.0

# The strongest preference the slider offers, in either direction. Nothing in
# the maths needs an upper bound -- the API takes one so that a signed alpha
# still has something to reject, nan and inf included.
MAX_ALPHA = 12.0


def nearest_node(graph, lat: float, lon: float) -> int:
    """The graph node closest to a lat/lon, if one is close enough to mean it."""
    nodes = ox.graph_to_gdfs(graph, edges=False)
    point = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(CRS).iloc[0]

    distances = nodes.distance(point)
    node = distances.idxmin()
    if distances[node] > MAX_SNAP_M:
        raise ValueError(
            f"That point is {distances[node] / 1000:.1f} km from the nearest mapped "
            "street. The walking network only covers the city centre."
        )
    return node



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


def route(graph, scored, origin, destination, alpha) -> dict:
    weights = edge_weights(scored, alpha)
    nx.set_edge_attributes(graph, weights.to_dict(), "shade_weight")
    nx.set_edge_attributes(graph, scored["shade_fraction"].to_dict(), "shade_fraction")

    # Straight-line metres between two nodes. Admissible for any alpha because
    # every weight above is at least the edge's own length, which is at least
    # the straight line it spans -- so this can never overestimate.
    def heuristic(u, v):
        return ((graph.nodes[u]["x"] - graph.nodes[v]["x"]) ** 2
        + (graph.nodes[u]["y"] - graph.nodes[v]["y"]) ** 2) ** 0.5

    orig = nearest_node(graph, *origin)
    dest = nearest_node(graph, *destination)

    # Two clicks a few metres apart snap to the same corner. The path is then a
    # single node, which has no edges to measure, draw, or divide by.
    if orig == dest:
        raise ValueError(
            "Those two points are the same street corner. Pick somewhere further apart."
        )

    try:
        path = nx.astar_path(graph, orig, dest, heuristic=heuristic, weight="shade_weight")
    except nx.NetworkXNoPath as exc:
        raise ValueError("No walking route connects those two points.") from exc

    distance = 0.0
    shaded = 0.0
    for u, v in zip(path, path[1:]):
        _, data = min(graph[u][v].items(), key=lambda kv: kv[1]["shade_weight"])
        distance += data["length"]
        shaded += data["length"] * data["shade_fraction"]

    edges = ox.routing.route_to_gdf(graph, path, weight="shade_weight")
    line = shapely.line_merge(shapely.MultiLineString([list(g.coords) for g in edges.geometry]))

    return {"distance_m": distance, "shade_fraction": shaded / distance, "geometry": line}

def plan(graph, scored, origin, destination, alpha) -> dict:
    return {
        "route": route(graph, scored, origin, destination, alpha),
        "baseline": route(graph, scored, origin, destination, 0.0),
    }