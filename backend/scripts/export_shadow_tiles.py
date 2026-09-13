"""Precompute the whole city's shadows as vector tiles, one set per daylight hour.

    uv run python scripts/export_shadow_tiles.py [--date YYYY-MM-DD]

Within a day the sun repeats, so an hour's shadows never change once built.
Precomputing them turns the shadow layer from a query into part of the map: the
browser reads shadow tiles the way it reads roads -- whole city, every zoom,
nothing to wait for and no server in the loop.

Across days it does change, and a lot: an Astana noon shadow is 0.55x the
building's height in June and 3.8x in December. So the date is an argument, and
rebuilding for today is a job for whatever runs this on a schedule.

Needs tippecanoe on PATH (brew install tippecanoe).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd

from backend.config import CACHE_DIR, LAT, LON, TZ, today
from backend.core.buildings import load_buildings
from backend.core.shadows import SIMPLIFY_M, layered_field
from backend.core.solar import daylight_times, sun_position
from backend.core.trees import CANOPY_SOURCES, shading_geometry

# Every hour uses the same layer name, so one map style can read whichever
# tileset is currently loaded without rewriting the layer.
LAYER = "shadows"

# What this run produced, written beside the tiles for the map to read.
MANIFEST = "index.json"

# z16 is about a metre per pixel and maplibre overzooms past it for free. The
# floor is where the whole city is still a few hundred pixels across.
MIN_ZOOM, MAX_ZOOM = 8, 16

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "frontend" / "public" / "shadows"


def blobs(merged, crs, kind: str) -> gpd.GeoDataFrame:
    """The merged field as one feature per shadow blob, in lon/lat.

    Tippecanoe slices thousands of small features across tiles far better than
    one city-sized multipolygon -- and a blob is the honest unit anyway.

    `kind` rides along as a feature property so the map can draw a crown
    lighter than a wall. Without it every shadow is equally black, which is
    both wrong and the reason you cannot tell there are trees on the map.
    """
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    frame = gpd.GeoDataFrame(
        geometry=[part.simplify(SIMPLIFY_M) for part in parts], crs=crs
    )
    frame["kind"] = kind
    return frame.to_crs(4326)


def stem(when: dt.time) -> str:
    """HHMM. Sorts as a string, needs no separator, and reads as a clock."""
    return f"{when.hour:02d}{when.minute:02d}"


def build_time(
    gdf: gpd.GeoDataFrame, date: dt.date, at: dt.time, out_dir: Path, work_dir: Path
) -> Path | None:
    """Write one timestamp's tileset. None when there is nothing to draw."""
    when = dt.datetime.combine(date, at, tzinfo=TZ)
    altitude, azimuth = sun_position(LAT, LON, when)
    if altitude <= 0:
        return None

    # Split the same way the router does, so the picture and the route agree
    # about what canopy is worth.
    opaque, dappled = layered_field(
        gdf, altitude, azimuth, gdf["height_source"].isin(CANOPY_SOURCES))
    if opaque is None and dappled is None:
        return None

    parts = [blobs(field, gdf.crs, kind)
             for field, kind in ((opaque, "solid"), (dappled, "canopy"))
             if field is not None]
    frame = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=4326)
    source = work_dir / f"{stem(at)}.geojson"
    frame.to_file(source, driver="GeoJSON")

    tiles = out_dir / f"{stem(at)}.pmtiles"
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


def write_manifest(out_dir: Path, date: dt.date, times: list[dt.time]) -> Path:
    """Record what this run produced, so the map can read it instead of guessing.

    Which stamps exist is a property of the date, twice over. Daylight is 16
    hours in June and 8 in December, and how finely each hour is cut depends on
    how high the sun gets -- see daylight_times. Neither is something the
    frontend can work out for itself, and a window hardcoded on that side is a
    second rule that agrees with this one only until the season moves.

    Written last, once every tileset is on disk, so a run that dies halfway
    through never leaves a manifest promising stamps it did not build.
    """
    path = out_dir / MANIFEST
    path.write_text(json.dumps({
        "date": date.isoformat(),
        # "HH:MM", which is both the label the slider shows and, with the colon
        # dropped, the tileset filename.
        "times": [t.strftime("%H:%M") for t in times],
        # Set by --layer below. The map needs the same string to style it.
        "layer": LAYER,
        # Not used for drawing -- it is how you tell a stale deploy from a
        # scheduled run that quietly stopped firing.
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }, indent=2) + "\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--date", type=dt.date.fromisoformat, default=today(),
                        help="YYYY-MM-DD; defaults to today in Astana")
    args = parser.parse_args()

    if shutil.which("tippecanoe") is None:
        raise SystemExit("tippecanoe is not on PATH -- brew install tippecanoe")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    buildings = load_buildings(args.cache_dir)
    # Trees join the frame here rather than per hour: whether there is a canopy
    # is a property of the date, and the date does not change inside a run.
    gdf = shading_geometry(buildings, args.date, args.cache_dir)
    canopy = len(gdf) - len(buildings)
    leaf = f" + {canopy:,} trees in leaf" if canopy else " (trees bare, out of season)"
    print(f"{len(buildings):,} buildings{leaf}, {args.date}\n")

    # Resolution is decided once, up front, by the sun rather than the clock:
    # hourly where the sun is high and every 20 minutes where it is low and a
    # shadow crosses a street inside the hour.
    wanted = daylight_times(LAT, LON, args.date, TZ)
    print(f"{len(wanted)} stamps, {wanted[0]:%H:%M}-{wanted[-1]:%H:%M}\n")

    written: dict[dt.time, Path] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for at in wanted:
            start = time.time()
            tiles = build_time(gdf, args.date, at, args.out_dir, Path(tmp))
            if tiles is None:
                print(f"  {at:%H:%M}  sun down, skipped")
                continue
            size = tiles.stat().st_size / 1e6
            written[at] = tiles
            print(f"  {at:%H:%M}  {tiles.name}  {size:5.1f} MB  {time.time() - start:5.1f}s")

    # A rebuild can leave behind stamps the new date has no sun for, or, after
    # a change to daylight_times, ones it no longer cuts the hour finely enough
    # to want. They would ship as dead weight and, worse, still answer when the
    # map asked.
    for stale in sorted(set(args.out_dir.glob("*.pmtiles")) - set(written.values())):
        stale.unlink()
        print(f"  {stale.name}  stale, removed")

    times = sorted(written)
    write_manifest(args.out_dir, args.date, times)

    total = sum(p.stat().st_size for p in args.out_dir.glob("*.pmtiles")) / 1e6
    span = f"{times[0]:%H:%M}-{times[-1]:%H:%M}" if times else "none"
    print(f"\n{len(times)} tilesets, {span}, {total:.1f} MB total, in {args.out_dir}")
    print(f"{MANIFEST} written for {args.date}")


if __name__ == "__main__":
    main()
