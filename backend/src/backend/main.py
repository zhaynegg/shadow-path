"""HTTP API for the shadow map."""

from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
from fastapi import FastAPI, Query

from backend.core.buildings import LAT, LON, load_buildings
from backend.core.shadow import shadow_field, shadow_frame
from backend.core.solar import sun_position

# This file sits at backend/src/backend/, so the repo root is four levels up.
CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
RADIUS = 2000
TZ = dt.timezone(dt.timedelta(hours=5))

# Fixed until the API takes a date, and deliberately the same day the fixtures
# were exported for, so responses can be compared against them.
DATE = dt.date(2026, 6, 21)

app = FastAPI()


@lru_cache(maxsize=1)
def buildings() -> gpd.GeoDataFrame:
    """Prepared footprints, read from disk once and shared by every request.

    Treat the result as read-only. It is the same object every time, so a
    mutation here would leak into every later response.
    """
    return load_buildings(CACHE_DIR, RADIUS)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/shadows")
def shadows(hour: int = Query(12, ge=0, le=23)) -> dict:
    """Merged shadow field for one local hour, as GeoJSON in lon/lat."""
    when = dt.datetime.combine(DATE, dt.time(hour), tzinfo=TZ)
    altitude, azimuth = sun_position(LAT, LON, when)

    gdf = buildings()

    # Below the horizon there is nothing to project, and shadow_field returns
    # None when no building casts anything. Both are answers, not errors.
    merged = shadow_field(gdf, altitude, azimuth) if altitude > 0 else None
    if merged is None:
        return {"type": "FeatureCollection", "features": []}

    frame = shadow_frame(merged, gdf.crs, when, altitude, azimuth)
    return json.loads(frame.to_json())
