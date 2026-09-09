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


def parse_numeric(value: object) -> float:
    """Pull a leading number out of a messy OSM tag ('12', '12 m', '3,5')."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    match = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)", str(value))
    return float(match.group(1).replace(",", ".")) if match else np.nan


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


def load_buildings(
    cache_dir: Path,
    radius: float | None = None,
    overrides_path: Path = HEIGHT_OVERRIDES,
) -> gpd.GeoDataFrame:
    """Footprints with a height_m, the whole cached city unless `radius` clips it.

    The cache covers all of Astana -- 50k footprints over 34 x 52 km. Pass a
    radius only when the caller genuinely works in one small place, as routing
    does; the map asks by viewport instead, through `in_view`.

    Height falls back through four sources, and `height_source` records which
    one won: a hand-entered override, then the `height` tag, then
    `building:levels` times a storey height, then a single storey. Most of
    Astana carries none of the first three, so the last fallback covers the
    majority -- which is exactly what `height_source` is there to make visible.
    """
    gdf = gpd.read_parquet(cache_dir / "astana_buildings.parquet")

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
    candidates = {
        "override": surveyed * LEVEL_HEIGHT,
        "tag": gdf["height"].map(parse_numeric),
        "levels": gdf["building:levels"].map(parse_numeric) * LEVEL_HEIGHT,
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
