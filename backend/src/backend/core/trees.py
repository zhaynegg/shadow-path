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
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Point

from backend.config import LAT, LON

TREES = "astana_trees.parquet"

# Canopy detected from Sentinel-2 by scripts/detect_trees.py. OSM has mapped
# 0.1% of the streets here; this reaches 62%. Already polygons, so unlike the
# OSM rows it needs no buffering -- but it does need rounding. See below.
CANOPY = "astana_canopy.parquet"

# Sentinel-2 resolves 10 m, and the detector returns one square per lit pixel,
# so the cache is a lattice: 77,654 polygons, three quarters of them a single
# 10 x 10 m square, every corner on the same grid. That is the sensor's shape,
# not a tree's, and it survives all the way onto the map -- a boulevard of
# poplars drawn as a staircase of boxes.
CELL_M = 10.0

# Segments per quarter circle, so 4 draws a crown as a 16-gon. shapely's default
# of 8 spends 51 vertices where the pixel square spent 5, and every one of them
# is paid for again downstream -- in cast_shadow's hull, in the city-wide union,
# and in every edge intersection scored against it. That is what took a tile
# stamp from ~37s to ~4m15s. Measured city-wide against the squares' 28.1 km2:
#
#     quad_segs=8   32-gon   7.2M vertices
#     quad_segs=4   16-gon   3.6M           <- here
#     quad_segs=2    8-gon   2.3M
#
# This is now purely a cost and detail knob: the radius below is derived from
# whatever it is set to, so lowering it coarsens the outline without eating
# canopy. A 16-gon of this size strays 0.11 m from a true circle, and the tiles
# are simplified at SIMPLIFY_M = 1.0 m on the way out, so the finer arcs were
# being computed at great expense and then thrown away.
CROWN_QUAD_SEGS = 4

# The radius that gives the polygon we actually draw the cell's own 100 m2.
#
# Worth being exact about, because the obvious version is wrong: buffer()
# inscribes its n-gon in the circle of the radius it is handed, and an inscribed
# n-gon is always the smaller of the two. Deriving this from pi*r^2 drew 97.45 m2
# per crown at quad_segs=4, and 90.03 at quad_segs=2 -- so coarsening the outline
# quietly ate canopy, and the two knobs were not independent at all. The n-gon's
# own area leaves them independent:
#
#     area of a regular n-gon = (n/2) r^2 sin(2pi/n),  n = 4 * quad_segs
#
# What remains is the honest part. City-wide the crowns still come out a few
# percent under the squares, because neighbours overlap where the squares merely
# touched and the diagonal corners between four cells go uncovered. Erring small
# is the right direction -- every other guess in this file is written to avoid
# inventing shade.
CROWN_FROM_CELL_M = float(np.sqrt(
    CELL_M**2 / (2 * CROWN_QUAD_SEGS * np.sin(np.pi / (2 * CROWN_QUAD_SEGS)))))

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

    rounded = gpd.GeoDataFrame(geometry=round_cells(gdf.geometry.values), crs=gdf.crs)
    rounded["height_m"] = TREE_HEIGHT_M
    rounded["height_source"] = CANOPY_SOURCE
    return rounded


def cell_centres(geoms) -> np.ndarray:
    """The centre of every 10 m cell the detector lit.

    The polygons are unions of grid cells and their bounds sit on the lattice,
    so stepping from `minx + CELL/2` lands on centres exactly -- no need to know
    where the grid's origin is, only that a polygon is made of whole cells.
    Multi-cell blobs are not always rectangles, so each candidate is tested
    against the polygon rather than assumed.
    """
    found = []
    for geom in geoms:
        minx, miny, maxx, maxy = geom.bounds
        x, y = np.meshgrid(np.arange(minx + CELL_M / 2, maxx, CELL_M),
                           np.arange(miny + CELL_M / 2, maxy, CELL_M))
        points = shapely.points(x.ravel(), y.ravel())
        found.append(points[shapely.intersects(geom, points)])
    return np.concatenate(found) if found else np.array([])


def round_cells(geoms) -> np.ndarray:
    """Pixel squares in, round crowns out.

    A crown at every cell centre, merged back into blobs. Merging is what keeps
    this affordable: a run of trees becomes one rounded blob rather than the
    dozen overlapping discs it was built from, so the caster count comes out
    unchanged -- 77,654 squares in, 77,654 blobs out. The count is not the cost,
    though: those blobs carry 6.7x the vertices the squares did, and that is
    paid again at every step that touches them. See CROWN_QUAD_SEGS.

    Dissolving is free of meaning here: every crown carries the same guessed
    height, so there is nothing to lose by merging them.
    """
    if len(geoms) == 0:
        return np.array([])

    crowns = shapely.buffer(cell_centres(geoms), CROWN_FROM_CELL_M,
                            quad_segs=CROWN_QUAD_SEGS)
    merged = shapely.union_all(crowns)
    return np.array(merged.geoms if hasattr(merged, "geoms") else [merged])


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
