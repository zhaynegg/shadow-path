"""Precompute the whole city's shadows as vector tiles, one set per daylight hour.

    uv run python scripts/export_shadow_tiles.py

The date is fixed and the sun repeats, so none of this changes at runtime.
Building it once turns the shadow layer from a query into part of the map: the
browser reads shadow tiles the way it reads roads -- whole city, every zoom,
nothing to wait for and no server in the loop.

Needs tippecanoe on PATH (brew install tippecanoe).
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import geopandas as gpd
from backend.config import CACHE_DIR, DATE, LAT, LON, TZ
from backend.core.buildings import load_buildings
from backend.core.shadows import SIMPLIFY_M, shadow_field
from backend.core.solar import sun_position

# Every hour uses the same layer name, so one map style can read whichever
# tileset is currently loaded without rewriting the layer.
LAYER = "shadows"

# z16 is about a metre per pixel and maplibre overzooms past it for free. The
# floor is where the whole city is still a few hundred pixels across.
MIN_ZOOM, MAX_ZOOM = 8, 16

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "frontend" / "public" / "shadows"


def blobs(merged, crs) -> gpd.GeoDataFrame:
    """The merged field as one feature per shadow blob, in lon/lat.

    Tippecanoe slices thousands of small features across tiles far better than
    one city-sized multipolygon -- and a blob is the honest unit anyway.
    """
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    frame = gpd.GeoDataFrame(geometry=[part.simplify(SIMPLIFY_M) for part in parts], crs=crs)
    return frame.to_crs(4326)


def build_hour(gdf: gpd.GeoDataFrame, hour: int, out_dir: Path, work_dir: Path) -> Path | None:
    """Write one hour's tileset. None when the sun is down and there is nothing to draw."""
    when = dt.datetime.combine(DATE, dt.time(hour), tzinfo=TZ)
    altitude, azimuth = sun_position(LAT, LON, when)
    if altitude <= 0:
        return None

    merged = shadow_field(gdf, altitude, azimuth)
    if merged is None:
        return None

    frame = blobs(merged, gdf.crs)
    source = work_dir / f"{hour:02d}.geojson"
    frame.to_file(source, driver="GeoJSON")

    tiles = out_dir / f"{hour:02d}.pmtiles"
    subprocess.run(
        [
            "tippecanoe",
            "--output", str(tiles),
            "--layer", LAYER,
            "--minimum-zoom", str(MIN_ZOOM),
            "--maximum-zoom", str(MAX_ZOOM),
            # Zoomed out, shadows should thin into a smear rather than grow
            # holes where tippecanoe gave up on a crowded tile.
            "--coalesce-densest-as-needed",
            "--simplification", "4",
            "--force",
            "--quiet",
            str(source),
        ],
        check=True,
    )
    return tiles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    args = parser.parse_args()

    if shutil.which("tippecanoe") is None:
        raise SystemExit("tippecanoe is not on PATH -- brew install tippecanoe")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    gdf = load_buildings(args.cache_dir)
    print(f"{len(gdf):,} buildings, {DATE}\n")

    written = 0
    with tempfile.TemporaryDirectory() as tmp:
        for hour in range(24):
            start = time.time()
            tiles = build_hour(gdf, hour, args.out_dir, Path(tmp))
            if tiles is None:
                print(f"  {hour:02d}:00  sun down, skipped")
                continue
            size = tiles.stat().st_size / 1e6
            written += 1
            print(f"  {hour:02d}:00  {tiles.name}  {size:5.1f} MB  {time.time() - start:5.1f}s")

    total = sum(p.stat().st_size for p in args.out_dir.glob("*.pmtiles")) / 1e6
    print(f"\n{written} tilesets, {total:.1f} MB total, in {args.out_dir}")


if __name__ == "__main__":
    main()
