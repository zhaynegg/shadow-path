"""Export shadow-polygon fixtures for the frontend.

uv run python scripts/export_fixtures.py
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from backend.config import LAT, LON, RADIUS, TZ
from backend.core.buildings import load_buildings
from backend.core.shadows import cast_shadow, shadow_field, shadow_frame
from backend.core.solar import sun_position

# Local hours to export. Morning, noon, afternoon, evening.
FIXTURE_HOURS = (8, 12, 16, 19)


def write_fixture(
    geom, crs, when: dt.datetime, altitude: float, azimuth: float, out_dir: Path
) -> Path:
    """Write one merged shadow field as GeoJSON for the frontend.

    Simplified in metres first (cheap, and 1 m is far below anything visible),
    then reprojected to lon/lat, which is the only CRS GeoJSON allows.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"shadows-{when:%H%M}.geojson"

    frame = shadow_frame(geom, crs, when, altitude, azimuth)
    frame.to_file(path, driver="GeoJSON")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data" / "cache",
    )
    parser.add_argument("--radius", type=float, default=RADIUS)
    parser.add_argument(
        "--date", type=dt.date.fromisoformat, default=dt.date(2026, 6, 21)
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "frontend"
        / "public"
        / "fixtures",
    )
    args = parser.parse_args()

    gdf = load_buildings(args.cache_dir, args.radius)

    print(f"{len(gdf):,} buildings within {args.radius:,.0f} m of centre")
    print(gdf["height_m"].describe())
    print("missing:", gdf["height_m"].isna().sum())

    # Direction check: at noon the sun is nearly due south, so shadows must run
    # north; by evening it is in the west, so they must run east.
    tallest = gdf.loc[gdf["height_m"].idxmax()]
    print(f"\ntallest building: {tallest['height_m']:.0f} m")
    for hour in FIXTURE_HOURS:
        when = dt.datetime.combine(args.date, dt.time(hour), tzinfo=TZ)
        altitude, azimuth = sun_position(LAT, LON, when)

        if altitude <= 0:
            print(f"  {when:%H:%M}  sun below horizon, skipped")
            continue

        shadow = cast_shadow(tallest.geometry, tallest["height_m"], altitude, azimuth)
        east = shadow.centroid.x - tallest.geometry.centroid.x
        north = shadow.centroid.y - tallest.geometry.centroid.y

        merged = shadow_field(gdf, altitude, azimuth)
        path = write_fixture(merged, gdf.crs, when, altitude, azimuth, args.out_dir)
        print(
            f"  {when:%H:%M}  alt={altitude:5.1f}  az={azimuth:6.1f}"
            f"   offset: east {east:+7.1f} m, north {north:+7.1f} m"
            f"   field: {merged.area / 1e6:5.2f} km2"
            f"   -> {path.name} ({path.stat().st_size / 1024:.0f} KB)"
        )


if __name__ == "__main__":  # only when run directly, not when imported
    main()
