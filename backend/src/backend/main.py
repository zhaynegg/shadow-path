"""HTTP API for the shadow map."""
# uv run uvicorn backend.main:app --reload --port 8000
from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache

import geopandas as gpd
import osmnx as ox
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from backend.config import CACHE_DIR, CRS, DATE, GRAPH_RADIUS, LAT, LON, RADIUS, TZ
from backend.core.buildings import load_buildings
from backend.core.graph import load_graph
from backend.core.routing import plan
from backend.core.scoring import score_edges
from backend.core.shadows import shadow_field, shadow_frame
from backend.core.solar import sun_position

app = FastAPI()


class RouteRequest(BaseModel):
    origin: tuple[float, float]
    destination: tuple[float, float]
    hour: int = Field(12, ge=0, le=23)
    alpha: float = Field(3.0, ge=0)

def line_to_geojson(line, crs) -> dict:
    frame = gpd.GeoDataFrame(geometry=[line], crs=crs).to_crs(4326)
    return json.loads(frame.to_json())["features"][0]["geometry"]

@lru_cache(maxsize=1)
def buildings() -> gpd.GeoDataFrame:
    """Prepared footprints, read from disk once and shared by every request.

    Treat the result as read-only. It is the same object every time, so a
    mutation here would leak into every later response.
    """
    return load_buildings(CACHE_DIR, RADIUS)

@lru_cache(maxsize=1)
def graph():
    return load_graph(CACHE_DIR, GRAPH_RADIUS)

@lru_cache(maxsize=24)
def scored_edges(hour: int):
    when = dt.datetime.combine(DATE, dt.time(hour), tzinfo=TZ)
    altitude, azimuth = sun_position(LAT, LON, when)
    shadow = shadow_field(buildings(), altitude, azimuth) if altitude > 0 else None
    return score_edges(ox.graph_to_gdfs(graph(), nodes=False), shadow)

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


@app.post("/api/route")
def route_endpoint(request: RouteRequest) -> dict:
    try:
        result = plan(graph(), scored_edges(request.hour),
            request.origin, request.destination, request.alpha)
    except ValueError as exc:
        # A bad pair of points is the caller's mistake, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
     
    for leg in result.values():
        leg["geometry"] = line_to_geojson(leg["geometry"], CRS)
    return result