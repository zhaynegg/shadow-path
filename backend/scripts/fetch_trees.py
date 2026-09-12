"""Cache Astana's mapped trees, the way the buildings are cached.

    uv run python scripts/fetch_trees.py

OSM holds street planting as `natural=tree_row` -- a line down the pavement --
and the odd `natural=tree` point. Both are stored here as they come, in metres,
and buffered into canopy at load time: the crown width is a guess (see
core/trees.py) and a guess should be cheap to change without refetching.

Coverage is thin, and worth knowing before building anything on it. Citywide
there are ~740 tree rows and a handful of individual trees, and none of them
carry a `height` tag. Inside the routing disc that is about 1.2 km of planting
against 570 km of walk network.
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import geopandas as gpd
import osmnx as ox

from backend.config import CACHE_DIR, CRS

warnings.filterwarnings("ignore")

# Lines and points both count. Polygonal woodland is deliberately left out: a
# park boundary is not a canopy, and casting a solid shadow from one would put
# a tree's shade over open lawn.
TAGS = {"natural": ["tree", "tree_row"]}

TREES = "astana_trees.parquet"


def fetch(place: str, cache_dir: Path) -> gpd.GeoDataFrame:
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir / "osmnx")

    gdf = ox.features_from_place(place, tags=TAGS)
    gdf = gdf[gdf.geometry.geom_type.isin(["LineString", "Point"])].copy()

    # Same move as the buildings cache: materialise osmnx's (element, id) index
    # so the OSM identity survives the round-trip through disk.
    gdf = gdf.reset_index()
    keep = [c for c in ("element", "id", "natural", "leaf_type", "leaf_cycle", "species")
            if c in gdf.columns]
    return gdf[[*keep, "geometry"]].to_crs(CRS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--place", default="Astana, Kazakhstan")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    args = parser.parse_args()

    gdf = fetch(args.place, args.cache_dir)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    out = args.cache_dir / TREES
    gdf.to_parquet(out)

    counts = gdf["natural"].value_counts().to_dict()
    tagged = int(gdf["leaf_cycle"].notna().sum()) if "leaf_cycle" in gdf else 0
    print(f"{len(gdf):,} features -> {out}")
    print(f"  by type: {counts}")
    print(f"  with leaf_cycle: {tagged}  (the rest fall back to the default season)")
    print(f"  with a height tag: {int(gdf['height'].notna().sum()) if 'height' in gdf else 0}")


if __name__ == "__main__":
    main()
