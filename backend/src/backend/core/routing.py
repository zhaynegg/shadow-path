import geopandas as gpd
import networkx as nx
import osmnx as ox
from shapely.geometry import Point

from backend.config import CRS


def nearest_node(graph, lat: float, lon: float) -> int:
    nodes = ox.graph_to_gdfs(graph, edges=False)
    point = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(CRS).iloc[0]
    return nodes.distance(point).idxmin()



def route(graph, scored, origin, destination, alpha) -> dict:
    weights = scored["length"] * (1 + alpha * (1 - scored["shade_fraction"]))
    nx.set_edge_attributes(graph, weights.to_dict(), "shade_weight")
    nx.set_edge_attributes(graph, scored["shade_fraction"].to_dict(), "shade_fraction")


    assert alpha >= 0
    def heuristic(u, v):
        return ((graph.nodes[u]["x"] - graph.nodes[v]["x"]) ** 2
        + (graph.nodes[u]["y"] - graph.nodes[v]["y"]) ** 2) ** 0.5

    orig = nearest_node(graph, *origin)
    dest = nearest_node(graph, *destination)
    path = nx.astar_path(graph, orig, dest, heuristic=heuristic, weight="shade_weight")

    distance = 0.0
    shaded = 0.0
    for u, v in zip(path, path[1:]):
        _, data = min(graph[u][v].items(), key=lambda kv: kv[1]["shade_weight"])
        distance += data["length"]
        shaded += data["length"] * data["shade_fraction"]

    return {"distance_m": distance, "shade_fraction": shaded / distance}

def plan(graph, scored, origin, destination, alpha) -> dict:
    return {
        "route": route(graph, scored, origin, destination, alpha),
        "baseline": route(graph, scored, origin, destination, 0.0),
    }