"""Season policy, weather validity/cache, and the public read-only explanation."""

import datetime as dt
import json
from http.client import IncompleteRead
from urllib.parse import parse_qs, urlparse

import pytest
from backend.config import today
from backend.core import preference
from backend.core.preference import HourlyWeather
from backend.main import app
from fastapi.testclient import TestClient


def weather(feels=5.0, cloud=20.0, precipitation=0.0):
    return HourlyWeather("2026-10-12T12:00+05:00", feels, feels, cloud, precipitation)


@pytest.fixture(autouse=True)
def isolated_weather_cache():
    preference._cache.clear()
    yield
    preference._cache.clear()


@pytest.mark.parametrize("month, mode", [(12, "sun"), (1, "sun"), (2, "sun"),
                                        (6, "shade"), (7, "shade"), (8, "shade")])
def test_winter_sun_and_summer_shade_do_not_wait_for_weather(monkeypatch, month, mode):
    def unexpected_weather(*args):
        raise AssertionError("fixed seasons do not need a network request")

    monkeypatch.setattr(preference, "weather_day", unexpected_weather)
    chosen = preference.at_time(dt.date(2026, month, 15), dt.time(12))

    assert chosen.mode == mode
    assert abs(chosen.alpha) == 6
    assert chosen.source == "season"
    assert chosen.public()["weather"] is None
    assert "alpha" not in chosen.public()


@pytest.mark.parametrize("month", [3, 5, 9, 11])
def test_transitional_seasons_follow_feels_like_temperature(month):
    date = dt.date(2026, month, 15)
    cold = preference.choose(date, weather(-5))
    warm = preference.choose(date, weather(32))
    pleasant = preference.choose(date, weather(18))

    assert cold.mode == "sun"
    assert cold.reason == "cold"
    assert warm.mode == "shade"
    assert warm.reason == "warm"
    assert pleasant.mode == "balanced"
    assert pleasant.alpha == 0
    assert all(abs(p.alpha) <= 6 for p in (cold, warm, pleasant))


def test_cloud_and_precipitation_avoid_a_detour_for_geometric_shadows():
    date = dt.date(2026, 10, 15)
    overcast = preference.choose(date, weather(4, cloud=95))
    rainy = preference.choose(date, weather(30, precipitation=0.5))

    assert overcast.mode == rainy.mode == "balanced"
    assert overcast.reason == "overcast"
    assert rainy.reason == "precipitation"
    assert overcast.public()["weather_provider"] == "Open-Meteo"


@pytest.mark.parametrize("month, mode", [(3, "sun"), (4, "sun"), (5, "shade"),
                                        (9, "shade"), (10, "sun"), (11, "sun")])
def test_missing_weather_uses_an_explicit_modest_seasonal_fallback(month, mode):
    chosen = preference.choose(dt.date(2026, month, 15))

    assert chosen.mode == mode
    assert abs(chosen.alpha) == 3
    assert chosen.source == "fallback"
    assert chosen.reason == "seasonal_fallback"
    assert chosen.public()["weather"] is None
    assert chosen.public()["weather_provider"] is None


def payload(date, feels=5):
    return {"hourly": {
        "time": [f"{date.isoformat()}T12:00", f"{date.isoformat()}T13:00"],
        "temperature_2m": [feels + 1, feels + 1],
        "apparent_temperature": [feels, feels],
        "cloud_cover": [20, 20],
        "precipitation": [0, 0],
    }}


def test_fetch_requests_only_a_single_city_day_with_a_timeout(monkeypatch):
    date = today()
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size == preference.MAX_RESPONSE_BYTES + 1
            return json.dumps(payload(date)).encode()

    def fake_open(request, timeout):
        seen["query"] = parse_qs(urlparse(request.full_url).query)
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(preference, "urlopen", fake_open)
    rows = preference.weather_day(date)

    assert seen["timeout"] == 3
    assert seen["query"]["start_date"] == seen["query"]["end_date"] == [date.isoformat()]
    assert seen["query"]["timezone"] == ["Asia/Almaty"]
    assert rows[12].time == f"{date.isoformat()}T12:00+05:00"
    assert rows[12].apparent_temperature_c == 5


def test_successful_weather_is_cached_then_refreshed(monkeypatch):
    date = today()
    elapsed = [100.0]
    calls = []

    def fetch(asked):
        calls.append(asked)
        return {12: weather()}

    monkeypatch.setattr(preference.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(preference, "_fetch_weather", fetch)
    preference.weather_day(date)
    preference.weather_day(date)
    assert calls == [date]

    elapsed[0] += preference.WEATHER_TTL_S + 1
    preference.weather_day(date)
    assert calls == [date, date]


def test_weather_failure_is_graceful_and_briefly_cached(monkeypatch):
    date = today()
    elapsed = [100.0]
    calls = []

    def failed(asked):
        calls.append(asked)
        raise TimeoutError("forecast unavailable")

    monkeypatch.setattr(preference.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(preference, "_fetch_weather", failed)
    assert preference.weather_day(date) == {}
    assert preference.weather_day(date) == {}
    assert calls == [date]

    elapsed[0] += preference.WEATHER_FAILURE_TTL_S + 1
    assert preference.weather_day(date) == {}
    assert calls == [date, date]


def test_truncated_http_response_uses_and_caches_the_seasonal_fallback(monkeypatch):
    date = dt.date(2026, 10, 12)
    elapsed = [100.0]
    calls = []

    class TruncatedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            raise IncompleteRead(b"{", 20)

    def truncated_open(request, timeout):
        calls.append(request.full_url)
        return TruncatedResponse()

    monkeypatch.setattr(preference, "today", lambda: date)
    monkeypatch.setattr(preference.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(preference, "urlopen", truncated_open)

    for at in (dt.time(12), dt.time(13)):
        chosen = preference.at_time(date, at)
        assert chosen.mode == "sun"
        assert chosen.source == "fallback"
        assert chosen.reason == "seasonal_fallback"
        assert chosen.weather is None
    assert len(calls) == 1

    elapsed[0] += preference.WEATHER_FAILURE_TTL_S + 1
    assert preference.at_time(date, dt.time(12)).source == "fallback"
    assert len(calls) == 2


@pytest.mark.parametrize("offset", [-30, 16, 365])
def test_uncovered_dates_do_not_borrow_todays_weather(monkeypatch, offset):
    def no_network(*args):
        raise AssertionError("out-of-window dates should not fetch a forecast")

    monkeypatch.setattr(preference, "_fetch_weather", no_network)
    assert preference.weather_day(today() + dt.timedelta(days=offset)) == {}


@pytest.mark.parametrize("field, invalid", [("temperature_2m", None),
                                          ("cloud_cover", float("nan")),
                                          ("cloud_cover", 110),
                                          ("precipitation", -1)])
def test_incomplete_or_invalid_hours_are_not_presented_as_weather(field, invalid):
    date = today()
    data = payload(date)
    data["hourly"][field][0] = invalid

    rows = preference._parse_weather(data, date)

    assert 12 not in rows
    assert 13 in rows


def test_weather_from_another_date_is_not_reused():
    assert preference._parse_weather(payload(today()), today() + dt.timedelta(days=1)) == {}


def test_forecast_air_temperature_is_used_if_feels_like_is_null():
    date = today()
    data = payload(date)
    data["hourly"]["apparent_temperature"][0] = None

    row = preference._parse_weather(data, date)[12]
    assert row.apparent_temperature_c == row.temperature_c


def test_read_only_preference_works_without_shadow_scores(monkeypatch):
    monkeypatch.setattr(preference, "weather_day", lambda date: {12: weather(30)})
    date = today().replace(month=10, day=12)
    response = TestClient(app).get("/api/preference", params={"date": date.isoformat(), "hour": 12})

    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "shade"
    assert data["source"] == "weather"
    assert "alpha" not in data


@pytest.mark.parametrize("extra", [{"alpha": 0}, {"hour": 24}, {"minute": 10},
                                   {"date": (today() + dt.timedelta(days=400)).isoformat()}])
def test_preference_query_has_the_same_date_and_stamp_bounds_as_routes(extra):
    response = TestClient(app).get("/api/preference", params={"date": today().isoformat()} | extra)
    assert response.status_code == 422
