"""HTTP API for the shadow map."""
# uv run uvicorn backend.main:app --reload --port 8000
from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache

import geopandas as gpd
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.config import CACHE_DIR, CRS, GRAPH_RADIUS, LAT, LON, TZ, today
from backend.core import scores
from backend.core import streets as street_tables
from backend.core.day import Day, at_one_stamp, day_of, empty_shade
from backend.core.routing import (
    MAX_ALPHA,
    departures,
    lay_out,
    minutes_past_midnight,
    plan,
)
from backend.core.solar import LOW_SUN_MINUTES, daylight_times

# The map draws shadows from precomputed tiles built by
# scripts/export_shadow_tiles.py, so nothing here serves them. What is left is
# routing, which reads back the shade that same run scored the graph with.
# Nothing here computes a shadow field any more -- see no_scores for what a
# date the export has not covered gets instead.

app = FastAPI()

# How far from today a caller may ask for. A distinct date no longer costs a
# citywide union -- a miss is a refusal now -- so this bounds tidiness rather
# than load. A year either side covers any tiles the map could be showing.
MAX_DATE_DRIFT = dt.timedelta(days=366)


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
def graph_edges() -> pd.DataFrame:
    """The walk network as a table of edges: length, geometry, and the index.

    Read straight off disk rather than derived from a graph. This used to be
    `ox.graph_to_gdfs(load_graphml(...))`, which is the same 153k rows by way of
    a networkx MultiDiGraph costing 887 MB to build and 226 MB of osmnx to
    import -- against a 512 MB server. The graph is still what scripts/ work
    from; core/streets.py records what it reduces to.

    Held rather than re-read because it is a property of the network alone --
    no date, no sun. Read-only: everything that writes a shade column copies
    first, which is what makes sharing it safe.
    """
    return street_tables.load(CACHE_DIR, GRAPH_RADIUS)[0]

@lru_cache(maxsize=1)
def streets():
    """The walk network in the three shapes the router needs it in.

    Flattening 153k edges into adjacency arrays takes about a second and
    depends on nothing but the tables, so it happens once per process.
    """
    return lay_out(*street_tables.load(CACHE_DIR, GRAPH_RADIUS))

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

    # Allocated once and filled a stamp at a time. Collecting the rows and
    # stacking them afterwards holds three copies of the day at the moment of
    # stacking, and on a 512 MB box the peak is the number that matters -- a
    # container is killed for what it touched, not for what it kept.
    shade = empty_shade(times, len(edges))
    for row, at in enumerate(times):
        got = scores.load(CACHE_DIR, GRAPH_RADIUS, date, at, edges.index)
        # All of it or none. A day with a hole in it would route a walk through
        # the hole as though those streets were in full sun.
        if got is None:
            return None
        shade[row] = got.to_numpy(dtype="float32")

    return day_of(shade, times)

@lru_cache(maxsize=24)
def scored_edges(date: dt.date, hour: int, minute: int) -> np.ndarray | None:
    """One stamp's shade if the export wrote it, None if it did not.

    The narrow half of scored_day: a date missing some of its stamps can still
    answer a single route, which needs only the one it departs in.

    This used to compute the field itself on a miss -- the original
    implementation, from when the graph was a 1.7 km disc and a union of the
    city was cheap. At 15 km it is half a minute, and the key is
    (date, hour, minute), all three caller-supplied: roughly 52,000 reachable
    combinations against a cache of 96, so the caller chose when the server
    spent it. A miss is a refusal now, and that branch is gone.

    Returns the bare column. It used to hand back a copy of the edge frame with
    a shade column welded on, and its only caller then threw everything but the
    column away -- 2.5 MB of duplicated geometry pointers per cached stamp,
    against 0.6 MB for the numbers. At 96 entries that was a quarter of the
    server held in copies of one frame. 24 is as many stamps as a date has.
    """
    at = dt.time(hour, minute)

    # Stat the file before touching the graph, the way /api/day does. Without
    # this a refusal first pulls 153k edges off disk -- 2.7s of precisely what
    # the refusal exists to avoid, for a question the filesystem has already
    # answered. Once per process rather than per request, but free is better.
    if not scores.scores_path(CACHE_DIR, GRAPH_RADIUS, date, at).exists():
        return None

    edges = graph_edges()

    precomputed = scores.load(CACHE_DIR, GRAPH_RADIUS, date, at, edges.index)
    if precomputed is None:
        return None
    return precomputed.to_numpy(dtype="float32")


def sun_over(date: dt.date, at: dt.time) -> Day | None:
    """The day to plan against, or None if the export has not covered it.

    Three outcomes, narrowing. The whole day on disk prices the walk hour by
    hour as it is walked. Only the departure stamp prices it end to end at that
    stamp -- a true answer to a slightly smaller question. Neither is a
    refusal: the caller is told to run the export rather than made to wait
    while the server does it for them.
    """
    whole = scored_day(date)
    if whole is not None:
        return whole

    one = scored_edges(date, at.hour, at.minute)
    if one is None:
        return None
    return at_one_stamp(one, at)


def no_scores(date: dt.date) -> HTTPException:
    """Why this date cannot be answered, in a sentence the caller can act on.

    Returned rather than raised, so the traceback starts at the endpoint that
    declined. Both endpoints decline for the same two reasons, and only one of
    them used to say which.
    """
    times = daylight_times(LAT, LON, date, TZ)
    if not times:
        return HTTPException(status_code=503,
            detail="The sun does not rise over Astana on that date.")

    absent = scores.missing(CACHE_DIR, GRAPH_RADIUS, date, times)
    return HTTPException(status_code=503,
        detail=f"No precomputed scores for {len(absent)} of the {len(times)} stamps on "
               f"{date}. Run backend/scripts/export_shadow_tiles.py.")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/route")
def route_endpoint(request: RouteRequest) -> dict:
    at = dt.time(request.hour, request.minute)
    day = sun_over(request.date, at)
    if day is None:
        raise no_scores(request.date)

    try:
        result = plan(streets(), day,
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

    Needs every stamp where /api/route needs one, so it declines more often --
    but both decline rather than compute. A day built from scratch would be
    twelve minutes of one request holding the process.
    """
    day = scored_day(request.date)
    if day is None:
        raise no_scores(request.date)

    try:
        return departures(streets(), day, request.origin, request.destination, request.alpha)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
