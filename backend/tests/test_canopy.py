"""Which canopy the shadow model gets, and what height it says it is.

There are now two sources of canopy with very different confidence -- crowns cut
from a 1 m height map, and the 10 m Sentinel-2 lattice filling what that map did
not see -- and the whole value of the pair is that they stay distinguishable.
These are the joins where they could quietly stop being.
"""

import datetime as dt
import importlib.util
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import shapely

from backend.core import trees as T

CRS = "EPSG:32642"

# The city centre in the project's metres, so `within` has something to measure
# from that agrees with config.LAT/LON.
CENTRE = gpd.GeoSeries([shapely.Point(71.4704, 51.1605)], crs=4326).to_crs(CRS).iloc[0]


def frame(geoms, **columns):
    return gpd.GeoDataFrame({"geometry": list(geoms), **columns}, crs=CRS)


def cell(x, y, size=T.CELL_M):
    """A square on the Sentinel-2 lattice, which is what the old cache holds."""
    return shapely.box(x, y, x + size, y + size)


# --- the radius filter, now shared by three loaders -------------------------

def test_within_keeps_what_is_inside_and_drops_what_is_not():
    near = CENTRE.buffer(100)
    far = shapely.Point(CENTRE.x + 20_000, CENTRE.y)
    kept = T.within(frame([near, far]), radius=15_000)
    assert len(kept) == 1
    assert kept.geometry.iloc[0].equals(near)


def test_within_passes_everything_through_when_no_radius_is_asked_for():
    """The tile export wants the whole cache, not a disc."""
    far = shapely.Point(CENTRE.x + 200_000, CENTRE.y)
    assert len(T.within(frame([far]), radius=None)) == 1


# --- which file wins --------------------------------------------------------

def measured_file(path: Path, height=6.0):
    frame([CENTRE.buffer(8)], height_m=[height],
          height_source=[T.MEASURED_SOURCE]).to_parquet(path / T.CANOPY_HEIGHT)


def lattice_file(path: Path):
    frame([cell(CENTRE.x, CENTRE.y)]).to_parquet(path / T.CANOPY)


def test_measured_canopy_is_preferred_and_keeps_its_own_heights(tmp_path):
    """The point of the whole exercise: a measured height must survive the load.

    If this ever falls back to the constant, every crown on the map goes back to
    being the same height and nothing says so.
    """
    measured_file(tmp_path, height=6.0)
    lattice_file(tmp_path)

    canopy = T.load_canopy(tmp_path)
    assert list(canopy.height_source) == [T.MEASURED_SOURCE]
    assert list(canopy.height_m) == [6.0]


def test_the_lattice_is_a_real_fallback_and_not_a_slower_road_to_the_same_answer(tmp_path):
    """Without the measured file the old shape and the old flat height come back.

    Worth pinning: the fallback is the thing being replaced, so a test that let
    it quietly return crowns would be testing nothing.
    """
    lattice_file(tmp_path)

    canopy = T.load_canopy(tmp_path)
    assert list(canopy.height_source) == [T.CANOPY_SOURCE]
    assert list(canopy.height_m) == [T.TREE_HEIGHT_M]
    # Rounded, not the square it was stored as: a corner here is the sensor's
    # shape rather than a tree's. A box is five coordinates; this is a 16-gon.
    crown = canopy.geometry.iloc[0]
    assert shapely.get_num_coordinates(crown) > 5
    # And rounding it cost no canopy -- the whole point of deriving the radius
    # from the n-gon's own area rather than from pi r squared.
    assert crown.area == pytest.approx(T.CELL_M**2)


def test_no_canopy_at_all_is_a_normal_state(tmp_path):
    """Neither file is built on a fresh checkout, and the OSM rows stand alone."""
    assert T.load_canopy(tmp_path).empty


# --- the join with the tile export ------------------------------------------

def test_every_canopy_source_is_scored_dappled():
    """A crown is not a wall, and export_shadow_tiles.py decides which is which
    by this set alone. A source missing from it is drawn and weighted as solid
    masonry -- silently, and everywhere.
    """
    for source in (T.HEIGHT_SOURCE, T.CANOPY_SOURCE, T.MEASURED_SOURCE):
        assert source in T.CANOPY_SOURCES


def test_the_tree_constant_is_not_taller_than_the_city(tmp_path):
    """8 m was a guess sitting near the 95th percentile of measured canopy and
    doubling every tree shadow. Whatever it becomes, it is a typical tree.
    """
    assert 2.0 <= T.TREE_HEIGHT_M <= 6.0


def test_bare_trees_cast_nothing():
    assert T.leaf_on(dt.date(2026, 7, 1))
    assert not T.leaf_on(dt.date(2026, 1, 15))


# --- the builder ------------------------------------------------------------
# It imports rasterio, which is the `ml` group and not installed in CI.

spec = importlib.util.spec_from_file_location(
    "fetch_canopy_height",
    Path(__file__).resolve().parents[1] / "scripts" / "fetch_canopy_height.py",
)
try:
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
except ImportError:  # pragma: no cover - depends on which groups are installed
    build = None

needs_ml = pytest.mark.skipif(build is None, reason="needs the ml dependency group")


@needs_ml
def test_quadkey_names_the_tiles_the_published_index_names():
    """The index is a 15 MB GeoJSON of 56,145 footprints and is not shipped, so
    the names are derived instead. These two are what it actually holds over
    Astana -- if the derivation drifts, the script downloads nothing and finds
    no canopy anywhere.
    """
    assert build.quadkey(51.1605, 71.4704, 9) == "121302123"
    assert build.quadkey(51.2953, 71.4704, 9) == "121302121"


@needs_ml
def test_a_crown_carries_the_median_height_measured_inside_it():
    from rasterio.transform import from_origin

    heights = np.zeros((10, 10), dtype=np.uint8)
    # One clump, 4x4 m at 1 m a pixel, mostly 4 m with a single tall spike.
    heights[2:6, 2:6] = 4
    heights[3, 3] = 30

    shapes, tall = build.crowns(heights >= 2, heights, from_origin(0, 10, 1, 1))
    assert len(shapes) == 1
    # The median, not the mean: the mean would be dragged to 5.6 m by one pixel.
    assert tall == [4.0]
    assert shapes[0].area == pytest.approx(16.0)


@needs_ml
def test_a_speck_is_not_a_tree():
    from rasterio.transform import from_origin

    heights = np.zeros((10, 10), dtype=np.uint8)
    heights[5, 5] = 9          # 1 m2, under MIN_CROWN_M2
    assert build.crowns(heights >= 2, heights, from_origin(0, 10, 1, 1)) == ([], [])


@needs_ml
def test_crowns_touching_at_a_corner_are_measured_as_one_canopy():
    """Labelled 8-connected on purpose: there is no gap of sun between them, so
    they are one crown for the purpose of deciding how tall they are.

    They still come back as two polygons -- rasterio traces 4-connected, and a
    ring pinched to a point at the corner would be a degenerate geometry anyway.
    What matters is that one height was measured across both, not two.
    """
    from rasterio.transform import from_origin

    heights = np.zeros((12, 12), dtype=np.uint8)
    heights[2:5, 2:5] = 4
    heights[5:8, 5:8] = 10     # diagonally adjacent, and much taller
    shapes, tall = build.crowns(heights >= 2, heights, from_origin(0, 12, 1, 1))

    assert len(shapes) == 2
    # One median over the pair, so neither half is measured on its own.
    assert tall == [7.0, 7.0]


@needs_ml
def test_the_fill_height_is_weighted_by_ground_and_not_by_polygon():
    """Half the crowns found are specks of twenty-odd square metres. Counted one
    apiece they drag the median to 3 m while carrying 5% of the canopy; a walker
    stands under square metres.
    """
    heights = np.array([2.0, 2.0, 2.0, 8.0])
    specks = np.array([1.0, 1.0, 1.0, 1.0])
    ground = np.array([1.0, 1.0, 1.0, 500.0])

    assert build.weighted_median(heights, specks) == 2.0
    assert build.weighted_median(heights, ground) == 8.0
