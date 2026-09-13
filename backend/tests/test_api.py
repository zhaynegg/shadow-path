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
from backend.config import CRS, today
from backend.main import RouteRequest, app


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
    main.scored_edges.cache_clear()

    june = dt.date(today().year, 6, 21)
    december = dt.date(today().year, 12, 21)
    main.scored_edges(june, 13, 0)
    main.scored_edges(december, 13, 0)

    assert [when.date() for when in seen] == [june, december]

    main.scored_edges.cache_clear()


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
    main.scored_edges.cache_clear()

    main.scored_edges(today(), 13, 0)
    main.scored_edges(today(), 13, 0)

    assert len(seen) == 1

    main.scored_edges.cache_clear()


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
    main.scored_edges.cache_clear()

    for minute in (0, 20, 40):
        main.scored_edges(today(), 17, minute)

    assert [when.minute for when in seen] == [0, 20, 40]

    main.scored_edges.cache_clear()


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
