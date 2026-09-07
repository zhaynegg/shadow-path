import geopandas as gpd

from backend.config import CRS


def score_edges(edges: gpd.GeoDataFrame, shadow) -> gpd.GeoDataFrame:
    assert edges.crs == CRS, f"edges are in {edges.crs}, expected {CRS}"

    if shadow is None:
        scored = edges.copy()
        scored["shade_fraction"] = 1.0
        return scored

    inside = edges.geometry.intersection(shadow)
    scored = edges.copy()
    scored["shade_fraction"] = inside.length / edges.length
    return scored