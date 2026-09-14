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
from backend.core.day import Day, across_the_day, at_one_stamp
from backend.core.graph import load_graph
from backend.core.routing import (
    MAX_ALPHA,
    departures,
    graph_nodes,
    lay_out,
    minutes_past_midnight,
    plan,
)
from backend.core.scoring import score_edges, score_edges_layered
from backend.core.shadows import MAX_SHADOW_M, layered_field
from backend.core.solar import LOW_SUN_MINUTES, daylight_times, sun_position
from backend.core.trees import CANOPY_OPACITY, CANOPY_SOURCES, shading_geometry

# The map draws shadows from precomputed tiles built by
# scripts/export_shadow_tiles.py, so nothing here serves them. What is left is
# routing, which needs its own shadow field to weight the streets.

app = FastAPI()

# How far from today a caller may ask for. Every distinct date is a fresh day of
# scores and a fresh shadow field over the routing footprints, so an unbounded
# range is an unbounded amount of work a caller can ask for. A year either side
# covers any tiles the map could reasonably be showing.
MAX_DATE_DRIFT = dt.timedelta(days=366)

# Everything close enough to a routed street to shade it. One constant so the
# buildings and the trees are clipped to the same disc.
SHADING_RADIUS = GRAPH_RADIUS + MAX_SHADOW_M


class WalkRequest(BaseModel):
    """Two ends, a day, and how much detour the walker will pay for.

    Everything both endpoints need. /api/route adds the stamp the walker sets
    off in; /api/day asks about all of them and so names none.
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
    # the search and come back as a route nobody asked for.
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
    # the date is bounded, and because this is where the walk starts on a clock
    # the router now follows all the way to the far end.
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
    date, no sun -- and building it is 0.6s for 153k edges.

    Read-only, like routing_buildings. Everything that writes a shade column
    copies first, which is what makes sharing it safe.
    """
    return ox.graph_to_gdfs(graph(), nodes=False)

@lru_cache(maxsize=1)
def streets():
    """The walk network in the three shapes the router needs it in.

    Flattening 153k edges into adjacency arrays takes about 0.2s and depends on
    nothing but the graph, so it happens once for the life of the process.
    """
    return lay_out(graph_edges(), graph_nodes(graph()))

@lru_cache(maxsize=2)
def scored_day(date: dt.date) -> Day | None:
    """A whole date's shade, or None if the nightly export has not written it.

    The unit the router works in now. A walk is priced at the sun it is under as
    it crosses each street, so planning one means holding every stamp of the day
    at once rather than the single stamp it sets off in -- 15 MB of float32 for
    the 15 km graph, and about a third of a second of parquet to fill.

    Two dates, so a rollover at midnight cannot evict the day still being asked
    about. None is an ordinary outcome and not an error: a checkout that has
    never run the export still routes, one stamp at a time, the way this did
    before it could do better.
    """
    times = daylight_times(LAT, LON, date, TZ)
    if not times:
        return None

    # Stat the files before touching the graph. Declining has to stay cheap:
    # on a cold checkout this is the difference between answering "no" in a
    # millisecond and loading 153k edges off disk first to say the same thing.
    if scores.missing(CACHE_DIR, GRAPH_RADIUS, date, times):
        return None

    edges = graph_edges()
    rows = []
    for at in times:
        got = scores.load(CACHE_DIR, GRAPH_RADIUS, date, at, edges.index)
        # All of it or none. A day with a hole in it would route a walk through
        # the hole as though those streets were in full sun.
        if got is None:
            return None
        rows.append(got.to_numpy(dtype="float32"))

    return across_the_day(rows, times)

@lru_cache(maxsize=96)
def scored_edges(date: dt.date, hour: int, minute: int):
    """One stamp's shade, computed from scratch -- the cold-checkout path.

    Only reached when scored_day found nothing on disk. Keyed on the date as
    well as the time, because 13:00 in June and 13:00 in December are different
    suns and a time-only key would go on serving one for the other the first
    time a rebuild rolled the map forward.
    """
    edges = graph_edges()

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


def sun_over(date: dt.date, at: dt.time) -> Day:
    """The day to plan against, degrading rather than failing without a cache.

    With the day on disk the walk is priced hour by hour as it is walked. With
    nothing on disk it is priced end to end at the stamp it starts in, which is
    a true answer to a slightly smaller question and is what this API answered
    for its whole life before now.
    """
    whole = scored_day(date)
    if whole is not None:
        return whole

    one = scored_edges(date, at.hour, at.minute)
    return at_one_stamp(one["shade_fraction"].to_numpy(), at)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/route")
def route_endpoint(request: RouteRequest) -> dict:
    at = dt.time(request.hour, request.minute)
    try:
        result = plan(streets(), sun_over(request.date, at),
            request.origin, request.destination, request.alpha, minutes_past_midnight(at))
    except ValueError as exc:
        # A bad pair of points is the caller's mistake, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for leg in result.values():
        leg["geometry"] = line_to_geojson(leg["geometry"], CRS)
    return result


@app.post("/api/day")
def day_endpoint(request: WalkRequest) -> dict:
    """The same walk at every stamp of the day: when to leave, and what it buys.

    Unlike /api/route this has no fallback. One stamp computed from scratch is
    half a minute; a day of them is twelve minutes of one request holding the
    process, which is not a slow answer but an outage a caller can cause.
    """
    day = scored_day(request.date)
    if day is None:
        times = daylight_times(LAT, LON, request.date, TZ)
        if not times:
            raise HTTPException(status_code=503,
                detail="The sun does not rise over Astana on that date.")

        absent = scores.missing(CACHE_DIR, GRAPH_RADIUS, request.date, times)
        raise HTTPException(status_code=503,
            detail=f"No precomputed scores for {len(absent)} of the {len(times)} stamps on "
                   f"{request.date}. Run backend/scripts/export_shadow_tiles.py.")

    try:
        return departures(streets(), day, request.origin, request.destination, request.alpha)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
