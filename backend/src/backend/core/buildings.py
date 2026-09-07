"""Load the cached OSM footprints and give every building a height."""

from __future__ import annotations

import re
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import Point

from backend.config import LAT, LON

# Assumed storey height, for buildings tagged with levels but no height.
LEVEL_HEIGHT = 3.2


def parse_numeric(value: object) -> float:
    """Pull a leading number out of a messy OSM tag ('12', '12 m', '3,5')."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    match = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)", str(value))
    return float(match.group(1).replace(",", ".")) if match else np.nan


def load_buildings(cache_dir: Path, radius: float) -> gpd.GeoDataFrame:
    """Footprints within `radius` metres of the centre, each with a height_m.

    Height falls back through three sources: the `height` tag, then
    `building:levels` times a storey height, then a single storey. Most of
    Astana carries neither tag, so the last fallback covers the majority.
    """
    gdf = gpd.read_parquet(cache_dir / "astana_buildings.parquet")

    # The parquet is in a projected CRS (metres), so reproject the centre to
    # match before measuring distance -- degrees and metres do not compare.
    centre = gpd.GeoSeries([Point(LON, LAT)], crs=4326).to_crs(gdf.crs).iloc[0]
    gdf = gdf[gdf.geometry.distance(centre) <= radius].copy()

    levels = gdf["building:levels"].map(parse_numeric)
    gdf["height_m"] = (
        gdf["height"]
        .map(parse_numeric)
        .fillna(levels * LEVEL_HEIGHT)
        .fillna(LEVEL_HEIGHT)
    )
    return gdf
