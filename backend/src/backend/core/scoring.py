import geopandas as gpd
import pandas as pd
import shapely

from backend.config import CRS


def score_edges(edges: gpd.GeoDataFrame, shadow) -> gpd.GeoDataFrame:
    """What fraction of each edge lies in shade.

    `shadow` arrives as one dissolved geometry. Intersecting every edge against
    it directly is the obvious way and does not scale: GEOS indexes the whole
    field afresh for each edge and throws the index away, so the cost is one
    edge times every blob -- 91s for 10k edges against the city, and roughly an
    hour once the graph covers it too. Splitting the field into its blobs and
    indexing them once turns that into 0.12s for the same answer.
    """
    assert edges.crs == CRS, f"edges are in {edges.crs}, expected {CRS}"

    scored = edges.copy()

    if shadow is None:
        # Sun below the horizon: everything is in shade, and there is no
        # geometry to measure against.
        scored["shade_fraction"] = 1.0
        return scored

    # Position, not label. sjoin reports matches as positional offsets into
    # `blobs`, and edges arrive with osmnx's (u, v, key) MultiIndex.
    flat = edges.reset_index(drop=True)[["geometry"]]
    blobs = gpd.GeoDataFrame(
        geometry=list(shadow.geoms) if hasattr(shadow, "geoms") else [shadow],
        crs=edges.crs,
    )

    pairs = gpd.sjoin(flat, blobs, predicate="intersects")

    # shapely's vectorised ops pair the two arrays positionally. GeoSeries
    # intersection would refuse them -- it aligns on index and will not guess.
    pieces = shapely.intersection(
        pairs.geometry.to_numpy(),
        blobs.geometry.to_numpy()[pairs["index_right"].to_numpy()],
    )

    # Summing per blob is only valid because shadow_field dissolves overlaps,
    # so no two blobs cover the same ground. Were that ever relaxed, a street
    # shaded by two buildings would count twice and shade_fraction would climb
    # past 1.0 -- quietly, and only on the busiest streets.
    shaded = pd.Series(shapely.length(pieces), index=pairs.index).groupby(level=0).sum()

    # Edges the index matched nothing for are in full sun, not missing data.
    shaded = shaded.reindex(flat.index).fillna(0.0)
    scored["shade_fraction"] = (shaded / flat.length).to_numpy()
    return scored
