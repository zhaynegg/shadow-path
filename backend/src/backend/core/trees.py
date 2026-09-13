"""Mapped trees as canopy that casts a shadow, on the days it has leaves.

Trees reach the shadow model the same way buildings do -- a polygon and a
height -- so nothing in shadows.py needs to know the difference. What is
different is the confidence: OSM has no height on a single Astana tree, so
every height here is a constant, and the crown width is a constant too.

Read the coverage note in scripts/fetch_trees.py before trusting any of this.
Inside the routing disc it is about 1.2 km of planting against 570 km of walk
network, which is not enough to change a route.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from backend.config import LAT, LON

TREES = "astana_trees.parquet"

# Canopy detected from Sentinel-2 by scripts/detect_trees.py. OSM has mapped
# 0.1% of the streets here; this reaches 62%. Already polygons, so unlike the
# OSM rows it needs no buffering.
CANOPY = "astana_canopy.parquet"

# Half the crown width of a mature boulevard tree. A guess, and the single
# number that decides how much ground a tree row covers -- 1.2 km of planting
# at 3 m either side is about 7,000 m2 of canopy.
CROWN_RADIUS_M = 3.0

# Astana street planting is mostly poplar, birch and elm, much of it young.
# A guess, and a deliberately modest one: at a 40 degree sun a metre of tree
# height is a metre of shadow, so overstating this invents shade.
TREE_HEIGHT_M = 8.0

# Marks every tree height as a guess, so the provenance column keeps meaning
# what it means for buildings and the UI can grey these the same way.
HEIGHT_SOURCE = "tree_default"

# Detected canopy is a guess twice over -- a model said there is a tree, and a
# constant said how tall. Worth its own name so the two are never confused with
# something somebody actually surveyed.
CANOPY_SOURCE = "canopy_model"

# Every source whose shade is dappled rather than solid. A wall stops all the
# light; a crown lets a good deal through, and the two should not be scored as
# the same thing.
CANOPY_SOURCES = frozenset({HEIGHT_SOURCE, CANOPY_SOURCE})

# How much of the sun a summer crown actually blocks. A guess, but a consequential
# one: canopy is now more than half the shadow area in the routing disc, so
# treating it as opaque was overstating shade across most of the network.
CANOPY_OPACITY = 0.7

# Leaf-on window for Astana, as (month, day). Leaf-out is early May and the
# fall is well under way by mid-October. Bare trees cast nothing worth routing
# around, and getting this wrong is worse than having no trees at all: the
# winter product is sun-seeking, so phantom canopy pushes a walker away from
# streets that are genuinely sunny.
LEAF_OUT = (5, 1)
LEAF_FALL = (10, 10)


def leaf_on(date: dt.date) -> bool:
    """Is there a canopy on this date?

    Year-agnostic: the window is a season, not a date range, so it holds for
    whichever year is being built. Both the tile export and the router call
    this -- two copies of a seasonal rule would agree only until one was tuned.
    """
    return LEAF_OUT <= (date.month, date.day) <= LEAF_FALL


def load_trees(cache_dir: Path, radius: float | None = None) -> gpd.GeoDataFrame:
    """Cached tree geometry as canopy polygons with a height.

    Lines and points are buffered to a crown, because cast_shadow sweeps a
    polygon and reads its rings -- a bare LineString has neither.

    `leaf_cycle=evergreen` is kept year-round; it is tagged on only a handful
    of features, but where somebody has said so it should be believed over the
    default season.
    """
    path = cache_dir / TREES
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing -- run scripts/fetch_trees.py")

    gdf = gpd.read_parquet(path)

    if radius is not None:
        centre = gpd.GeoSeries([Point(LON, LAT)], crs=4326).to_crs(gdf.crs).iloc[0]
        gdf = gdf[gdf.geometry.distance(centre) <= radius]

    canopy = gdf.copy()
    canopy["geometry"] = gdf.geometry.buffer(CROWN_RADIUS_M)
    canopy["height_m"] = TREE_HEIGHT_M
    canopy["height_source"] = HEIGHT_SOURCE
    return canopy


def load_canopy(cache_dir: Path, radius: float | None = None) -> gpd.GeoDataFrame:
    """Model-detected canopy, or nothing if it has not been run.

    Absent is a normal state -- the detector needs the ml dependency group and
    a trip to the imagery -- so this returns empty rather than raising. The OSM
    trees stand on their own.
    """
    path = cache_dir / CANOPY
    if not path.exists():
        return gpd.GeoDataFrame(geometry=[], crs=None)

    gdf = gpd.read_parquet(path)
    if radius is not None:
        centre = gpd.GeoSeries([Point(LON, LAT)], crs=4326).to_crs(gdf.crs).iloc[0]
        gdf = gdf[gdf.geometry.distance(centre) <= radius]

    gdf = gdf.copy()
    gdf["height_m"] = TREE_HEIGHT_M
    gdf["height_source"] = CANOPY_SOURCE
    return gdf


def shading_geometry(
    buildings: gpd.GeoDataFrame,
    date: dt.date,
    cache_dir: Path,
    radius: float | None = None,
) -> gpd.GeoDataFrame:
    """Everything that casts a shadow on `date`: buildings, plus canopy in season.

    Both callers go through here -- the tile export and the router -- so the
    map cannot end up drawing shade the route will not walk towards. That
    disagreement is the same bug the date already caused once.

    Returns the buildings frame itself out of season, not a copy: callers treat
    it as read-only and one of them hands out an lru_cached object.
    """
    if not leaf_on(date):
        return buildings

    columns = ["geometry", "height_m", "height_source"]
    parts = [buildings[columns], load_trees(cache_dir, radius).to_crs(buildings.crs)[columns]]

    # Both, not one or the other. The detector finds 75% of what OSM has
    # mapped, so OSM still carries the quarter it misses; shadow_field unions
    # everything anyway, so the overlap costs nothing.
    canopy = load_canopy(cache_dir, radius)
    if not canopy.empty:
        parts.append(canopy.to_crs(buildings.crs)[columns])

    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=buildings.crs)
