"""Measure OSM building-height coverage for a city.

Answers the question the shadow model depends on: for how many buildings do we
actually know the height, and how wrong is a fallback prior on the buildings
that cast the shadows we care about?

    uv run python scripts/height_coverage.py
    uv run python scripts/height_coverage.py --place "Delft, Netherlands"

Buildings are cached to `--cache-dir` so re-runs are free.
"""

from __future__ import annotations

import argparse
import re
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd

warnings.filterwarnings("ignore")

# Building types that plausibly cast a shadow worth routing around.
SHADOW_RELEVANT_TYPES = frozenset(
    {
        "apartments",
        "commercial",
        "office",
        "retail",
        "hotel",
        "school",
        "university",
        "hospital",
        "public",
        "civic",
        "dormitory",
        "kindergarten",
    }
)

# Footprints above this area are treated as shadow-relevant whatever their tag.
SHADOW_RELEVANT_AREA_M2 = 500.0

SIZE_BINS = [0, 150, 500, 1500, np.inf]
SIZE_LABELS = ["s", "m", "l", "xl"]

TALL_STOREYS = 5  # threshold for "this building's height actually matters"
LEVEL_HEIGHT_M = 3.2
OBLIQUITY_DEG = 23.44


def parse_numeric(value: object) -> float:
    """Pull a leading number out of a messy OSM tag ('12', '12 m', '3,5')."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    match = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)", str(value))
    return float(match.group(1).replace(",", ".")) if match else np.nan


def load_buildings(place: str, cache_dir: Path) -> gpd.GeoDataFrame:
    """Fetch polygonal building footprints for `place`, projected to local UTM."""
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir / "osmnx")
    ox.settings.requests_timeout = 900

    local = cache_dir / f"{place.split(',')[0].strip().lower()}_buildings.parquet"
    if local.exists():
        gdf = gpd.read_parquet(local)
    else:
        gdf = ox.features_from_place(place, tags={"building": True})
        gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
        gdf = gdf.to_crs(gdf.estimate_utm_crs())
        # osmnx indexes by (element, id); materialise it so the OSM identity
        # survives the round-trip through disk.
        gdf = gdf.reset_index()
        local.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_parquet(local)

    gdf = gdf.rename(columns={"element": "osm_element", "id": "osm_id"})
    gdf["area_m2"] = gdf.geometry.area
    gdf["levels"] = gdf.get("building:levels", pd.Series(index=gdf.index)).map(
        parse_numeric
    )
    gdf["height_m"] = gdf.get("height", pd.Series(index=gdf.index)).map(parse_numeric)
    gdf["size_class"] = pd.cut(gdf["area_m2"], SIZE_BINS, labels=SIZE_LABELS)

    # Shadow-mass: silhouette width x height, a proxy for shadow actually thrown.
    by_type = gdf.loc[gdf["levels"].notna()].groupby("building")["levels"].median()
    levels_filled = gdf["levels"].fillna(gdf["building"].map(by_type)).fillna(1)
    gdf["shadow_mass"] = np.sqrt(gdf["area_m2"]) * levels_filled
    return gdf


def report_coverage(gdf: gpd.GeoDataFrame) -> None:
    has_height, has_levels = gdf["height_m"].notna(), gdf["levels"].notna()
    area, mass = gdf["area_m2"].sum(), gdf["shadow_mass"].sum()

    print(f"  {len(gdf):,} footprints, {area / 1e6:,.1f} km2 of roof area\n")
    print(
        f"{'signal':<26}{'buildings':>11}{'% count':>9}{'% area':>9}{'% shadow-mass':>15}"
    )
    print("-" * 70)
    for label, mask in [
        ("height tag", has_height),
        ("building:levels", has_levels),
        ("either", has_height | has_levels),
        ("neither (needs prior)", ~(has_height | has_levels)),
    ]:
        print(
            f"{label:<26}{mask.sum():>11,}{100 * mask.mean():>8.1f}%"
            f"{100 * gdf.loc[mask, 'area_m2'].sum() / area:>8.1f}%"
            f"{100 * gdf.loc[mask, 'shadow_mass'].sum() / mass:>14.1f}%"
        )

    relevant = gdf["building"].isin(SHADOW_RELEVANT_TYPES) | (
        gdf["area_m2"] > SHADOW_RELEVANT_AREA_M2
    )
    print(
        f"\n  shadow-relevant subset: {relevant.sum():,} buildings "
        f"({100 * relevant.mean():.1f}% of all), "
        f"{100 * has_levels[relevant].mean():.1f}% levels-tagged"
    )


def report_prior(gdf: gpd.GeoDataFrame, seed: int = 0) -> float:
    """Held-out accuracy of a (building type x size class) median-levels prior.

    Returns MAE on tall buildings, which is the number that matters -- the
    overall MAE is flattered by the huge population of single-storey sheds.
    """
    labelled = gdf[gdf["levels"].notna()]
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(labelled))
    split = int(0.7 * len(labelled))
    train, test = labelled.iloc[order[:split]], labelled.iloc[order[split:]]

    medians = train.groupby(["building", "size_class"], observed=True)[
        "levels"
    ].median()
    fallback = train["levels"].median()
    pred = test.set_index(["building", "size_class"]).index.map(medians).to_numpy(float)
    pred = np.where(np.isnan(pred), fallback, pred)
    err = np.abs(test["levels"].to_numpy() - pred)

    print(f"  70/30 split, n_test={len(test):,}\n")
    print(f"{'prior':<26}{'MAE':>8}{'within 1':>11}{'p90':>7}")
    print("-" * 52)
    print(
        f"{'(type x size) median':<26}{err.mean():>8.2f}{100 * (err <= 1).mean():>10.1f}%{np.quantile(err, 0.9):>7.1f}"
    )
    for guess in (1, 3):
        e = (test["levels"] - guess).abs()
        print(
            f"{f'constant {guess} storeys':<26}{e.mean():>8.2f}{100 * (e <= 1).mean():>10.1f}%{e.quantile(0.9):>7.1f}"
        )

    tall = (test["levels"] >= TALL_STOREYS).to_numpy()
    tall_mae = err[tall].mean()
    print(
        f"\n  !! on tall buildings only (>={TALL_STOREYS} storeys, n={tall.sum():,}): "
        f"MAE {tall_mae:.2f} storeys"
    )
    return float(tall_mae)


def report_concentration(gdf: gpd.GeoDataFrame) -> None:
    """How few buildings you'd have to survey to close most of the gap."""
    untagged = gdf[gdf["levels"].isna()].sort_values("shadow_mass", ascending=False)
    share = untagged["shadow_mass"].cumsum() / untagged["shadow_mass"].sum()

    print(f"  {len(untagged):,} untagged buildings\n")
    for frac in (0.5, 0.6, 0.7, 0.8, 0.9):
        n = int((share < frac).sum()) + 1
        print(
            f"    {int(frac * 100)}% of untagged shadow-mass sits in the top "
            f"{n:>6,} buildings ({100 * n / len(untagged):>4.1f}%)"
        )
    top = untagged.head(1000)
    print(
        f"\n  top 1000: median area {top['area_m2'].median():,.0f} m2, "
        f"types {dict(top['building'].value_counts().head(4))}"
    )


def report_sun_angles(lat: float, mae_storeys: float) -> None:
    """Translate a height error into shadow-length error at this latitude."""
    error_m = mae_storeys * LEVEL_HEIGHT_M
    print(
        f"  latitude {lat:.1f}deg, height error {error_m:.0f} m ({mae_storeys:.1f} storeys)\n"
    )
    print(f"{'moment':<26}{'sun alt':>9}{'shadow':>10}{'error':>10}")
    print("-" * 55)
    for label, decl in [
        ("summer solstice, noon", OBLIQUITY_DEG),
        ("equinox, noon", 0.0),
        ("winter solstice, noon", -OBLIQUITY_DEG),
    ]:
        alt = 90.0 - abs(lat - decl)
        ratio = 1.0 / np.tan(np.radians(alt))
        print(f"{label:<26}{alt:>8.1f}°{ratio:>9.2f}x{error_m * ratio:>9.0f} m")
    for alt in (10.0, 7.0, 5.0):
        ratio = 1.0 / np.tan(np.radians(alt))
        print(
            f"{f'low sun ({alt:.0f}° altitude)':<26}{alt:>8.1f}°{ratio:>9.2f}x{error_m * ratio:>9.0f} m"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--place", default="Astana, Kazakhstan")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    gdf = load_buildings(args.place, args.cache_dir)
    lat = gdf.to_crs(4326).geometry.representative_point().y.mean()

    print(f"\n=== COVERAGE — {args.place} ===")
    report_coverage(gdf)

    print(f"\n=== FALLBACK PRIOR — {args.place} ===")
    tall_mae = report_prior(gdf, args.seed)

    print(f"\n=== WHERE THE GAP IS — {args.place} ===")
    report_concentration(gdf)

    print("\n=== COST OF THE ERROR ===")
    report_sun_angles(lat, tall_mae)


if __name__ == "__main__":
    main()
