import geopandas as gpd
import pandas as pd
import pytest
import shapely
from shapely.geometry import LineString, MultiPolygon, box

from backend.config import CRS
from backend.core.scoring import score_edges


def test_edge_half_in_shade():
    # A 100 m edge running east along y=0.
    edges = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (100, 0)])], crs=CRS)

    # A shadow over its first half. The box is 10 m tall so the edge lies
    # properly inside it rather than grazing its boundary, where floating-point
    # rounding decides whether a touch counts as an intersection.
    shadow = box(0, -5, 50, 5)

    scored = score_edges(edges, shadow)

    assert scored["shade_fraction"].iloc[0] == pytest.approx(0.5)


def test_multipolygon_sums_per_edge_and_leaves_unmatched_edges_sunlit():
    """The shape production actually passes: a field of separate blobs.

    `shadow_field` returns a MultiPolygon, so this is the branch every real
    call takes -- and the one where `score_edges` does its real work, splitting
    the field apart and adding each edge's pieces back up.
    """
    edges = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (100, 0)]),   # crosses both blobs: 30 m + 30 m
            LineString([(0, 50), (100, 50)]),  # far to the north, touches neither
        ],
        crs=CRS,
    )

    shadow = shapely.union_all([box(0, -5, 30, 5), box(70, -5, 100, 5)])
    # Disjoint boxes, so union_all leaves them as two blobs. If a later edit
    # made them touch, this would collapse to one Polygon and the test would
    # quietly stop covering the branch it exists for.
    assert isinstance(shadow, MultiPolygon)

    scored = score_edges(edges, shadow)

    assert scored["shade_fraction"].iloc[0] == pytest.approx(0.6)
    # Not NaN: an edge the spatial index matched nothing for is in full sun,
    # which is a measurement, not missing data.
    assert scored["shade_fraction"].iloc[1] == pytest.approx(0.0)


def test_night_is_fully_shaded():
    """No sun, no geometry. `shadow_field` hands back None below the horizon."""
    edges = gpd.GeoDataFrame(
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(0, 50), (100, 50)])],
        crs=CRS,
    )

    scored = score_edges(edges, None)

    assert scored["shade_fraction"].tolist() == [1.0, 1.0]


def test_osmnx_multiindex_fractions_land_on_the_right_edges():
    """Edges as routing really hands them over: keyed by (u, v, key).

    `score_edges` builds its answer positionally -- `reset_index(drop=True)`,
    then `.to_numpy()` to assign it back -- because pandas would otherwise try
    to align a 0..n-1 index against this one and match nothing. The rows below
    are deliberately out of label order, so anything that sorts or aligns by
    label scrambles them instead of quietly agreeing.
    """
    # Three edges, three lanes of their own, each shaded a different amount --
    # so a scramble shows up as a wrong number, not a coincidence.
    index = pd.MultiIndex.from_tuples(
        [(30, 40, 0), (10, 20, 0), (20, 30, 0)], names=["u", "v", "key"]
    )
    edges = gpd.GeoDataFrame(
        geometry=[
            LineString([(0, 0), (100, 0)]),
            LineString([(0, 50), (100, 50)]),
            LineString([(0, 100), (100, 100)]),
        ],
        index=index,
        crs=CRS,
    )

    shadow = shapely.union_all(
        [box(0, -5, 25, 5), box(0, 45, 50, 55), box(0, 95, 75, 105)]
    )

    scored = score_edges(edges, shadow)

    assert scored.loc[(30, 40, 0), "shade_fraction"] == pytest.approx(0.25)
    assert scored.loc[(10, 20, 0), "shade_fraction"] == pytest.approx(0.50)
    assert scored.loc[(20, 30, 0), "shade_fraction"] == pytest.approx(0.75)

    # routing.py feeds `scored["shade_fraction"].to_dict()` straight into
    # networkx, which keys edges by exactly this triple. Lose the index and the
    # weights attach to nothing -- with no error, just an unshaded city.
    assert scored.index.equals(edges.index)
