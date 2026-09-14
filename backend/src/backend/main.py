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
from backend.core import scores
from backend.core.buildings import load_buildings
from backend.core.graph import load_graph
from backend.core.routing import MAX_ALPHA, departures, plan
from backend.core.scoring import score_edges, score_edges_layered
from backend.core.shadows import MAX_SHADOW_M, layered_field
from backend.core.solar import LOW_SUN_MINUTES, daylight_times, sun_position
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


class WalkRequest(BaseModel):
    """Two ends, a day, and how much detour the walker will pay for.

    Everything both endpoints need. /api/route adds the stamp it wants the walk
    priced at; /api/day asks about all of them and so names none.
    """

    origin: tuple[float, float]
    destination: tuple[float, float]

    # The date the map is showing, taken from the tile manifest. Sent rather
    # than assumed here: if a scheduled rebuild fails, the tiles on screen are
    # yesterday's, and a server trusting its own clock would weight the streets
    # by a sun nobody can see.
    date: dt.date

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


class RouteRequest(WalkRequest):
    hour: int = Field(12, ge=0, le=23)

    # Minutes past the hour, and only the ones a tileset can exist for. Low-sun
    # hours are cut into thirds because an hour is too coarse a step down there
    # -- see daylight_times. Constrained rather than free for the same reason
    # the date is bounded: every distinct stamp is its own scored graph, and
    # 0-59 would let one caller ask for sixty of them an hour.
    minute: int = Field(0)

    @field_validator("minute")
    @classmethod
    def on_a_step(cls, value: int) -> int:
        if value not in LOW_SUN_MINUTES:
            raise ValueError(f"minute must be one of {list(LOW_SUN_MINUTES)}")
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

@lru_cache(maxsize=1)
def graph_edges() -> gpd.GeoDataFrame:
    """The walking graph as a table of edges, geometry and all.

    Held rather than rebuilt because it is a property of the graph alone -- no
    date, no sun -- and building it is 0.6s for 153k edges. That was once a
    cost per cache miss, which was one stamp; a day scan misses twenty-four
    times on the first ask, and fifteen seconds of it would be this line.

    Read-only, like routing_buildings. Both branches of scored_edges copy
    before they write, which is what makes sharing it safe.
    """
    return ox.graph_to_gdfs(graph(), nodes=False)

@lru_cache(maxsize=96)
def scored_edges(date: dt.date, hour: int, minute: int):
    """The walking graph with every edge weighted by how shaded it is.

    Keyed on the date as well as the time, because 13:00 in June and 13:00 in
    December are different suns -- a time-only key would go on serving one for
    the other the first time a rebuild rolled the map forward.

    Sized for two days: a date runs to 29 stamps at midsummer, so 96 holds the
    longest two in the year and a rollover at midnight cannot evict the day
    still being asked for. That is also what makes /api/day cheap to ask twice
    -- the second scan of a day is already in here, whole.
    """
    edges = graph_edges()

    # The nightly tile run already built this exact field and scored the graph
    # against it. Reading that back is the difference between half a minute and
    # a parquet read, and it is what makes a city-wide graph usable at all.
    # Everything below is the fallback for a checkout that has not run it.
    precomputed = scores.load(CACHE_DIR, GRAPH_RADIUS, date, dt.time(hour, minute), edges.index)
    if precomputed is not None:
        ready = edges.copy()
        ready["shade_fraction"] = precomputed
        return ready

    when = dt.datetime.combine(date, dt.time(hour, minute), tzinfo=TZ)
    altitude, azimuth = sun_position(LAT, LON, when)
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
        result = plan(graph(), scored_edges(request.date, request.hour, request.minute),
            request.origin, request.destination, request.alpha)
    except ValueError as exc:
        # A bad pair of points is the caller's mistake, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for leg in result.values():
        leg["geometry"] = line_to_geojson(leg["geometry"], CRS)
    return result


@app.post("/api/day")
def day_endpoint(request: WalkRequest) -> dict:
    """The same walk at every stamp of the day: when to leave, and what it buys.

    The stamps come from daylight_times rather than from a window written down
    here, because that is the function export_shadow_tiles.py cut the tiles and
    the scores with -- so the scan covers exactly the times that have an answer
    on disk, and night falls out of it without being special-cased.
    """
    times = daylight_times(LAT, LON, request.date, TZ)
    if not times:
        raise HTTPException(status_code=503,
            detail="The sun does not rise over Astana on that date.")

    # Checked up front, not discovered per stamp. Falling back to computing the
    # field is the right answer for one missing stamp and the wrong one for
    # twenty-four of them -- see scores.missing.
    absent = scores.missing(CACHE_DIR, GRAPH_RADIUS, request.date, times)
    if absent:
        raise HTTPException(status_code=503,
            detail=f"No precomputed scores for {len(absent)} of the {len(times)} stamps on "
                   f"{request.date}. Run backend/scripts/export_shadow_tiles.py.")

    scored = {at: scored_edges(request.date, at.hour, at.minute) for at in times}
    try:
        return departures(graph(), scored, request.origin, request.destination, request.alpha)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
