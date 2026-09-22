"""Build the canopy layer from measured tree heights, not from a 10 m lattice.

    uv run --group ml python scripts/fetch_canopy_height.py

scripts/detect_trees.py finds canopy in Sentinel-2, and Sentinel-2 is 10 m. A
street tree's crown is smaller than one of those pixels, so core/trees.py can
only draw each lit cell as a disc of the cell's own area at the cell's centre --
every tree the same size, on the sensor's grid, up to 7 m from wherever it
actually stands. No better classifier fixes that; the pixel is the limit.

This reads a source that is not at that limit. Meta and WRI published a global
canopy *height* map on a 1 m grid, derived from Maxar imagery by a DINOv2 model
calibrated against GEDI lidar, under CC-BY-4.0 -- so unlike Google or Bing
tiles, what comes out of it can be published on this map. At Astana's latitude
its 1.19 m web-mercator pixel is 0.75 m on the ground.

Two sources go out, in one file, and the difference between them is the point:

  chm_height    the height map's own crowns, each carrying the median height
                measured inside it. A real shape, and a real height.
  canopy_model  what Sentinel-2 found and the height map did not, and only
                that -- subtracted in raster space so the two cannot overlap.

The subtraction is what makes this a combination rather than a replacement.
shadow_field unions everything it is given, so an 8 m blob sitting on top of a
measured 4 m crown simply wins, and the measurement is thrown away. Cut the
overlap out and each source describes the ground it is actually better at.

Why keep Sentinel-2 at all: the height map's imagery is credited "© 2016
Maxar", against a Sentinel-2 scene from August 2024, in a city that has been
planting hard in between. Roughly half the canopy the detector finds is not in
the height map, and some real part of that is simply newer than the imagery.

What this still cannot do: it has no more idea than the old path did which
species it is looking at, and a height map cannot tell a dense hedge from a
small tree. Every polygon is canopy, and canopy is scored dappled -- see
CANOPY_OPACITY in core/trees.py.
"""

from __future__ import annotations

import argparse
import math
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import shapely
from rasterio import features
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import from_bounds
from scipy import ndimage
from shapely.geometry import Point, shape

from backend.config import CACHE_DIR, CRS, LAT, LON
from backend.core.shadows import SIMPLIFY_M
from backend.core.trees import round_cells

warnings.filterwarnings("ignore")

# The published tiles, named by web-mercator quadkey at zoom 9. Deriving the
# name rather than shipping the index: the index is a 15 MB GeoJSON of 56,145
# footprints, and four of them are over Astana.
CHM_BASE = ("https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/"
            "alsgedi_global_v6_float/chm")
CHM_ZOOM = 9

# The same box scripts/detect_trees.py studies, so the two layers cover the same
# ground and the subtraction below is not quietly cutting one off at an edge.
STUDY_HALF_M = 10_000

# Below this, vegetation is not something a walker walks under. It is also
# where the height map is least trustworthy -- its stated mean absolute error is
# 2.8 m, so a pixel reading 1 m is not reliably distinguishable from bare
# ground. Crowns keep their measured height either way, so this threshold
# decides what counts as canopy, never how much shade it throws.
MIN_HEIGHT_M = 2.0

# A young street tree is a crown 2-3 m across, which is 4-7 m2. Anything under
# this is a speck: a bush, a hedge corner, or the model's noise.
MIN_CROWN_M2 = 4.0

# The grid everything is resampled onto, in the project's metres. Measured
# against the native 0.75 m over a 3 km box, this changes the polygon count by
# 0.4% and the canopy area by nothing -- the count is set by how many clumps of
# vegetation there are, not by how finely each one is sampled -- while costing
# half the memory. See the tuning note in the README.
WORK_RES_M = 1.0

# Processed a square at a time, because the study box at 1 m is 400 million
# pixels and labelling it whole would want about 1.6 GB for the label array
# alone. A crown is at most ~15 m across, so a clump straddling a chunk edge is
# rare, and when it happens it becomes two polygons each honestly describing its
# own half rather than one polygon describing neither.
CHUNK_M = 2000

CANOPY_IN = "astana_canopy.parquet"
OUT = "astana_canopy_height.parquet"

# What each row's height came from. Kept distinct so nothing downstream can
# confuse a measurement with the fallback, and so the README's provenance
# columns keep meaning what they mean for buildings.
MEASURED = "chm_height"
FILLED = "canopy_model"


def quadkey(lat: float, lon: float, zoom: int) -> str:
    """The web-mercator quadkey of the tile holding a point."""
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    key = ""
    for i in range(zoom, 0, -1):
        bit = 1 << (i - 1)
        key += str((1 if x & bit else 0) + (2 if y & bit else 0))
    return key


def tiles_over(box4326) -> list[str]:
    """Every published tile the study box touches.

    Walked over the corners rather than sampled: at this zoom a tile is about
    78 km across and the box is 20 km, so it meets at most four -- but which
    four depends on where the box falls against the grid, and a box that
    straddles a corner meets all of them.
    """
    minx, miny, maxx, maxy = box4326.bounds
    found = {quadkey(lat, lon, CHM_ZOOM)
             for lat in (miny, (miny + maxy) / 2, maxy)
             for lon in (minx, (minx + maxx) / 2, maxx)}
    return sorted(found)


def read_chunk(sources: dict, bounds, size: int) -> np.ndarray:
    """Canopy height over one chunk, on the project's grid, in metres.

    Composed with a maximum across tiles: they do not overlap, and everything
    outside a given tile reads back as zero, so the maximum is simply "whichever
    tile actually covers this pixel".
    """
    grid = from_origin(bounds[0], bounds[3], WORK_RES_M, WORK_RES_M)
    out = np.zeros((size, size), dtype=np.uint8)

    for src in sources.values():
        merc = transform_bounds(CRS, src.crs, *bounds)
        # Nothing to read where the chunk misses the tile entirely.
        if (merc[2] <= src.bounds.left or merc[0] >= src.bounds.right
                or merc[3] <= src.bounds.bottom or merc[1] >= src.bounds.top):
            continue

        window = from_bounds(*merc, transform=src.transform)
        # boundless, so the array matches the window that was asked for even
        # where the tile runs out. A clipped window returns a smaller array than
        # its transform describes, which slides every pixel by what was cut.
        patch = src.read(1, window=window, boundless=True, fill_value=0)

        warped = np.zeros((size, size), dtype=np.uint8)
        reproject(patch, warped,
                  src_transform=src.window_transform(window), src_crs=src.crs,
                  dst_transform=grid, dst_crs=CRS, resampling=Resampling.bilinear)
        np.maximum(out, warped, out=out)

    return out


def crowns(mask: np.ndarray, heights: np.ndarray, grid) -> tuple[list, list]:
    """Clumps of canopy as polygons, each with the median height inside it.

    One height per clump rather than per pixel, and a clump rather than a
    height band: a crown is taller in the middle, so banding by height cuts
    every tree into concentric rings -- more polygons, and not one of them the
    shape of anything real. The median is the honest summary of a crown, and it
    errs small against the mean wherever a clump has a tall spike in it.
    """
    if not mask.any():
        return [], []

    # 8-connected: a crown touching its neighbour diagonally is one canopy, and
    # for a shadow that is the truth -- there is no gap of sun between them.
    labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=int))
    if not count:
        return [], []

    index = np.arange(1, count + 1)
    median = ndimage.labeled_comprehension(
        heights, labels, index, np.median, np.float64, 0.0)

    shapes, tall = [], []
    for geom, label in features.shapes(labels.astype(np.int32), mask=mask, transform=grid):
        polygon = shape(geom)
        if polygon.area < MIN_CROWN_M2:
            continue
        polygon = polygon.simplify(SIMPLIFY_M)
        if polygon.is_empty or polygon.area < MIN_CROWN_M2:
            continue
        shapes.append(polygon)
        tall.append(float(median[int(label) - 1]))
    return shapes, tall


def weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    """The value at which half the weight lies below.

    Plain `np.median` answers a question about polygons; this one answers the
    question about ground, which is what a shadow is cast over.
    """
    if not len(values):
        return 0.0
    order = np.argsort(values)
    share = np.cumsum(weights[order]) / weights.sum()
    return float(values[order][np.searchsorted(share, 0.5)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--half", type=float, default=STUDY_HALF_M,
                        help="half-width of the study box, in metres")
    args = parser.parse_args()

    centre = gpd.GeoSeries([Point(LON, LAT)], crs=4326).to_crs(CRS).iloc[0]
    box = shapely.box(centre.x - args.half, centre.y - args.half,
                      centre.x + args.half, centre.y + args.half)
    box4326 = gpd.GeoSeries([box], crs=CRS).to_crs(4326).iloc[0]

    names = tiles_over(box4326)
    print(f"study box {2 * args.half / 1000:.0f} x {2 * args.half / 1000:.0f} km")
    print(f"height tiles: {', '.join(names)}")

    # Sentinel-2's answer, to be cut down to only what the height map missed.
    #
    # Rounded first, by the same core/trees.py routine the old load path used,
    # and for the reason recorded there: the detector returns one square per lit
    # 10 m pixel, and a square edge is the sensor's shape rather than a tree's.
    # Subtracting before rounding would have shipped that lattice to the map
    # with its corners intact -- blockier than the thing being replaced, and
    # claiming the canopy stops exactly on a pixel boundary. What the height map
    # bites out of these blobs keeps its own edge, which is the one edge here
    # that is real.
    s2_path = args.cache_dir / CANOPY_IN
    s2_blobs = None
    if not s2_path.exists():
        print(f"  {CANOPY_IN} absent -- the height map will stand on its own")
    else:
        raw = gpd.read_parquet(s2_path).to_crs(CRS)
        s2_blobs = round_cells(raw.geometry.values)
        s2_index = shapely.STRtree(s2_blobs)
        print(f"  {len(raw):,} Sentinel-2 cells -> {len(s2_blobs):,} rounded blobs")

    sources = {name: rasterio.open(f"{CHM_BASE}/{name}.tif") for name in names}
    try:
        print(f"  {sources[names[0]].transform.a:.2f} mercator m per pixel "
              f"= {sources[names[0]].transform.a * math.cos(math.radians(LAT)):.2f} m "
              f"on the ground\n")

        size = int(CHUNK_M / WORK_RES_M)
        steps = np.arange(box.bounds[0], box.bounds[2], CHUNK_M)
        rows_ = np.arange(box.bounds[1], box.bounds[3], CHUNK_M)
        total = len(steps) * len(rows_)

        measured, measured_h, filled = [], [], []
        for n, (x0, y0) in enumerate(((x, y) for y in rows_ for x in steps), start=1):
            bounds = (x0, y0, x0 + CHUNK_M, y0 + CHUNK_M)
            grid = from_origin(bounds[0], bounds[3], WORK_RES_M, WORK_RES_M)

            heights = read_chunk(sources, bounds, size)
            mask = heights >= MIN_HEIGHT_M

            shapes_, tall = crowns(mask, heights, grid)
            measured.extend(shapes_)
            measured_h.extend(tall)

            # Sentinel-2, minus every pixel the height map already called
            # canopy. Done on the raster rather than with a geometric
            # difference: 78,000 blobs against 150,000 crowns is an overlay
            # nobody needs, and the two are already on one grid here.
            if s2_blobs is not None:
                chunk_box = shapely.box(*bounds)
                near = s2_index.query(chunk_box)
                if len(near):
                    clipped = shapely.intersection(s2_blobs[near], chunk_box)
                    burnt = features.rasterize(
                        [(g, 1) for g in clipped if not g.is_empty],
                        out_shape=(size, size), transform=grid, dtype="uint8").astype(bool)
                    only = burnt & ~mask
                    for geom, _ in features.shapes(
                            only.astype(np.uint8), mask=only, transform=grid):
                        polygon = shape(geom).simplify(SIMPLIFY_M)
                        if not polygon.is_empty and polygon.area >= MIN_CROWN_M2:
                            filled.append(polygon)

            if n % 20 == 0 or n == total:
                print(f"  chunk {n:>3}/{total}   "
                      f"{len(measured):>7,} measured   {len(filled):>7,} filled")
    finally:
        for src in sources.values():
            src.close()

    # The fill height is what was actually measured, not the 8 m the old path
    # guessed. These polygons are canopy the height map did not see, much of it
    # planted since its imagery, so the city's own canopy is a better guess
    # about them than any number chosen by hand -- and it is the only number
    # here that moves when the city's trees do.
    #
    # Weighted by area, which is not fussiness. Half of the crowns found are
    # specks of 20-odd square metres, and they drag the plain median down to
    # 3 m while carrying 5% of the canopy; three quarters of the area is 4-8 m
    # tall. A walker stands under square metres, not under polygons.
    fill = weighted_median(np.array(measured_h), shapely.area(np.array(measured, dtype=object)))

    frame = gpd.GeoDataFrame(
        {
            "geometry": measured + filled,
            "height_m": measured_h + [fill] * len(filled),
            "height_source": [MEASURED] * len(measured) + [FILLED] * len(filled),
        },
        crs=CRS,
    )

    path = args.cache_dir / OUT
    frame.to_parquet(path)

    area = frame.area.groupby(frame.height_source).sum() / 1e6
    print(f"\n{len(frame):,} polygons, "
          f"{shapely.get_num_coordinates(frame.geometry.values).sum():,} vertices")
    for source, km2 in area.items():
        rows = int((frame.height_source == source).sum())
        print(f"  {source:<13} {rows:>7,} polygons  {km2:6.2f} km2")
    if measured_h:
        areas = shapely.area(np.array(measured, dtype=object))
        print(f"\nmeasured crown height: {np.median(measured_h):.1f} m per crown, "
              f"{fill:.1f} m per square metre of canopy")
        for lo, hi in ((2, 4), (4, 8), (8, 99)):
            band = (np.array(measured_h) >= lo) & (np.array(measured_h) < hi)
            print(f"  {lo:>2}-{hi:<2} m : {100 * areas[band].sum() / areas.sum():5.1f}% of canopy area")
        print("  the old path gave every one of these a flat 8.0 m")
        print(f"  canopy the height map missed is filled at {fill:.1f} m")
    print(f"\n-> {path}")


if __name__ == "__main__":
    main()
