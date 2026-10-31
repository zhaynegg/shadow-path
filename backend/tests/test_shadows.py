"""Shadow geometry, including the canopy that stopped the October 24 export."""

import geopandas as gpd
import pytest
import shapely
from shapely.affinity import translate
from shapely.geometry import MultiPolygon, Polygon, box

from backend.config import CRS
from backend.core.shadows import SHADOW_GRID_M, cast_shadow, layered_field


@pytest.fixture
def october_canopy():
    # The actual valid canopy that failed on 2026-10-24 at 09:00. Keep its
    # projected coordinates: moving it to the origin hides the rounding error.
    outline = [
        (50, 25), (46, 22), (45, 18), (56, 17), (58, 15), (57, 12),
        (59, 11), (59, 9), (56, 8), (56, 6), (60, 3), (64, 4),
        (65, 6), (67, 6), (70, 3), (74, 4), (75, 6), (82, 3),
        (87, 8), (84, 15), (87, 16), (88, 14), (92, 13), (97, 18),
        (96, 22), (92, 25), (88, 24), (87, 22), (85, 22), (82, 25),
        (78, 24), (77, 22), (75, 22), (72, 25), (68, 24), (67, 22),
        (65, 22), (62, 25), (58, 24), (57, 22),
    ]
    holes = [
        [(65, 16), (68, 15), (67, 12), (64, 13)],
        [(75, 16), (78, 15), (77, 12), (74, 13)],
    ]
    return translate(Polygon(outline, holes),
                     xoff=671000.8530857915, yoff=5665700.725593821)


def test_valid_canopy_casts_a_shadow_at_the_october_24_sun_angle(october_canopy):
    assert october_canopy.is_valid

    shadow = cast_shadow(october_canopy, 4.0, 16.24558934808859, 134.30158825956164)

    assert shadow.is_valid
    assert shadow.area == pytest.approx(1323.34, abs=0.1)
    # Millimetre rounding must not lose the tree footprint or substitute a
    # convex hull that fills every notch and courtyard.
    assert shadow.buffer(0.002).covers(october_canopy)
    assert shadow.area < shadow.convex_hull.area


@pytest.mark.parametrize("azimuth", [0.0, 90.0, 180.0, 270.0])
def test_shadow_preserves_a_courtyard_and_separate_footprints(azimuth):
    courtyard = Polygon(box(0, 0, 100, 100).exterior,
                        [box(20, 20, 80, 80).exterior])
    footprint = MultiPolygon([courtyard, box(200, 0, 210, 10)])

    shadow = cast_shadow(footprint, 5.0, 45.0, azimuth)

    assert shadow.is_valid
    assert shadow.covers(footprint)
    assert not shadow.covers(shapely.Point(50, 50))
    assert not shadow.covers(shapely.Point(150, 50))


@pytest.mark.parametrize("altitude", [0.0, -5.0])
def test_no_shadow_below_the_horizon(altitude):
    assert cast_shadow(box(0, 0, 10, 10), 5.0, altitude, 180.0) is None


@pytest.mark.parametrize("failure", [None, "field_union", "difference"])
def test_layered_field_merges_overlaps_and_removes_solid_shade(
    october_canopy, monkeypatch, failure,
):
    # The real canopy exercises the per-footprint retry. Also exercise a GEOS
    # failure during the citywide merge or layer subtraction: those can depend
    # on the GEOS build as well as the input coordinates.
    if failure is not None:
        operation = "union_all" if failure == "field_union" else "difference"
        original = getattr(shapely, operation)
        failed = False

        def flaky_overlay(*args, **kwargs):
            nonlocal failed
            is_field = operation == "difference" or len(args[0]) == 2
            if is_field and kwargs.get("grid_size") is None and not failed:
                failed = True
                raise shapely.errors.GEOSException("TopologyException: test overlay")
            return original(*args, **kwargs)

        monkeypatch.setattr(shapely, operation, flaky_overlay)

    footprints = gpd.GeoDataFrame(
        {"geometry": [october_canopy, october_canopy, translate(october_canopy, 5, 0)],
         "height_m": [4.0, 4.0, 4.0]},
        crs=CRS,
    )
    soft = footprints.index.to_series() > 0

    opaque, dappled = layered_field(
        footprints, 16.24558934808859, 134.30158825956164, soft)

    assert opaque.is_valid
    assert dappled.is_valid
    assert opaque.area > 0
    assert dappled.area > 0
    # Overlay vertices are rounded to the same grid as the shadow calculation.
    overlap = shapely.intersection(opaque, dappled, grid_size=SHADOW_GRID_M)
    assert overlap.area == pytest.approx(0, abs=1e-8)
    if failure is not None:
        assert failed


@pytest.mark.parametrize("canopy_only", [False, True])
def test_layered_field_allows_an_empty_layer(canopy_only):
    footprints = gpd.GeoDataFrame(
        {"geometry": [box(0, 0, 10, 10)], "height_m": [4.0]}, crs=CRS)
    soft = footprints.index.to_series().map(lambda _: canopy_only)

    opaque, dappled = layered_field(footprints, 45.0, 180.0, soft)

    assert (opaque is None) == canopy_only
    assert (dappled is None) != canopy_only
