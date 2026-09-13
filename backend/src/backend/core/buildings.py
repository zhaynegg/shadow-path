"""Load the cached OSM footprints and give every building a height."""

from __future__ import annotations

import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from backend.config import HEIGHT_OVERRIDES, LAT, LON

# Assumed storey height, for buildings tagged with levels but no height.
LEVEL_HEIGHT = 3.2

# Footprint size classes for the height prior. Same cuts as
# scripts/height_coverage.py, so the prior and the analysis of it agree.
SIZE_BINS = [0, 150, 500, 1500, np.inf]
SIZE_LABELS = ["s", "m", "l", "xl"]

# How many known buildings a (tag, size) group needs before its median is worth
# believing. At 10 the prior still reaches 97% of the untagged buildings, and
# the 66 groups it drops were resting on a handful of examples each.
MIN_GROUP = 10


def parse_numeric(value: object) -> float:
    """Pull a leading number out of a messy OSM tag ('12', '12 m', '3,5').

    Zero and below come back as nan, not as a number. Every caller is reading a
    height or a storey count, and there is no such thing as a building 0 m tall
    -- it is a typo or a placeholder somebody saved. Kept as 0.0 it ranks as a
    *measurement*, above both the prior and the fallback, and a hotel and three
    apartment blocks in Astana cast no shadow at all because OSM said
    `height=0`. A fifth had `building:levels=0`.

    Letting them fall through to the prior is not pretending to know the answer;
    it is declining to treat a data-entry slip as a survey.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    match = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)", str(value))
    if match is None:
        return np.nan
    number = float(match.group(1).replace(",", "."))
    return number if number > 0 else np.nan


def load_overrides(path: Path) -> dict[tuple[str, int], float]:
    """Hand-entered storey counts, keyed by OSM element type and id.

    A missing file is the normal state until somebody has surveyed something,
    and so is an empty one -- you touch the file before you fill it. Neither is
    an error. Duplicate keys are: they would quietly resolve to whichever row
    happened to come last, so refuse the file instead.
    """
    if not path.exists() or path.stat().st_size == 0:
        return {}

    frame = pd.read_csv(path)
    keys = list(zip(frame["osm_element"], frame["osm_id"]))
    if len(keys) != len(set(keys)):
        raise ValueError(f"{path} has duplicate (osm_element, osm_id) rows")

    levels = frame["levels"].map(parse_numeric)
    return {key: value for key, value in zip(keys, levels) if not np.isnan(value)}


def size_bin(gdf: gpd.GeoDataFrame) -> pd.Series:
    """Footprint area as a coarse size class. Needs a projected CRS (metres)."""
    return pd.cut(gdf.geometry.area, SIZE_BINS, labels=SIZE_LABELS)


def level_priors(
    gdf: gpd.GeoDataFrame, min_group: int = MIN_GROUP
) -> tuple[dict[tuple[str, str], float], dict[str, float]]:
    """Typical storey counts, learned from the buildings OSM does know about.

    Returns medians per (building tag, size class) and, as a second chance for
    combinations too thin to trust, medians per tag alone. A `school` the size
    of a house is a better guess from schools than from the city at large.

    Groups below `min_group` are dropped -- a median over three buildings is
    noise wearing a number's clothes.
    """
    known = pd.DataFrame(
        {
            "tag": gdf["building"].astype(str),
            "bin": size_bin(gdf),
            "levels": gdf["building:levels"].map(parse_numeric),
        }
    )
    # Storeys outside this range are typos, not buildings. NaN fails the test
    # too, which is how the unknown ones drop out.
    known = known[known["levels"].between(1, 60)]

    # observed=True or the categorical bins produce every tag x bin pair that
    # could exist, thousands of them empty.
    grouped = known.groupby(["tag", "bin"], observed=True)["levels"]
    by_group = grouped.median()[grouped.size() >= min_group]
    return by_group.to_dict(), known.groupby("tag")["levels"].median().to_dict()


def load_buildings(
    cache_dir: Path,
    radius: float | None = None,
    overrides_path: Path = HEIGHT_OVERRIDES,
) -> gpd.GeoDataFrame:
    """Footprints with a height_m, the whole cached city unless `radius` clips it.

    The cache covers all of Astana -- 50k footprints over 34 x 52 km. Pass a
    radius only when the caller genuinely works in one small place, as routing
    does; the map asks by viewport instead, through `in_view`.

    Height falls back through five sources, and `height_source` records which
    one won: a hand-entered override, the `height` tag, `building:levels`, a
    prior learned from similar buildings, and finally a single storey. Only the
    first three are measurements; `prior` and `fallback` are guesses, and the
    column exists so you can always ask how much of a map rests on them.
    """
    gdf = gpd.read_parquet(cache_dir / "astana_buildings.parquet")

    # Learn from the whole city, before any clip. Derived after one, a routing
    # call for a small disc would build city-wide priors out of whichever few
    # thousand buildings happened to fall inside it -- silently, and differently
    # for every caller.
    by_group, by_tag = level_priors(gdf)

    if radius is not None:
        # The parquet is in a projected CRS (metres), so reproject the centre to
        # match before measuring distance -- degrees and metres do not compare.
        centre = gpd.GeoSeries([Point(LON, LAT)], crs=4326).to_crs(gdf.crs).iloc[0]
        gdf = gdf[gdf.geometry.distance(centre) <= radius]

    gdf = gdf.copy()

    # One dict lookup per row. A merge would read more naturally and could
    # silently duplicate rows when the override file repeats a key -- inflating
    # the building count and double-counting shadows, with no error anywhere.
    overrides = load_overrides(overrides_path)
    keys = pd.Series(list(zip(gdf["element"], gdf["id"])), index=gdf.index)
    surveyed = keys.map(overrides) if overrides else pd.Series(np.nan, index=gdf.index)

    # Best source first. Overrides outrank even a `height` tag: surveying a
    # building by hand is something you only do to correct what OSM says.
    tags = gdf["building"].astype(str)
    prior = pd.Series(list(zip(tags, size_bin(gdf))), index=gdf.index).map(by_group)
    prior = prior.fillna(tags.map(by_tag))

    candidates = {
        "override": surveyed * LEVEL_HEIGHT,
        "tag": gdf["height"].map(parse_numeric),
        "levels": gdf["building:levels"].map(parse_numeric) * LEVEL_HEIGHT,
        "prior": prior * LEVEL_HEIGHT,
    }

    height = pd.Series(np.nan, index=gdf.index)
    source = pd.Series("fallback", index=gdf.index)
    for name, candidate in candidates.items():
        # Only rows still unresolved, so an earlier source is never overwritten.
        take = height.isna() & candidate.notna()
        height = height.where(~take, candidate)
        source = source.where(~take, name)

    gdf["height_m"] = height.fillna(LEVEL_HEIGHT)
    gdf["height_source"] = source
    return gdf
