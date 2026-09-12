"""Does what the neighbours look like tell you how tall a building is?

    uv run python scripts/height_neighbours.py

The (type x size) prior in height_coverage.py scores MAE 0.96 storeys overall
and MAE 5.10 on buildings of five storeys or more -- and the tall ones are the
whole product, since they cast every shadow worth routing around. This measures
one new feature against that same baseline: the median storey count of the
nearest buildings already tagged in OSM -- both as it comes, and restricted to
neighbours of a similar footprint size.

The feature is worth trying because of how Astana was built. Soviet-planned
microdistricts go up as uniform series, so a nine-storey panel block is usually
surrounded by other nine-storey panel blocks. Nothing has to be downloaded to
find that out -- it is already in the footprints.

Two things here are deliberate, because both flatter a score that is not real:

  Neighbours come only from the training fold. Drawn from every tagged building
  instead, a test building can read its answer off the courtyard twin sitting
  in the test fold beside it, and the feature scores itself.

  Folds are blocks of city, not random rows -- which is also why this does not
  reuse the 70/30 split in height_coverage.py. Split at random and halves of
  the same terrace land on both sides, so the model is scored on buildings it
  has, for all practical purposes, already seen. Run --random to see the size
  of that lie.
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

# Same loader, same prior, same tall-building threshold as the baseline. A
# re-implemented baseline is not a baseline: the only honest comparison is
# against the code whose numbers are already written down.
from height_coverage import TALL_STOREYS, load_buildings
from scipy.spatial import cKDTree

warnings.filterwarnings("ignore")

# Fold size. Big enough that a whole microdistrict falls inside one block, so
# training never sees the courtyard it is about to be tested on; small enough
# that five folds still cover the city several times over.
BLOCK_M = 1000

FOLDS = 5

# How many tagged neighbours to take the median of. Small enough to stay inside
# one series of blocks, large enough that a single mistagged building nearby
# cannot swing the answer.
NEIGHBOURS = 8

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = REPO / "data" / "cache"


def blocks(gdf: gpd.GeoDataFrame, size: float) -> pd.Series:
    """A grid-cell id per building, so folds can be places rather than rows."""
    centre = gdf.geometry.centroid
    return pd.Series(
        np.floor(centre.x / size).astype(int).astype(str)
        + "_"
        + np.floor(centre.y / size).astype(int).astype(str),
        index=gdf.index,
    )


def assign_folds(groups: pd.Series, folds: int, rng: np.random.Generator) -> np.ndarray:
    """Deal whole groups into folds, so a group is never split across them."""
    unique = np.array(sorted(set(groups)))
    rng.shuffle(unique)
    lookup = {group: index % folds for index, group in enumerate(unique)}
    return groups.map(lookup).to_numpy()


def neighbour_median(
    train: gpd.GeoDataFrame, test: gpd.GeoDataFrame, k: int
) -> np.ndarray:
    """Median storeys of the k nearest *training* buildings, for each test row.

    k is clipped to the training fold's size. It never binds on a real city,
    but a fold small enough to have fewer than k buildings would otherwise make
    cKDTree return its "no neighbour" sentinel index and quietly poison the
    median with it.
    """
    levels = train["levels"].to_numpy(float)
    tree = cKDTree(np.c_[train.geometry.centroid.x, train.geometry.centroid.y])
    points = np.c_[test.geometry.centroid.x, test.geometry.centroid.y]
    _, index = tree.query(points, k=min(k, len(train)))
    return np.median(levels[np.atleast_2d(index)], axis=1)


def neighbour_median_grouped(
    train: gpd.GeoDataFrame, test: gpd.GeoDataFrame, k: int, key: str
) -> np.ndarray:
    """Neighbour median, but only counting neighbours in the same `key` group.

    The unrestricted version takes the k nearest buildings of any kind, and a
    nine-storey panel block's nearest buildings include garages, kiosks and
    transformer huts. The median over that mix falls back towards low-rise --
    the same failure the global prior already has, just measured locally.
    Grouping by footprint size class asks the narrower question: how tall are
    the buildings of roughly this size around here?

    Groups missing from a training fold come back as nan, for the caller to
    fill however it likes. Guessing here would hide how often it happens.
    """
    out = np.full(len(test), np.nan)
    key_of = test[key].to_numpy()
    for group, rows in train.groupby(key, observed=True):
        mask = key_of == group
        if mask.any() and len(rows):
            out[mask] = neighbour_median(rows, test[mask], k)
    return out


def type_size_prior(train: gpd.GeoDataFrame, test: gpd.GeoDataFrame) -> np.ndarray:
    """The existing baseline: median levels per (building type, size class)."""
    medians = train.groupby(["building", "size_class"], observed=True)["levels"].median()
    fallback = train["levels"].median()
    pred = test.set_index(["building", "size_class"]).index.map(medians).to_numpy(float)
    return np.where(np.isnan(pred), fallback, pred)


def cross_validate(
    labelled: gpd.GeoDataFrame, fold_of: np.ndarray, folds: int
) -> dict[str, np.ndarray]:
    """Out-of-fold predictions for every candidate, plus the truth beside them.

    Held out-of-fold rather than averaged per fold: one array of every
    prediction the models made on data they had not seen, scored once.
    """
    truth = labelled["levels"].to_numpy(float)
    names = ("prior", "neighbour", "neighbour by size")
    out = {name: np.full(len(labelled), np.nan) for name in names}

    for fold in range(folds):
        test_mask = fold_of == fold
        train, test = labelled[~test_mask], labelled[test_mask]
        if len(test) == 0 or len(train) == 0:
            continue
        out["prior"][test_mask] = type_size_prior(train, test)
        out["neighbour"][test_mask] = neighbour_median(train, test, NEIGHBOURS)
        out["neighbour by size"][test_mask] = neighbour_median_grouped(
            train, test, NEIGHBOURS, "size_class")

    # A size class absent from a training fold leaves a hole. Filling it from
    # the unrestricted neighbours keeps every predictor scored on the same rows
    # -- otherwise the restricted one quietly competes on an easier subset.
    gaps = np.isnan(out["neighbour by size"])
    out["neighbour by size"][gaps] = out["neighbour"][gaps]
    if gaps.any():
        print(f"  ({gaps.sum():,} rows had no same-size neighbour, fell back)")

    # Does the local answer add anything to the global one, or just repeat it?
    out["prior + by size"] = (out["prior"] + out["neighbour by size"]) / 2
    out["truth"] = truth
    return out


def report(predictions: dict[str, np.ndarray], label: str) -> None:
    truth = predictions["truth"]
    tall = truth >= TALL_STOREYS

    print(f"\n=== {label} ===")
    print(f"  n={len(truth):,}   of which tall (>={TALL_STOREYS} storeys) n={tall.sum():,}\n")
    print(f"{'predictor':<22}{'MAE':>8}{'within 1':>11}{'MAE tall':>11}{'within 1 tall':>15}")
    print("-" * 67)

    for name in ("prior", "neighbour", "neighbour by size", "prior + by size"):
        err = np.abs(truth - predictions[name])
        print(
            f"{name:<22}{err.mean():>8.2f}{100 * (err <= 1).mean():>10.1f}%"
            f"{err[tall].mean():>11.2f}{100 * (err[tall] <= 1).mean():>14.1f}%"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--place", default="Astana, Kazakhstan")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--random", action="store_true",
                        help="also score under a random split, to show what the leak is worth")
    args = parser.parse_args()

    gdf = load_buildings(args.place, args.cache_dir)
    labelled = gdf[gdf["levels"].notna()].copy()
    rng = np.random.default_rng(args.seed)

    print(f"{len(gdf):,} footprints, {len(labelled):,} with building:levels")
    group = blocks(labelled, BLOCK_M)
    print(f"{group.nunique():,} blocks of {BLOCK_M} m, dealt into {FOLDS} folds")

    report(
        cross_validate(labelled, assign_folds(group, FOLDS, rng), FOLDS),
        f"SPATIAL {FOLDS}-fold ({BLOCK_M} m blocks)",
    )

    if args.random:
        # The same models, scored the way the baseline scores them. The gap
        # between this and the block folds above is the leak, in storeys.
        rows = pd.Series(np.arange(len(labelled)).astype(str), index=labelled.index)
        report(
            cross_validate(labelled, assign_folds(rows, FOLDS, rng), FOLDS),
            f"RANDOM {FOLDS}-fold -- leaks, shown for comparison",
        )


if __name__ == "__main__":
    main()
