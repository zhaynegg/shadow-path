"""Reading heights out of OSM tags, which are typed by hand and show it."""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from backend.config import CRS
from backend.core.buildings import neighbour_levels, parse_numeric


@pytest.mark.parametrize("raw, expected", [
    ("12", 12.0),
    ("12 m", 12.0),
    ("3,5", 3.5),      # comma decimal, which is how it is written locally
    ("  7.5  ", 7.5),
    ("24m", 24.0),
])
def test_reads_the_leading_number(raw, expected):
    assert parse_numeric(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [
    None,
    float("nan"),
    "ё",                              # a keyboard slip, twice over in Astana
    "Многофункциональный комплекс",   # a description typed into a numeric field
    "",
])
def test_unreadable_is_missing_not_zero(raw):
    """nan, so the height chain falls through to the next source.

    Returning 0.0 here would be worse than returning nothing: it is a number,
    so it wins the chain, and it means a building of no height.
    """
    assert np.isnan(parse_numeric(raw))


@pytest.mark.parametrize("raw", ["0", "0 m", "0.0", "0,0"])
def test_zero_is_missing_not_a_measurement(raw):
    """The bug this function exists to not have.

    Four Astana buildings carry `height=0` and a fifth `building:levels=0`, and
    none of the five has another tag to fall back on. Parsed as 0.0 the value
    ranks as a measurement -- above the prior, above the fallback, beaten only
    by a hand survey -- so a hotel and three apartment blocks stood 0 m tall and
    cast no shadow anywhere on the map. Nothing errored; they were simply gone.
    """
    assert np.isnan(parse_numeric(raw))


def microdistrict() -> gpd.GeoDataFrame:
    """A courtyard of nine-storey blocks with garages in between.

    The garages are deliberately the nearest things to the untagged block, and
    there are more of them, so an estimate that takes the nearest buildings of
    any kind gets dragged towards one storey. That mix is what the size
    restriction exists for -- and it is real, not contrived: this is the shape
    of every Soviet-planned microdistrict in Astana.
    """
    blocks, garages = [], []
    for i in range(8):
        x = 200 * i
        # 2,000 m2 -> the 'xl' size class.
        blocks.append(box(x, 300, x + 40, 350))
    for i in range(24):
        # 100 m2 -> 's', and sitting much closer to the untagged block below.
        x = 60 * i
        garages.append(box(x, 0, x + 10, 10))

    untagged = box(700, 295, 740, 345)  # 'xl', in among the blocks
    geometry = [*blocks, *garages, untagged]
    levels = [9.0] * len(blocks) + [1.0] * len(garages) + [np.nan]
    return gpd.GeoDataFrame({"geometry": geometry}, crs=CRS), pd.Series(levels)


def test_neighbour_reads_the_block_it_stands_in_not_the_garages():
    gdf, known = microdistrict()
    known.index = gdf.index

    answer = neighbour_levels(gdf, known)

    # The untagged building is the last row, and the only one asked about.
    assert answer.iloc[:-1].isna().all()
    assert answer.iloc[-1] == pytest.approx(9.0)


def test_every_untagged_building_gets_an_answer():
    """There is no second chance below this one in practice.

    `load_buildings` keeps the global prior beneath it in the chain, but on
    Astana's data this rung answers all 29,860 untagged buildings and the prior
    is never reached. If that ever stops being true, the prior catches it -- but
    silently, so this is the test that would notice.
    """
    gdf, known = microdistrict()
    known.index = gdf.index

    answer = neighbour_levels(gdf, known)

    assert answer[known.isna()].notna().all()


def test_a_single_tagged_building_still_answers():
    """k collapses to 1, which cKDTree returns with the k axis dropped.

    Left unhandled that shape change hands one median to every row at once, so
    a size class with a single tagged example in the whole city would quietly
    paint its neighbours with a scalar.
    """
    gdf = gpd.GeoDataFrame(
        {"geometry": [box(0, 0, 40, 50), box(100, 0, 140, 50), box(200, 0, 240, 50)]},
        crs=CRS)
    known = pd.Series([7.0, np.nan, np.nan])

    answer = neighbour_levels(gdf, known)

    assert answer.iloc[1] == pytest.approx(7.0)
    assert answer.iloc[2] == pytest.approx(7.0)
