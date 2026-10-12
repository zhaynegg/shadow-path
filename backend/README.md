# Shadow Path API

Run the API from this directory with `uv run uvicorn backend.main:app --reload --port 8000`.
Tests run with `uv run pytest tests`.

## Automatic route preference

The server chooses the route preference. Requests to `/api/route` and `/api/day`
contain origin, destination and the shadow tiles' date; a route also contains
hour and minute. A caller-supplied `alpha` is rejected with HTTP 422.

`core/preference.py` holds the policy. Winter (December–February) seeks sun,
and summer (June–August) seeks shade. Both use a fixed internal strength of 6.
Spring and autumn use the forecast at the selected departure hour:

- A feels-like temperature of 12°C or less seeks sun; 22°C or more seeks shade.
  Strength ranges from 2 to 6 as the temperature moves away from the threshold.
- Between those thresholds the route follows the shortest path.
- Cloud cover of at least 85%, or precipitation of at least 0.2 mm, also uses
  the shortest path, since a detour for geometric shadows offers little benefit.

These temperature thresholds are product defaults. They can be adjusted in one
place without adding a user control or changing the routing algorithm.

The hourly data comes from the [Open-Meteo forecast API](https://open-meteo.com/en/docs):
air temperature, apparent temperature, total cloud cover and precipitation.
Times are interpreted in Astana's UTC+5 timezone. Sub-hour departures use their
containing forecast hour; precipitation describes the preceding hour's sum.
The server requests one date at a time, with a 3-second network timeout, and
caches successful results for 30 minutes and failures for 60 seconds. No API
key or browser-side weather request is required.

Only yesterday through the next 15 days is requested from the forecast API.
Other dates, an unavailable provider or an incomplete forecast hour use an
explicit seasonal fallback: March/April and October/November seek sun;
May/September seek shade, with internal strength 3. A fallback never claims to
be live weather. Precomputed shadow scores are still required to plan routes;
weather does not bypass the existing HTTP 503 response for missing scores.

## Read-only policy metadata

`GET /api/preference?date=2026-10-12&hour=12&minute=0` returns the preference
without loading the street graph or requiring shadow scores. `/api/route`
includes the same object under `preference`. `/api/day` has a noon summary
under `preference`, and each departure row has its own `preference`; a cold
morning and a warm afternoon can therefore choose different route modes.

```json
{
  "mode": "sun",
  "season": "autumn",
  "source": "weather",
  "reason": "cold",
  "weather": {
    "time": "2026-10-12T12:00+05:00",
    "temperature_c": 9.0,
    "apparent_temperature_c": 7.0,
    "cloud_cover_percent": 20.0,
    "precipitation_mm": 0.0
  },
  "weather_provider": "Open-Meteo",
  "weather_url": "https://open-meteo.com/"
}
```

`mode` is `sun`, `shade` or `balanced`; `source` is `season`, `weather` or
`fallback`. Reasons are `winter`, `summer`, `cold`, `warm`, `comfortable`,
`overcast`, `precipitation` and `seasonal_fallback`. `weather`,
`weather_provider` and `weather_url` are null when weather is not used.
The coefficient stays internal, including in these read-only responses.

Date bounds and the supported shadow stamps (minutes 0, 20 and 40) are shared
between the preview and route endpoints. Unknown request fields are rejected.
