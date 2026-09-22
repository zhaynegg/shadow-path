from pathlib import Path

import networkx as nx
import osmnx as ox

from backend.config import CRS, LAT, LON
from backend.core.routing import snap


def load_graph(cache_dir: Path, radius: float) -> nx.MultiDiGraph:
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir / "osmnx")
    ox.settings.requests_timeout = 900

    path = cache_dir / f"walk-{radius:.0f}m.graphml"
    if path.exists():
        return ox.load_graphml(path)

    graph = ox.graph_from_point((LAT, LON), dist=radius, network_type="walk")
    graph = ox.project_graph(graph, to_crs=CRS)
    ox.save_graphml(graph, path)
    return graph


def graph_nodes(graph):
    """The graph's nodes as a frame, which is all that snapping needs.

    Lives here rather than in routing.py because it is the one step that still
    needs osmnx, and routing.py is on the API's import path -- where osmnx
    costs 226 MB before it has read anything. See core/streets.py.
    """
    return ox.graph_to_gdfs(graph, edges=False)


def nearest_node(graph, lat: float, lon: float) -> int:
    """`snap`, for a caller holding a graph and doing it only once."""
    return snap(graph_nodes(graph), lat, lon)
