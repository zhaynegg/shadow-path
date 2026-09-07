from pathlib import Path

import networkx as nx
import osmnx as ox

from backend.config import CRS, LAT, LON


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