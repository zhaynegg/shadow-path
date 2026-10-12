"""Server-owned walking preference, with hourly weather in spring and autumn.

The preference is a routing policy, not a value supplied by the walker. Weather
is fetched once per date for Astana; an unavailable forecast never prevents a
route. Its fallback is explicit in the public metadata so an old shadow date
is not passed off as today's weather.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from http.client import HTTPException as HTTPTransportError
from threading import Lock
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backend.config import LAT, LON, TZ, today

WEATHER_API = "https://api.open-meteo.com/v1/forecast"
WEATHER_URL = "https://open-meteo.com/"
WEATHER_TIMEOUT_S = 3.0
WEATHER_TTL_S = 30 * 60
WEATHER_FAILURE_TTL_S = 60
MAX_CACHED_DATES = 32
MAX_RESPONSE_BYTES = 128 * 1024

# Positive seeks shade, negative seeks sun. Keep seasonal choices together:
# winter brings sun, summer brings shade, and the shoulder seasons use weather.
SEASON_ALPHA = {"winter": -6.0, "summer": 6.0}
COLD_C = 12.0
WARM_C = 22.0
OVERCAST_PERCENT = 85.0
WET_MM = 0.2


@dataclass(frozen=True)
class HourlyWeather:
    time: str
    temperature_c: float
    apparent_temperature_c: float
    cloud_cover_percent: float
    precipitation_mm: float


@dataclass(frozen=True)
class Preference:
    alpha: float
    season: str
    source: str
    reason: str
    weather: HourlyWeather | None = None

    @property
    def mode(self) -> str:
        return "shade" if self.alpha > 0 else "sun" if self.alpha < 0 else "balanced"

    def public(self) -> dict:
        """Explain the decision without exposing a tunable routing coefficient."""
        return {
            "mode": self.mode,
            "season": self.season,
            "source": self.source,
            "reason": self.reason,
            "weather": asdict(self.weather) if self.weather is not None else None,
            "weather_provider": "Open-Meteo" if self.weather is not None else None,
            "weather_url": WEATHER_URL if self.weather is not None else None,
        }


def season_of(date: dt.date) -> str:
    if date.month in (12, 1, 2):
        return "winter"
    if date.month in (3, 4, 5):
        return "spring"
    if date.month in (6, 7, 8):
        return "summer"
    return "autumn"


def choose(date: dt.date, weather: HourlyWeather | None = None) -> Preference:
    """Apply a modest, bounded policy to a date and its departure-hour forecast.

    The 12/22 C thresholds are product choices, rather than medical comfort
    claims. Rain or a nearly overcast sky removes the benefit of paying a
    detour for geometric sun/shade, so those walks use the shortest path.
    """
    season = season_of(date)
    if season in SEASON_ALPHA:
        return Preference(SEASON_ALPHA[season], season, "season", season)

    if weather is None:
        # Early spring and late autumn are cold in Astana; late spring and
        # early autumn are the warmer shoulders. No live weather is invented.
        alpha = 3.0 if date.month in (5, 9) else -3.0
        return Preference(alpha, season, "fallback", "seasonal_fallback")

    if weather.precipitation_mm >= WET_MM:
        return Preference(0.0, season, "weather", "precipitation", weather)
    if weather.cloud_cover_percent >= OVERCAST_PERCENT:
        return Preference(0.0, season, "weather", "overcast", weather)

    feels = weather.apparent_temperature_c
    if feels <= COLD_C:
        strength = min(6.0, 2.0 + (COLD_C - feels) / 3.0)
        return Preference(-strength, season, "weather", "cold", weather)
    if feels >= WARM_C:
        strength = min(6.0, 2.0 + (feels - WARM_C) / 3.0)
        return Preference(strength, season, "weather", "warm", weather)
    return Preference(0.0, season, "weather", "comfortable", weather)


def _number(value, lower: float, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("missing weather variable")
    result = float(value)
    if not math.isfinite(result) or not lower <= result <= upper:
        raise ValueError("invalid weather variable")
    return result


def _parse_weather(payload: dict, date: dt.date) -> dict[int, HourlyWeather]:
    hourly = payload.get("hourly", {})
    if not isinstance(hourly, dict):
        return {}
    times = hourly.get("time", [])
    if not isinstance(times, list):
        return {}
    rows = {}
    for index, value in enumerate(times):
        try:
            stamp = dt.datetime.fromisoformat(value)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=TZ)
            stamp = stamp.astimezone(TZ)
            if stamp.date() != date or stamp.minute != 0:
                continue

            temperature = _number(hourly["temperature_2m"][index], -100, 80)
            apparent = hourly.get("apparent_temperature", [])[index]
            # Some models lack feels-like data, but a real air temperature
            # still allows the policy to work. Cloud/rain remain mandatory.
            feels = temperature if apparent is None else _number(apparent, -120, 100)
            rows[stamp.hour] = HourlyWeather(
                time=stamp.isoformat(timespec="minutes"),
                temperature_c=temperature,
                apparent_temperature_c=feels,
                cloud_cover_percent=_number(hourly["cloud_cover"][index], 0, 100),
                precipitation_mm=_number(hourly["precipitation"][index], 0, 1000),
            )
        except (IndexError, KeyError, TypeError, ValueError):
            # A missing/malformed hour is a fallback for that hour, rather
            # than borrowing a forecast from another time or another date.
            continue
    return rows


def _fetch_weather(date: dt.date) -> dict[int, HourlyWeather]:
    query = urlencode({
        "latitude": LAT,
        "longitude": LON,
        "hourly": "temperature_2m,apparent_temperature,cloud_cover,precipitation",
        "timezone": "Asia/Almaty",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
        "start_date": date.isoformat(),
        "end_date": date.isoformat(),
    })
    request = Request(f"{WEATHER_API}?{query}", headers={
        "Accept": "application/json",
        "User-Agent": "ShadowPath/0.1",
    })
    with urlopen(request, timeout=WEATHER_TIMEOUT_S) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("weather response exceeds the expected daily payload")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        return {}
    return _parse_weather(payload, date)


_cache: OrderedDict[dt.date, tuple[float, dict[int, HourlyWeather]]] = OrderedDict()
_cache_lock = Lock()


def weather_day(date: dt.date) -> dict[int, HourlyWeather]:
    """One bounded forecast read, cached across route, scan and preview calls.

    The forecast API covers 16 days. Yesterday is also requested when shadow
    tiles cross midnight; older/further dates use the explicit season fallback.
    A failure is briefly cached too so every drag of the clock cannot retry it.
    """
    offset = (date - today()).days
    if not -1 <= offset <= 15:
        return {}

    # Fetch under the lock to coalesce simultaneous requests for the same day.
    # Sockets have a timeout and the cached values are read-only.
    with _cache_lock:
        now = time.monotonic()
        cached = _cache.get(date)
        if cached is not None and cached[0] > now:
            _cache.move_to_end(date)
            return cached[1]
        try:
            rows = _fetch_weather(date)
        except (OSError, HTTPTransportError, ValueError, TypeError):
            rows = {}
        ttl = WEATHER_TTL_S if rows else WEATHER_FAILURE_TTL_S
        _cache[date] = (time.monotonic() + ttl, rows)
        _cache.move_to_end(date)
        while len(_cache) > MAX_CACHED_DATES:
            _cache.popitem(last=False)
        return rows


def for_day(date: dt.date, times: tuple[dt.time, ...]) -> dict[dt.time, Preference]:
    """Every departure's choice from a single weather snapshot."""
    rows = weather_day(date) if season_of(date) not in SEASON_ALPHA else {}
    return {at: choose(date, rows.get(at.hour)) for at in times}


def at_time(date: dt.date, at: dt.time) -> Preference:
    return for_day(date, (at,))[at]
