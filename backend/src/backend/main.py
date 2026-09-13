"""HTTP API for the shadow map."""
# uv run uvicorn backend.main:app --reload --port 8000
from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache

import geopandas as gpd
import osmnx as ox
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.config import CACHE_DIR, CRS, GRAPH_RADIUS, LAT, LON, TZ, today
from backend.core.buildings import load_buildings
from backend.core.graph import load_graph
from backend.core.routing import MAX_ALPHA, plan
from backend.core.scoring import score_edges, score_edges_layered
from backend.core.shadows import MAX_SHADOW_M, layered_field
from backend.core.solar import sun_position
from backend.core.trees import CANOPY_OPACITY, CANOPY_SOURCES, shading_geometry

# The map draws shadows from precomputed tiles built by
# scripts/export_shadow_tiles.py, so nothing here serves them. What is left is
# routing, which needs its own shadow field to weight the streets.

app = FastAPI()

# How far from today a caller may ask for. Every distinct date is a fresh
# scored_edges entry and a fresh shadow field over the routing footprints, so an
# unbounded range is an unbounded amount of work a caller can ask for. A year
# either side covers any tiles the map could reasonably be showing.
MAX_DATE_DRIFT = dt.timedelta(days=366)

# Everything close enough to a routed street to shade it. One constant so the
# buildings and the trees are clipped to the same disc.
SHADING_RADIUS = GRAPH_RADIUS + MAX_SHADOW_M


class RouteRequest(BaseModel):
    origin: tuple[float, float]
    destination: tuple[float, float]

    # The date the map is showing, taken from the tile manifest. Sent rather
    # than assumed here: if a scheduled rebuild fails, the tiles on screen are
    # yesterday's, and a server trusting its own clock would weight the streets
    # by a sun nobody can see.
    date: dt.date

    hour: int = Field(12, ge=0, le=23)

    # Signed: positive routes towards shade, negative towards sun, 0 is the
    # plain shortest path. Bounded on both sides rather than left open, because
    # the bounds are also what rejects nan and inf -- either would sail through
    # A* and come back as a route nobody asked for.
    alpha: float = Field(3.0, ge=-MAX_ALPHA, le=MAX_ALPHA)

    @field_validator("date")
    @classmethod
    def near_today(cls, value: dt.date) -> dt.date:
        if abs(value - today()) > MAX_DATE_DRIFT:
            raise ValueError(f"date must be within {MAX_DATE_DRIFT.days} days of today")
        return value

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
    return load_buildings(CACHE_DIR, SHADING_RADIUS)

@lru_cache(maxsize=1)
def graph():
    return load_graph(CACHE_DIR, GRAPH_RADIUS)

@lru_cache(maxsize=48)
def scored_edges(date: dt.date, hour: int):
    """The walking graph with every edge weighted by how shaded it is.

    Keyed on the date as well as the hour, because 13:00 in June and 13:00 in
    December are different suns -- an hour-only key would go on serving one for
    the other the first time a rebuild rolled the map forward. Two days of hours
    fit, so a rollover at midnight does not evict the day still being asked for.
    """
    when = dt.datetime.combine(date, dt.time(hour), tzinfo=TZ)
    altitude, azimuth = sun_position(LAT, LON, when)
    edges = ox.graph_to_gdfs(graph(), nodes=False)
    if altitude <= 0:
        return score_edges(edges, None)

    # Same frame the tiles were built from, so the route cannot be weighted by
    # shade the map does not draw. Split in two on the way in: a crown is not a
    # wall, and counting them alike called a tree-lined street as shaded as the
    # north side of a tower.
    casters = shading_geometry(routing_buildings(), date, CACHE_DIR, SHADING_RADIUS)
    opaque, dappled = layered_field(
        casters, altitude, azimuth, casters["height_source"].isin(CANOPY_SOURCES))
    return score_edges_layered(edges, opaque, dappled, CANOPY_OPACITY)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/route")
def route_endpoint(request: RouteRequest) -> dict:
    try:
        result = plan(graph(), scored_edges(request.date, request.hour),
            request.origin, request.destination, request.alpha)
    except ValueError as exc:
        # A bad pair of points is the caller's mistake, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for leg in result.values():
        leg["geometry"] = line_to_geojson(leg["geometry"], CRS)
    return result
