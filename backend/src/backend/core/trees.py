"""Trees as canopy that casts a shadow, on the days it has leaves.

Trees reach the shadow model the same way buildings do -- a polygon and a
height -- so nothing in shadows.py needs to know the difference. What differs
is how much of each is known, and there are now three answers to that:

  chm_height    a crown cut from a 1 m canopy height map, carrying the height
                measured inside it. A shape and a number, neither invented.
  canopy_model  Sentinel-2 said canopy and the height map did not. A real
                place, at the city's median height -- and drawn as a rounded
                blob because a 10 m pixel has no edge worth believing.
  tree_default  OSM mapped a tree row. A real place, at a guessed crown width
                and the same median height.

Only the first is a measurement. Read the coverage note in fetch_trees.py
before trusting the third: inside the routing disc OSM holds about 1.2 km of
planting against 570 km of walk network, which is not enough to change a route
and never was -- the other two are what made trees matter here.
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

# The same thing built properly, by scripts/fetch_canopy_height.py: crowns cut
# from a 1 m canopy *height* map, each carrying the height measured inside it,
# with the Sentinel-2 detector filling only what that map missed. Preferred
# wherever it exists, and everything below about cells and rounding is the
# fallback for where it does not.
CANOPY_HEIGHT = "astana_canopy_height.parquet"

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
#
# This was 8.0 m, and 8.0 m was a guess made before there was anything to check
# it against. There is now: the 1 m height map measures 97% of the city's canopy
# area below 8 m, and its area-weighted median is 4 m -- so the old constant sat
# near the 95th percentile of Astana's actual canopy and roughly doubled every
# tree shadow on the map. Weighted by area rather than by crown, because half
# the crowns found are specks of twenty-odd square metres and a walker stands
# under square metres, not under polygons.
#
# Still a constant, and still only for the rows OSM mapped and the canopy the
# height map did not see -- but a measured one, and the same number the fill in
# fetch_canopy_height.py uses, so one street tree cannot cast two different
# shadows depending on which source found it.
TREE_HEIGHT_M = 4.0

# Marks every tree height as a guess, so the provenance column keeps meaning
# what it means for buildings and the UI can grey these the same way.
HEIGHT_SOURCE = "tree_default"

# Detected canopy is a guess twice over -- a model said there is a tree, and a
# constant said how tall. Worth its own name so the two are never confused with
# something somebody actually surveyed.
CANOPY_SOURCE = "canopy_model"

# Canopy whose height was measured rather than assumed. A guess once, not twice:
# a model still said there is a tree, but nothing here decided how tall it is.
MEASURED_SOURCE = "chm_height"

# Every source whose shade is dappled rather than solid. A wall stops all the
# light; a crown lets a good deal through, and the two should not be scored as
# the same thing.
CANOPY_SOURCES = frozenset({HEIGHT_SOURCE, CANOPY_SOURCE, MEASURED_SOURCE})

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

    gdf = within(gpd.read_parquet(path), radius)

    canopy = gdf.copy()
    canopy["geometry"] = gdf.geometry.buffer(CROWN_RADIUS_M)
    canopy["height_m"] = TREE_HEIGHT_M
    canopy["height_source"] = HEIGHT_SOURCE
    return canopy


def within(gdf: gpd.GeoDataFrame, radius: float | None) -> gpd.GeoDataFrame:
    """The rows inside `radius` of the city centre, or all of them if None.

    The router asks for a disc and the tile export asks for everything, and
    three loaders here were spelling the same filter out three times.
    """
    if radius is None:
        return gdf
    centre = gpd.GeoSeries([Point(LON, LAT)], crs=4326).to_crs(gdf.crs).iloc[0]
    return gdf[gdf.geometry.distance(centre) <= radius]


def load_canopy(cache_dir: Path, radius: float | None = None) -> gpd.GeoDataFrame:
    """Model-detected canopy, from the best source that has been built.

    Absent is a normal state for both -- each needs the ml dependency group and
    a trip to somebody's imagery -- so this returns empty rather than raising.
    The OSM trees stand on their own.

    The measured file is preferred and is not merely a better version of the
    other one: its polygons are crowns rather than pixels, so there is nothing
    to round, and each carries the height measured inside it rather than the
    constant. Falling back is a real fallback, not a slower path to the same
    answer -- the lattice comes back, and so does the flat height.
    """
    measured = cache_dir / CANOPY_HEIGHT
    if measured.exists():
        return within(gpd.read_parquet(measured), radius)

    path = cache_dir / CANOPY
    if not path.exists():
        return gpd.GeoDataFrame(geometry=[], crs=None)

    gdf = within(gpd.read_parquet(path), radius)
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
