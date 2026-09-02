"""Export shadow-polygon fixtures for the frontend.

    uv run python scripts/export_fixtures.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
from pysolar import solar
from shapely.affinity import translate
from shapely.geometry import Point

LEVEL_HEIGHT = 3.2
LAT, LON = 51.1605, 71.4704
TZ = dt.timezone(dt.timedelta(hours=5))

# Low sun makes h/tan(altitude) run away; past this the polygon is meaningless
# and the union gets expensive. See the latitude table in the README.
MAX_SHADOW_M = 300.0

# Geometry tolerance for the exported file. 1 m is well below one screen pixel
# at any zoom the shadow layer is drawn at, and cuts the file size sharply.
SIMPLIFY_M = 1.0

# Local hours to export. Morning, noon, afternoon, evening.
FIXTURE_HOURS = (8, 12, 16, 19)

def parse_numeric(value: object) -> float:
    """Pull a leading number out of a messy OSM tag ('12', '12 m', '3,5')."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    match = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)", str(value))
    return float(match.group(1).replace(",", ".")) if match else np.nan

def sun_position(when: dt.datetime) -> tuple[float, float]:
    """Solar altitude and azimuth in degrees at the city centre."""
    return solar.get_altitude(LAT, LON, when), solar.get_azimuth(LAT, LON, when)

def cast_shadow(geom, height_m: float, altitude: float, azimuth: float):
    """Project one footprint's shadow as a single polygon.

    The shadow is the convex hull of the footprint and a copy of it translated
    away from the sun -- the building, its shadow, and the band swept between.

    Returns None when the sun is at or below the horizon: the shadow is
    unbounded there, and everything is in shade anyway.
    """
    if altitude <= 0:
        return None

    length = min(height_m / np.tan(np.radians(altitude)), MAX_SHADOW_M)

    # Compass bearing of the shadow: directly away from the sun. Bearings run
    # clockwise from north, so north is the cosine axis and east the sine one.
    bearing = np.radians(azimuth + 180.0)
    moved = translate(
        geom,
        xoff=length * np.sin(bearing),
        yoff=length * np.cos(bearing),
    )
    return geom.union(moved).convex_hull


def shadow_field(gdf: gpd.GeoDataFrame, altitude: float, azimuth: float):
    """Every building's shadow, merged into one geometry.

    Overlaps are dissolved: two buildings shading the same street is one
    shaded area, not two polygons stacked on top of each other.
    """
    shadows = gdf.apply(
        lambda row: cast_shadow(row.geometry, row["height_m"], altitude, azimuth),
        axis=1,
    ).dropna()
    if shadows.empty:
        return None
    return gpd.GeoSeries(shadows, crs=gdf.crs).union_all()


def write_fixture(geom, crs, when: dt.datetime, altitude: float, azimuth: float,
                  out_dir: Path) -> Path:
    """Write one merged shadow field as GeoJSON for the frontend.

    Simplified in metres first (cheap, and 1 m is far below anything visible),
    then reprojected to lon/lat, which is the only CRS GeoJSON allows.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"shadows-{when:%H%M}.geojson"

    frame = gpd.GeoDataFrame(
        {
            "time": [when.isoformat()],
            "altitude_deg": [round(altitude, 2)],
            "azimuth_deg": [round(azimuth, 2)],
        },
        geometry=[geom.simplify(SIMPLIFY_M)],
        crs=crs,
    ).to_crs(4326)

    frame.to_file(path, driver="GeoJSON")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(__file__).resolve().parents[2] / "data" / "cache")
    parser.add_argument("--radius", type=float, default=2000)
    parser.add_argument("--date", type=dt.date.fromisoformat, default=dt.date(2026, 6, 21))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "frontend" / "public" / "fixtures",
    )
    args = parser.parse_args()
    gdf = gpd.read_parquet(args.cache_dir / "astana_buildings.parquet")

    centre = gpd.GeoSeries([Point(71.4704, 51.1605)], crs=4326).to_crs(gdf.crs).iloc[0]
    gdf = gdf[gdf.geometry.distance(centre) <= args.radius]

    levels = gdf["building:levels"].map(parse_numeric)
    gdf["height_m"] = (gdf["height"].map(parse_numeric).fillna(levels * LEVEL_HEIGHT).fillna(LEVEL_HEIGHT))

    print(f"{len(gdf):,} buildings within {args.radius:,.0f} m of centre")
    print(gdf["height_m"].describe())
    print("missing:", gdf["height_m"].isna().sum())

    # Direction check: at noon the sun is nearly due south, so shadows must run
    # north; by evening it is in the west, so they must run east.
    tallest = gdf.loc[gdf["height_m"].idxmax()]
    print(f"\ntallest building: {tallest['height_m']:.0f} m")
    for hour in FIXTURE_HOURS:
        when = dt.datetime.combine(args.date, dt.time(hour), tzinfo=TZ)
        altitude, azimuth = sun_position(when)

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

if __name__ == "__main__": # only do this when the file is run directly, not when another file imports it
    main()

