"""HTTP API for the shadow map."""
# uv run uvicorn backend.main:app --reload --port 8000
from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache

import geopandas as gpd
import osmnx as ox
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.config import CACHE_DIR, CRS, DATE, GRAPH_RADIUS, LAT, LON, TZ
from backend.core.buildings import load_buildings
from backend.core.graph import load_graph
from backend.core.routing import MAX_ALPHA, plan
from backend.core.scoring import score_edges
from backend.core.shadows import MAX_SHADOW_M, shadow_field
from backend.core.solar import sun_position

# The map draws shadows from precomputed tiles built by
# scripts/export_shadow_tiles.py, so nothing here serves them. What is left is
# routing, which needs its own shadow field to weight the streets.

app = FastAPI()


class RouteRequest(BaseModel):
    origin: tuple[float, float]
    destination: tuple[float, float]
    hour: int = Field(12, ge=0, le=23)

    # Signed: positive routes towards shade, negative towards sun, 0 is the
    # plain shortest path. Bounded on both sides rather than left open, because
    # the bounds are also what rejects nan and inf -- either would sail through
    # A* and come back as a route nobody asked for.
    alpha: float = Field(3.0, ge=-MAX_ALPHA, le=MAX_ALPHA)

def line_to_geojson(line, crs) -> dict:
    frame = gpd.GeoDataFrame(geometry=[line], crs=crs).to_crs(4326)
    return json.loads(frame.to_json())["features"][0]["geometry"]

@lru_cache(maxsize=1)
def routing_buildings() -> gpd.GeoDataFrame:
    """Only the footprints that can shade a street we route on.

    The walking graph is a disc of GRAPH_RADIUS and MAX_SHADOW_M is the longest
    shadow the model casts, so nothing further out can reach it. Scoring
    intersects every edge against this field, and a city-wide one would be
    thousands of times more geometry for no change in the answer.

    Treat the result as read-only. It is the same object every time, so a
    mutation here would leak into every later response.
    """
    return load_buildings(CACHE_DIR, GRAPH_RADIUS + MAX_SHADOW_M)

@lru_cache(maxsize=1)
def graph():
    return load_graph(CACHE_DIR, GRAPH_RADIUS)

@lru_cache(maxsize=24)
def scored_edges(hour: int):
    when = dt.datetime.combine(DATE, dt.time(hour), tzinfo=TZ)
    altitude, azimuth = sun_position(LAT, LON, when)
    shadow = shadow_field(routing_buildings(), altitude, azimuth) if altitude > 0 else None
    return score_edges(ox.graph_to_gdfs(graph(), nodes=False), shadow)


@app.get("/api/health")
def health():
    return {"status": "ok"}


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
