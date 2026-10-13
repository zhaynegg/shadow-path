"""Export permanent tree crowns as vector tiles, independently of their shadows.

    uv run python scripts/export_tree_tiles.py [--cache-dir PATH] [--out-file PATH]

The same cached crowns feed the seasonal shadow model, but a tree remains on
the map after dark and outside the leaf-on window. Rebuild this asset when its
source caches change; it has no date or time input.

Needs tippecanoe on PATH (brew install tippecanoe).
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

from backend.config import CACHE_DIR
from backend.core.trees import load_canopy, load_trees

LAYER = "tree-canopy"
MIN_ZOOM, MAX_ZOOM = 11, 16
REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "frontend" / "public" / "tree-canopy.pmtiles"


def canopy_frame(
    osm_crowns: gpd.GeoDataFrame, canopy_crowns: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Combine crowns in lon/lat without changing their shapes or keeping heights.

    Empty optional canopy is allowed, including the loader's empty frame with
    no CRS. Nonempty inputs must be valid polygons with a known CRS: quietly
    dropping malformed crowns would turn an export error into missing trees.
    """
    parts = []
    for crowns in (osm_crowns, canopy_crowns):
        if crowns.empty:
            continue
        if crowns.crs is None:
            raise ValueError("Tree crowns must have a coordinate reference system")
        geometry = crowns.geometry
        if not geometry.geom_type.isin(("Polygon", "MultiPolygon")).all():
            raise ValueError("Tree crowns must be Polygon or MultiPolygon geometries")
        if geometry.is_empty.any() or not geometry.is_valid.all():
            raise ValueError("Tree crowns must be nonempty valid polygons")
        parts.append(geometry.to_crs(4326))

    if not parts:
        return gpd.GeoDataFrame(geometry=[], crs=4326)

    frame = gpd.GeoDataFrame(
        geometry=pd.concat(parts, ignore_index=True), crs=4326,
    )
    west, south, east, north = frame.total_bounds
    if (not all(math.isfinite(value) for value in frame.total_bounds)
            or not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90)
            or not frame.geometry.is_valid.all()):
        raise ValueError("Tree crowns must transform to valid longitude/latitude polygons")
    return frame


def load_frame(cache_dir: Path) -> gpd.GeoDataFrame:
    """Read OSM crowns and the best available canopy, without a seasonal filter."""
    return canopy_frame(load_trees(cache_dir), load_canopy(cache_dir))


def export_tiles(frame: gpd.GeoDataFrame, out_file: Path) -> Path:
    """Stage outside the public directory; replace the asset only on success."""
    if frame.empty:
        raise ValueError("No tree crowns found; refusing to replace the canopy asset")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    # A concurrent frontend build copies everything under public. Keep the
    # large intermediate GeoJSON and unfinished archive outside that directory,
    # beside it on the same filesystem for the final atomic replacement.
    with tempfile.TemporaryDirectory(
        dir=out_file.parent.parent, prefix=f".{out_file.stem}-",
    ) as work:
        work_dir = Path(work)
        source = work_dir / "tree-canopy.geojson"
        tiles = work_dir / "tree-canopy.pmtiles"
        frame.to_file(source, driver="GeoJSON", index=False)
        subprocess.run(
            [
                "tippecanoe",
                "--output", str(tiles),
                "--layer", LAYER,
                "--minimum-zoom", str(MIN_ZOOM),
                "--maximum-zoom", str(MAX_ZOOM),
                # At city zoom, merge crowded crowns instead of dropping them.
                "--coalesce-densest-as-needed",
                "--simplification", "2",
                "--force",
                "--quiet",
                str(source),
            ],
            check=True,
        )
        tiles.replace(out_file)
    return out_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--out-file", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if shutil.which("tippecanoe") is None:
        raise SystemExit("tippecanoe is not on PATH -- brew install tippecanoe")

    frame = load_frame(args.cache_dir)
    tiles = export_tiles(frame, args.out_file)
    print(f"{len(frame):,} tree crowns, {tiles.stat().st_size / 1e6:.1f} MB, in {tiles}")


if __name__ == "__main__":
    main()
