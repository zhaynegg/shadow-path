"""Find tree canopy in satellite imagery and write it as shadow-casting geometry.

    uv run --group ml python scripts/detect_trees.py

OSM has mapped 0.1% of the streets you route on. This fills the rest in from
Sentinel-2: ten-metre multispectral imagery, openly licensed, so what comes out
can actually be published on the map -- which is the reason this is not built on
Google or Bing tiles, whose terms bar derived datasets and display elsewhere.

The model is a gradient-boosted tree over per-pixel spectral features, trained
on what OSM already knows:

  positives   `natural=tree` and `natural=tree_row`
  negatives   building footprints, and -- the ones that matter -- grass,
              pitches and meadow. Without those the model learns "vegetation",
              flags every lawn in Astana, and covers the city in shade.

Two things it cannot do, and the output is labelled accordingly. It finds where
canopy is, not how tall it is, so every polygon still leaves with the default
height from core/trees.py. And it is scored on blocks of city, never on random
pixels: neighbouring pixels are nearly the same pixel, so a random split scores
the model on data it has effectively trained on.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.windows import from_bounds
from shapely.geometry import Point, shape
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

from backend.config import CACHE_DIR, CRS, LAT, LON

warnings.filterwarnings("ignore")

STAC = "https://earth-search.aws.element84.com/v1/search"

# Half-width of the study box around the city centre. Wide enough to reach the
# outer districts where most of OSM's trees actually are -- the centre has
# almost none, so a box drawn round the routing disc would have no positives.
STUDY_HALF_M = 10_000

# Summer, and nearly cloudless. Canopy is only separable from bare ground when
# it is in leaf, which is the same window core/trees.py gates shadows on.
SEASON = "2024-06-01T00:00:00Z/2024-08-31T23:59:59Z"
MAX_CLOUD = 5

# 10 m natively; the rest are 20 m and get resampled up. The red edge and both
# SWIR bands are the ones that separate woody canopy from grass -- without them
# this is an NDVI threshold with extra steps.
BANDS = ["blue", "green", "red", "nir", "rededge1", "swir16", "swir22"]

# Folds are blocks of city, not random pixels. Adjacent pixels are nearly the
# same measurement, so a random split trains and tests on the same trees.
BLOCK_M = 1000
FOLDS = 5

# Labels come from what OSM has bothered to map, which is not a random sample
# of the ground. Cap each class so the commonest does not simply outvote.
MAX_PER_CLASS = 60_000

# A tree row is a few metres wide against a ten-metre pixel. Buffering to half
# a pixel keeps a row to the pixels it genuinely dominates; wider would label
# the road beside it as canopy.
LABEL_BUFFER_M = 5.0

# Buildings are shrunk before being used as negatives: a footprint edge often
# has a tree overhanging it, and those pixels are not honest negatives.
BUILDING_ERODE_M = 5.0

# What OSM calls ground cover that is green but not a tree. These are the
# labels that stop the model painting every lawn as canopy.
GRASS_TAGS = {"landuse": ["grass", "meadow", "farmland"], "leisure": ["pitch", "garden"]}

OUT = "astana_canopy.parquet"


def find_scene() -> dict:
    """The least cloudy summer scene over the city."""
    query = {
        "collections": ["sentinel-2-l2a"],
        "intersects": {"type": "Point", "coordinates": [LON, LAT]},
        "datetime": SEASON,
        "query": {"eo:cloud_cover": {"lt": MAX_CLOUD}},
        "limit": 20,
    }
    request = urllib.request.Request(
        STAC, data=json.dumps(query).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        features = json.load(response)["features"]
    if not features:
        raise SystemExit(f"no scene under {MAX_CLOUD}% cloud in {SEASON}")
    return min(features, key=lambda f: f["properties"]["eo:cloud_cover"])


def study_bounds() -> tuple[float, float, float, float]:
    centre = gpd.GeoSeries([Point(LON, LAT)], crs=4326).to_crs(CRS).iloc[0]
    return (
        centre.x - STUDY_HALF_M, centre.y - STUDY_HALF_M,
        centre.x + STUDY_HALF_M, centre.y + STUDY_HALF_M,
    )


def read_stack(item: dict, bounds) -> tuple[np.ndarray, rasterio.Affine]:
    """Every band over the study box, on one 10 m grid.

    Read by byte range straight out of the cloud-optimised GeoTIFFs, so this
    pulls the 20 km box and not the 110 km tile. The 20 m bands are resampled
    to the 10 m grid on read, which is what `out_shape` does here.
    """
    layers, transform, shape_hw = [], None, None

    for band in BANDS:
        href = item["assets"][band]["href"]
        with rasterio.open(href) as src:
            window = from_bounds(*bounds, transform=src.transform)
            if shape_hw is None:
                shape_hw = (round(window.height), round(window.width))
                transform = src.window_transform(window)
            data = src.read(1, window=window, out_shape=shape_hw).astype(np.float32)
        layers.append(data)
        print(f"  {band:<10} {shape_hw[1]}x{shape_hw[0]}")

    stack = np.stack(layers)

    # Indices do the real separating work: NDVI for greenness, NDWI for water,
    # and the SWIR ratio for the woody-versus-herbaceous difference.
    _blue, green, red, nir, rededge, swir16, swir22 = stack
    eps = 1e-6
    ndvi = (nir - red) / (nir + red + eps)
    ndwi = (green - nir) / (green + nir + eps)
    nbr = (nir - swir22) / (nir + swir22 + eps)
    swir_ratio = swir16 / (swir22 + eps)
    rededge_ndvi = (nir - rededge) / (nir + rededge + eps)

    features = np.stack([*stack, ndvi, ndwi, nbr, swir_ratio, rededge_ndvi])
    return features, transform


def label_geometry(cache_dir: Path, place: str, bounds) -> tuple[gpd.GeoSeries, gpd.GeoSeries]:
    """Where OSM says there are trees, and where it says there are not."""
    box = gpd.GeoSeries.from_wkt(
        [(f"POLYGON(({bounds[0]} {bounds[1]},{bounds[2]} {bounds[1]},"
          f"{bounds[2]} {bounds[3]},{bounds[0]} {bounds[3]},{bounds[0]} {bounds[1]}))")],
        crs=CRS,
    ).iloc[0]

    trees = gpd.read_parquet(cache_dir / "astana_trees.parquet").to_crs(CRS)
    trees = trees[trees.intersects(box)]
    positive = trees.geometry.buffer(LABEL_BUFFER_M)

    buildings = gpd.read_parquet(cache_dir / "astana_buildings.parquet").to_crs(CRS)
    buildings = buildings[buildings.intersects(box)]
    roofs = buildings.geometry.buffer(-BUILDING_ERODE_M)
    roofs = roofs[~roofs.is_empty]

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir / "osmnx")
    grass = ox.features_from_place(place, tags=GRASS_TAGS)
    grass = grass[grass.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(CRS)
    grass = grass[grass.intersects(box)]

    # Grass is the class that matters. Trees overhang the edges of lawns and
    # pitches, so shrink those too before believing them.
    lawns = grass.geometry.buffer(-BUILDING_ERODE_M)
    lawns = lawns[~lawns.is_empty]

    print(f"  positives: {len(positive):,} tree features")
    print(f"  negatives: {len(roofs):,} roofs, {len(lawns):,} lawns and pitches")
    return positive, pd.concat([roofs, lawns])


def burn(geometry: gpd.GeoSeries, transform, shape_hw) -> np.ndarray:
    if geometry.empty:
        return np.zeros(shape_hw, dtype=bool)
    return rasterize(
        [(g, 1) for g in geometry if not g.is_empty],
        out_shape=shape_hw, transform=transform, fill=0, dtype="uint8",
    ).astype(bool)


def blocks_of(rows, cols, transform) -> np.ndarray:
    """A 1 km block id per pixel, so folds are places rather than scattered pixels."""
    xs = transform.c + cols * transform.a
    ys = transform.f + rows * transform.e
    return (np.floor(xs / BLOCK_M).astype(np.int64) * 100_000
            + np.floor(ys / BLOCK_M).astype(np.int64))


def spatially_scored(X, y, groups, rng) -> tuple[float, float]:
    """Out-of-fold AUC and average precision, folds dealt out by city block."""
    unique = np.unique(groups)
    rng.shuffle(unique)
    fold_of_block = {block: i % FOLDS for i, block in enumerate(unique)}
    fold = np.array([fold_of_block[g] for g in groups])

    predicted = np.zeros(len(y))
    for k in range(FOLDS):
        test = fold == k
        if test.all() or not test.any():
            continue
        model = HistGradientBoostingClassifier(max_iter=200, random_state=0)
        model.fit(X[~test], y[~test])
        predicted[test] = model.predict_proba(X[test])[:, 1]
    return roc_auc_score(y, predicted), average_precision_score(y, predicted)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--place", default="Astana, Kazakhstan")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="canopy probability above which a pixel becomes canopy")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    bounds = study_bounds()

    item = find_scene()
    print(f"scene {item['id']}  cloud {item['properties']['eo:cloud_cover']:.1f}%  "
          f"{item['properties']['datetime'][:10]}")
    features, transform = read_stack(item, bounds)
    _, height, width = features.shape

    print("\nlabels")
    positive, negative = label_geometry(args.cache_dir, args.place, bounds)
    is_tree = burn(positive, transform, (height, width))
    is_not = burn(negative, transform, (height, width)) & ~is_tree

    rows, cols = np.mgrid[0:height, 0:width]
    flat = features.reshape(len(features), -1).T

    def sample(mask):
        index = np.flatnonzero(mask.ravel())
        if len(index) > MAX_PER_CLASS:
            index = rng.choice(index, MAX_PER_CLASS, replace=False)
        return index

    index = np.concatenate([sample(is_tree), sample(is_not)])
    X = flat[index]
    y = np.concatenate([np.ones(len(sample(is_tree))), np.zeros(len(index) - len(sample(is_tree)))])
    y = np.r_[np.ones(is_tree.ravel()[index].sum()), np.zeros(0)] if False else is_tree.ravel()[index].astype(int)
    groups = blocks_of(rows.ravel()[index], cols.ravel()[index], transform)

    keep = np.isfinite(X).all(axis=1)
    X, y, groups = X[keep], y[keep], groups[keep]
    print(f"  {int(y.sum()):,} canopy pixels, {int((1 - y).sum()):,} not, "
          f"{len(np.unique(groups)):,} blocks")

    print("\nspatial cross-validation")
    auc, ap = spatially_scored(X, y, groups, rng)
    print(f"  ROC AUC {auc:.3f}   average precision {ap:.3f}   (base rate {y.mean():.3f})")

    print("\nfitting on everything and predicting the city")
    model = HistGradientBoostingClassifier(max_iter=200, random_state=0).fit(X, y)
    finite = np.isfinite(flat).all(axis=1)
    probability = np.zeros(len(flat), dtype=np.float32)
    probability[finite] = model.predict_proba(flat[finite])[:, 1]
    canopy = (probability.reshape(height, width) >= args.threshold).astype(np.uint8)
    print(f"  {canopy.sum():,} canopy pixels "
          f"({100 * canopy.mean():.2f}% of the study box, {canopy.sum() * 100 / 1e4:.0f} ha)")

    polygons = [shape(geom) for geom, value in
                shapes(canopy, mask=canopy.astype(bool), transform=transform) if value == 1]
    out = gpd.GeoDataFrame(geometry=polygons, crs=CRS)
    path = args.cache_dir / OUT
    out.to_parquet(path)
    print(f"\n{len(out):,} canopy polygons -> {path}")


if __name__ == "__main__":
    main()
