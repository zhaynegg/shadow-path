"""The HTTP layer: what a caller is allowed to ask for, and what gets cached.

Deliberately nothing here routes. `plan` already has its own tests, and going
through the endpoint would pull the graph and the footprints off disk -- minutes
on a warm cache, a failure on a cold one. What is untested without this file is
everything around that call: the bounds on the request, and the key the scored
graph is memoised under.
"""
import datetime as dt

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from shapely.geometry import LineString

# `import ... as`, not `from backend import main`: backend/__init__.py defines a
# main() of its own for the console script, and that name shadows this module.
import backend.main as main  # noqa: PLR0402 -- the rewrite ruff suggests is the bug
from backend.config import CRS, LAT, LON, TZ, today
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
    main.scored_edges.cache_clear()
    main.graph_edges.cache_clear()


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


def test_scored_edges_keys_on_the_date_not_only_the_hour(monkeypatch):
    """The bug this file exists for, and it never raised anything.

    13:00 in June and 13:00 in December are different suns -- mean shade across
    the walk network is 0.033 against 0.291. Keyed on the hour alone, the second
    date is served the first one's weights, the routes stay plausible, and
    nothing anywhere says the map rolled forward a day.

    Everything expensive is replaced below so the function can actually be
    called twice: a sun below the horizon takes the early return, and the graph
    is never read once graph_to_gdfs is standing in for it.
    """
    seen: list[dt.datetime] = []

    def fake_sun(lat, lon, when):
        seen.append(when)
        return -10.0, 0.0  # below the horizon: skips all the geometry

    edges = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (100, 0)])], crs=CRS)

    monkeypatch.setattr(main, "sun_position", fake_sun)
    monkeypatch.setattr(main, "graph", lambda: None)
    # Patched on the osmnx module as main.py sees it; monkeypatch puts it back.
    monkeypatch.setattr(main.ox, "graph_to_gdfs", lambda graph, nodes: edges)

    # lru_cache outlives the test that filled it. Without this the assertions
    # below read whatever an earlier run left in there.
    clear_caches()

    june = dt.date(today().year, 6, 21)
    december = dt.date(today().year, 12, 21)
    main.scored_edges(june, 13, 0)
    main.scored_edges(december, 13, 0)

    assert [when.date() for when in seen] == [june, december]

    clear_caches()


def test_scored_edges_reuses_the_same_date_and_time(monkeypatch):
    """The other half: the cache has to actually cache, or every request
    recomputes a citywide polygon union and the whole quantise-by-hour decision
    buys nothing.
    """
    seen: list[dt.datetime] = []

    def fake_sun(lat, lon, when):
        seen.append(when)
        return -10.0, 0.0

    edges = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (100, 0)])], crs=CRS)

    monkeypatch.setattr(main, "sun_position", fake_sun)
    monkeypatch.setattr(main, "graph", lambda: None)
    monkeypatch.setattr(main.ox, "graph_to_gdfs", lambda graph, nodes: edges)
    # This test counts sun_position calls, so it is about the path that
    # computes the field. Whether a developer has run export_shadow_tiles.py
    # must not decide which branch runs -- without this, the assertion below
    # passes on a cold checkout and fails on a warm one.
    monkeypatch.setattr(main.scores, "load", lambda *args, **kwargs: None)
    clear_caches()

    main.scored_edges(today(), 13, 0)
    main.scored_edges(today(), 13, 0)

    assert len(seen) == 1

    clear_caches()


def test_scored_edges_keys_on_the_minute_too(monkeypatch):
    """The same bug one level down, and it would look even more plausible.

    Low-sun hours are cut into thirds because an hour is too coarse a step
    there -- at 17:00 an hour redraws 59% of the network. Drop the minute from
    the key and 17:40 is served 17:00's weights, which is precisely the error
    the split exists to remove, back again and invisible.
    """
    seen: list[dt.datetime] = []

    def fake_sun(lat, lon, when):
        seen.append(when)
        return -10.0, 0.0

    edges = gpd.GeoDataFrame(geometry=[LineString([(0, 0), (100, 0)])], crs=CRS)

    monkeypatch.setattr(main, "sun_position", fake_sun)
    monkeypatch.setattr(main, "graph", lambda: None)
    monkeypatch.setattr(main.ox, "graph_to_gdfs", lambda graph, nodes: edges)
    # This test counts sun_position calls, so it is about the path that
    # computes the field. Whether a developer has run export_shadow_tiles.py
    # must not decide which branch runs -- without this, the assertion below
    # passes on a cold checkout and fails on a warm one.
    monkeypatch.setattr(main.scores, "load", lambda *args, **kwargs: None)
    clear_caches()

    for minute in (0, 20, 40):
        main.scored_edges(today(), 17, minute)

    assert [when.minute for when in seen] == [0, 20, 40]

    clear_caches()


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


def test_day_endpoint_scans_exactly_the_stamps_the_tiles_were_cut_for(monkeypatch):
    """daylight_times is what export_shadow_tiles.py cut the tiles and the
    scores with. Reading the window from anywhere else -- a range of hours
    written down here, the manifest, the clock -- is how the scan ends up
    asking for a stamp that has no answer on disk, in December especially.
    """
    seen: dict[str, list] = {}

    def fake_departures(graph, scored_by_time, origin, destination, alpha):
        seen["times"] = sorted(scored_by_time)
        seen["alpha"] = alpha
        return {"baseline_distance_m": 100.0, "departures": []}

    monkeypatch.setattr(main, "graph", lambda: None)
    monkeypatch.setattr(main, "scored_edges", lambda date, hour, minute: (date, hour, minute))
    monkeypatch.setattr(main.scores, "missing", lambda *args: [])
    monkeypatch.setattr(main, "departures", fake_departures)

    response = TestClient(app).post("/api/day", json=day_body(alpha=-4.0))

    assert response.status_code == 200
    assert seen["times"] == daylight_times(LAT, LON, today(), TZ)
    # Signed all the way through. A scan that dropped the sign would plot the
    # shadiest hour to somebody who asked for the sunniest.
    assert seen["alpha"] == -4.0


def test_day_endpoint_declines_rather_than_computing_a_day_of_fields(monkeypatch):
    """The guard that makes a scan safe to offer at all.

    One missing stamp is a cache miss the API absorbs by computing the field
    itself -- half a minute, and correct. Twenty-four of them is twelve minutes
    of one request holding the process, so the scan checks first. It has to
    check *before* touching the graph, or declining costs as much as agreeing.
    """
    def no_graph():
        raise AssertionError("the scan must decline before it reaches the graph")

    monkeypatch.setattr(main, "graph", no_graph)
    monkeypatch.setattr(main.scores, "missing", lambda *args: [dt.time(6, 0), dt.time(6, 20)])

    response = TestClient(app).post("/api/day", json=day_body())

    assert response.status_code == 503
    # Names the script that fixes it. A bare 503 sends a reader to the logs for
    # something a sentence can tell them.
    assert "export_shadow_tiles.py" in response.json()["detail"]


def test_day_endpoint_rejects_a_body_with_no_date():
    """422, the same as /api/route. The date is what keeps the scan on the sun
    the map is drawing, and a default here would be the server guessing it.
    """
    body = day_body()
    del body["date"]

    assert TestClient(app).post("/api/day", json=body).status_code == 422
