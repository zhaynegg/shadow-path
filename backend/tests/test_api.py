"""The HTTP layer: what a caller is allowed to ask for, and what gets cached.

Deliberately nothing here routes. `plan` already has its own tests, and going
through the endpoint would pull the graph and the footprints off disk -- minutes
on a warm cache, a failure on a cold one. What is untested without this file is
everything around that call: the bounds on the request, and the key the scored
graph is memoised under.
"""
import datetime as dt
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from shapely.geometry import LineString

# `import ... as`, not `from backend import main`: backend/__init__.py defines a
# main() of its own for the console script, and that name shadows this module.
import backend.main as main  # noqa: PLR0402 -- the rewrite ruff suggests is the bug
from backend.config import CRS, LAT, LON, TZ, today
from backend.core.day import at_one_stamp
from backend.core.solar import daylight_times
from backend.main import RouteRequest, WalkRequest, app


def valid_request(**overrides) -> dict:
    """A request that passes, so each test can spoil exactly one field.

    The date is computed, never written down. A hardcoded one drifts out of
    MAX_DATE_DRIFT on its own and fails a year from now for no reason anybody
    will remember.
    """
    return {
        "origin": (51.1605, 71.4704),
        "destination": (51.1700, 71.4300),
        "date": today(),
        "hour": 13,
        "minute": 0,
        "alpha": 3.0,
    } | overrides


def clear_caches():
    """Both lru_caches these tests fill, and why there are two of them.

    graph_edges holds the edge frame on its own now, because it depends on the
    graph alone and every stamp was rebuilding it. That also means a fake frame
    installed by monkeypatch outlives the test that installed it -- so the next
    test reads the previous test's graph, and passes or fails for reasons that
    have nothing to do with it.
    """
    for cached in (main.scored_edges, main.scored_day, main.graph_edges, main.streets):
        # Some tests replace one of these with a plain stand-in, which has no
        # cache to clear. Asking anyway is how this helper turns a monkeypatch
        # into an AttributeError three tests later.
        clear = getattr(cached, "cache_clear", None)
        if clear is not None:
            clear()


def test_valid_request_parses_and_alpha_defaults_to_shade_seeking():
    """The control. A file of rejections proves nothing if everything is rejected."""
    fields = valid_request()
    del fields["alpha"]

    request = RouteRequest(**fields)

    assert request.alpha == 3.0
    assert request.date == today()
    # Parsed into a real date, not left as whatever came off the wire -- the
    # scored_edges cache key is only stable because this is a date object.
    assert isinstance(request.date, dt.date)


@pytest.mark.parametrize("drift", [400, -400])
def test_date_far_from_today_is_rejected(drift):
    """Both directions. The bound exists because every distinct date is a fresh
    shadow field over the routing footprints -- an unbounded range is an
    unbounded amount of work one caller can ask a shared process to do.
    """
    far = today() + dt.timedelta(days=drift)

    with pytest.raises(ValidationError):
        RouteRequest(**valid_request(date=far))


def test_date_just_inside_the_bound_is_accepted():
    """The other side of the same line. Without this, a validator that rejected
    every date would pass the test above and look correct.
    """
    edge = today() + dt.timedelta(days=main.MAX_DATE_DRIFT.days)

    assert RouteRequest(**valid_request(date=edge)).date == edge


@pytest.mark.parametrize("alpha", [float("inf"), float("-inf"), float("nan")])
def test_alpha_must_be_a_real_number(alpha):
    """Not a typo anyone makes -- it is what the ge/le bounds are really for.

    A float field with no bounds takes inf and nan happily. Either one sails
    through A* without raising: inf makes every weight equal, nan makes every
    comparison false, and both come back as a route nobody asked for and no
    error to say so. Bounding alpha on both sides is what rejects them, which
    is why the bounds are not just a range check.
    """
    with pytest.raises(ValidationError):
        RouteRequest(**valid_request(alpha=alpha))


@pytest.mark.parametrize("alpha", [main.MAX_ALPHA + 0.1, -main.MAX_ALPHA - 0.1])
def test_alpha_past_the_limit_is_rejected(alpha):
    with pytest.raises(ValidationError):
        RouteRequest(**valid_request(alpha=alpha))


def test_negative_alpha_is_accepted():
    """Sun-seeking is not a novelty mode -- at 51degN it is the winter product.
    A bound written as ge=0 would pass every other test in this file.
    """
    assert RouteRequest(**valid_request(alpha=-6.0)).alpha == -6.0


@pytest.mark.parametrize("hour", [-1, 24])
def test_hour_outside_the_day_is_rejected(hour):
    with pytest.raises(ValidationError):
        RouteRequest(**valid_request(hour=hour))


def test_health_reports_ok():
    """Cheap, and not trivial: building the client imports main.py top to
    bottom, so a broken import or a bad decorator fails here rather than at
    whatever deploy first notices.
    """
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_malformed_body_is_a_client_error():
    """422, not 500. FastAPI turns a ValidationError into the former by itself,
    and this is the test that catches it ever being handled somewhere that
    turns it into the latter.
    """
    response = TestClient(app).post("/api/route", json={"origin": [51.16, 71.47]})

    assert response.status_code == 422


# The three tests that lived here drove scored_edges' compute branch: that it
# keyed on the date, on the minute, and that the cache held. That branch is
# gone, and with it the bug class -- a wrong sun served from a stale key. The
# same risk now sits one layer down, in the filename scores.load builds, where
# test_scores.py::test_another_date_is_not_borrowed already holds it.


@pytest.mark.parametrize("minute", [1, 10, 30, 59, -1])
def test_minute_off_the_step_is_rejected(minute):
    """Only the stamps a tileset can exist for. Anything else is a scored graph
    built for a sun the map never drew, and an unbounded number of them.
    """
    with pytest.raises(ValidationError):
        RouteRequest(**valid_request(minute=minute))


@pytest.mark.parametrize("minute", [0, 20, 40])
def test_minute_on_the_step_is_accepted(minute):
    assert RouteRequest(**valid_request(minute=minute)).minute == minute


def test_minute_defaults_to_the_top_of_the_hour():
    """A caller that has not heard of sub-hour stamps still gets a valid one."""
    fields = valid_request()
    del fields["minute"]

    assert RouteRequest(**fields).minute == 0


def day_body(**overrides) -> dict:
    """A /api/day body, JSON-ready and with no stamp in it.

    The scan is the one endpoint that names no time: it asks about every stamp
    the date has, which is what "when should I leave?" means.
    """
    return {
        "origin": [51.1605, 71.4704],
        "destination": [51.1700, 71.4300],
        "date": today().isoformat(),
        "alpha": 3.0,
    } | overrides


def test_day_request_inherits_the_bounds_a_route_request_has():
    """Both endpoints take the same two ends, date and preference, so they take
    them from the same model. Written down because the alternative -- a second
    model with the fields copied over -- is how one of them ends up a year
    later with a validator the other one grew and it did not.
    """
    assert WalkRequest(**day_body()).alpha == 3.0

    for far in (today() + dt.timedelta(days=400), today() - dt.timedelta(days=400)):
        with pytest.raises(ValidationError):
            WalkRequest(**day_body(date=far))

    for rejected in (float("nan"), float("inf"), main.MAX_ALPHA + 0.1):
        with pytest.raises(ValidationError):
            WalkRequest(**day_body(alpha=rejected))


def test_day_endpoint_rejects_a_body_with_no_date():
    """422, the same as /api/route. The date is what keeps the scan on the sun
    the map is drawing, and a default here would be the server guessing it.
    """
    body = day_body()
    del body["date"]

    assert TestClient(app).post("/api/day", json=body).status_code == 422


def route_body(**overrides) -> dict:
    """A /api/route body, JSON-ready.

    valid_request builds kwargs for the model, where a date object is what the
    validators want. This one crosses the wire, where it has to be a string.
    """
    return valid_request(**overrides) | {"date": valid_request()["date"].isoformat()}


def a_day(edges: int = 1):
    """The smallest thing the router will accept as a date's worth of sun."""
    return at_one_stamp(np.full(edges, 0.5, dtype="float32"), dt.time(12, 0))


def test_scored_day_covers_exactly_the_stamps_the_tiles_were_cut_for(monkeypatch):
    """daylight_times is what export_shadow_tiles.py cut the tiles and the
    scores with. Reading the window from anywhere else -- a range of hours
    written down here, the manifest, the clock -- is how a scan ends up asking
    for a stamp that has no answer on disk, in December especially.
    """
    asked: list[dt.time] = []

    def fake_load(cache, radius, date, at, index):
        asked.append(at)
        return pd.Series([0.5], index=index)

    edges = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (100, 0)])], crs=CRS)
    monkeypatch.setattr(main, "graph_edges", lambda: edges)
    monkeypatch.setattr(main.scores, "missing", lambda *args: [])
    monkeypatch.setattr(main.scores, "load", fake_load)
    clear_caches()

    day = main.scored_day(today())

    assert asked == daylight_times(LAT, LON, today(), TZ)
    # Every stamp, plus the night row underneath them.
    assert day is not None
    assert len(day.shade) == len(asked) + 1

    clear_caches()


def test_scored_day_is_all_of_a_date_or_none_of_it(monkeypatch):
    """A day with a hole in it would route a walk straight through the hole,
    weighting those streets as though they were in full sun -- which is a wrong
    answer delivered confidently, and the reason scores.load is strict.
    """
    edges = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (100, 0)])], crs=CRS)
    calls = {"n": 0}

    def sometimes(cache, radius, date, at, index):
        calls["n"] += 1
        return None if calls["n"] == 3 else pd.Series([0.5], index=index)

    monkeypatch.setattr(main, "graph_edges", lambda: edges)
    monkeypatch.setattr(main.scores, "missing", lambda *args: [])
    monkeypatch.setattr(main.scores, "load", sometimes)
    clear_caches()

    assert main.scored_day(today()) is None

    clear_caches()


def test_day_endpoint_declines_before_it_reaches_the_graph(monkeypatch):
    """The guard that makes a scan safe to offer at all, and it has to be the
    cheap half that runs first.

    One missing stamp is a miss /api/route can still answer around, needing
    only the stamp it departs in. Twenty-four of them is a day nobody can
    answer. Either way the check has to be the cheap half: statting the files
    costs nothing; loading 153k edges to reach the same conclusion does not.
    """
    def no_graph():
        raise AssertionError("the scan must decline before it reaches the graph")

    monkeypatch.setattr(main, "graph", no_graph)
    monkeypatch.setattr(main, "graph_edges", no_graph)
    monkeypatch.setattr(main.scores, "missing", lambda *args: [dt.time(6, 0), dt.time(6, 20)])
    clear_caches()

    response = TestClient(app).post("/api/day", json=day_body())

    assert response.status_code == 503
    # Names the script that fixes it. A bare 503 sends a reader to the logs for
    # something a sentence can tell them.
    assert "export_shadow_tiles.py" in response.json()["detail"]

    clear_caches()


def test_day_endpoint_passes_the_signed_alpha_through(monkeypatch):
    """Signed all the way down. A scan that dropped the sign would plot the
    shadiest hour to somebody who asked for the sunniest.
    """
    seen = {}

    def fake_departures(streets, day, origin, destination, alpha):
        seen["alpha"] = alpha
        return {"baseline_distance_m": 100.0, "baseline_duration_s": 74.0, "departures": []}

    monkeypatch.setattr(main, "scored_day", lambda date: a_day())
    monkeypatch.setattr(main, "streets", lambda: None)
    monkeypatch.setattr(main, "departures", fake_departures)

    response = TestClient(app).post("/api/day", json=day_body(alpha=-4.0))

    assert response.status_code == 200
    assert seen["alpha"] == -4.0


def test_route_falls_back_to_one_stamp_without_a_cached_day(monkeypatch):
    """A date missing some of its stamps still routes at the one it departs in.

    The narrower of the two reads, and why /api/route declines less often than
    /api/day: a single route can be priced from a single stamp. What it no
    longer does is build that stamp when it is absent -- see the test below.
    """
    edges = gpd.GeoDataFrame(
        {"shade_fraction": [0.4]}, geometry=[LineString([(0, 0), (100, 0)])], crs=CRS)

    monkeypatch.setattr(main, "scored_day", lambda date: None)
    monkeypatch.setattr(main, "scored_edges", lambda date, hour, minute: edges)

    day = main.sun_over(today(), dt.time(13, 0))

    assert day is not None
    assert not day.crosses_stamps
    assert day.shade.tolist() == [[pytest.approx(0.4)]]


def test_route_declines_a_stamp_the_export_never_wrote(monkeypatch):
    """The cold path, closed.

    This used to compute the field itself: half a minute of one request, on a
    key -- date, hour, minute -- the caller picked, with about 52,000 of them
    reachable against a cache of 96. So the caller decided when the server
    spent half a minute, which is the whole of the problem. It now refuses and
    names the script, the way /api/day always has.

    The graph stands in as a tripwire: declining has to happen before the
    expensive half, or the refusal costs what it was meant to avoid. Note that
    scored_edges is the real one here. Stubbing it out would step straight over
    the line under test -- the stat that has to come before the load.
    """
    def no_graph(*args, **kwargs):
        raise AssertionError("a declined route must not reach the graph")

    monkeypatch.setattr(main, "scored_day", lambda date: None)
    monkeypatch.setattr(main.scores, "scores_path",
                        lambda cache, radius, date, at: Path("no-such-stamp.parquet"))
    monkeypatch.setattr(main, "graph", no_graph)
    monkeypatch.setattr(main, "graph_edges", no_graph)
    monkeypatch.setattr(main.scores, "missing", lambda *args: [dt.time(6, 0)])
    clear_caches()

    response = TestClient(app).post("/api/route", json=route_body())

    assert response.status_code == 503
    assert "export_shadow_tiles.py" in response.json()["detail"]

    clear_caches()


def test_a_date_with_no_daylight_is_told_why_rather_than_sent_to_the_script(monkeypatch):
    """Astana never sees this, but both endpoints decline through one helper
    now, and the two reasons it declines for are not the same thing. Missing
    scores is something a developer fixes by running the export. A sun that
    never rises is not, and sending them to the script would be a lie.
    """
    monkeypatch.setattr(main, "daylight_times", lambda *args: [])

    detail = main.no_scores(today()).detail

    assert "does not rise" in detail
    assert "export_shadow_tiles.py" not in detail
