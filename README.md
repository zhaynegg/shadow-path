# shadow-path

Shade-aware pedestrian routing for **Astana**. Given an origin, a destination,
and a departure time, find the walk that keeps you out of the sun — or in it —
and show what that detour costs against the plain shortest path.

> **Status:** scaffolding plus a data feasibility study. The backend (FastAPI +
> uv) and frontend (React 19 / TypeScript / Vite / MapLibre) are set up and the
> `/api` dev proxy is wired; no application code exists yet. The data findings
> below are measured, not assumed — see [Data](#data).

## How it works

Buildings cast shadows, shadows fall on streets, and a street graph whose edges
know how shaded they are can be routed over with a weight that trades distance
against sun exposure.

```
 Browser (React + MapLibre)
   │  POST /api/route  {origin, dest, departure, shade_preference}
   │  (shadows are static .pmtiles, built ahead of time — see below)
   ▼
 FastAPI (uvicorn :8000)
   ▼
 ┌─────────────── routing core ───────────────┐
 │  graph.py     OSM walk network (osmnx)     │
 │  buildings.py footprints + heights         │
 │  solar.py     pysolar → altitude, azimuth  │
 │  shadows.py   footprints × sun → polygons  │
 │  scoring.py   edge ∩ shadow → shade_frac   │
 │  routing.py   weighted A* over networkx    │
 └────────────────────┬───────────────────────┘
                      ▼
            data/cache/  (graphml, geoparquet,
            per-time-bucket edge scores)
```

### 1. Ingest — once per city, cached

`osmnx` pulls the pedestrian network (`network_type="walk"`) and building
footprints. Everything is reprojected from EPSG:4326 to the local UTM zone
immediately; every geometric step downstream wants meters, not degrees.

Heights are resolved here, once, and baked into the cache with a provenance
column — see [Height resolution](#height-resolution).

The graph persists as GraphML and the buildings as GeoParquet under
`data/cache/`, which `.gitignore` carves out as "regenerable, large."

### 2. Shadow geometry — per timestamp

`pysolar` gives solar altitude and azimuth at the city centroid. Each footprint
casts a shadow of length `h / tan(altitude)` in the anti-solar direction; the
cheap standard approximation is the convex hull of the footprint unioned with
its translated copy. All shadows are unioned and indexed with shapely's
`STRtree`.

**Clamp the shadow length.** At Astana's latitude this is not an edge case (see
[Latitude](#latitude-changes-the-product)) — `h / tan(altitude)` runs away for a
large share of the year. Cap it around 200–300 m or the polygon union becomes
both meaningless and computationally explosive. Treat `altitude <= 0` as fully
shaded and skip the geometry entirely.

### 3. Scoring

Edges are split into ~10–20 m sub-segments so shade resolves below street-block
scale, then:

```
shade_fraction = length(segment ∩ shadow_union) / length(segment)
```

### 4. Routing

A* over the networkx graph with:

```
weight = length × (1 + |α| × unwanted)

unwanted = 1 − shade_fraction   when α ≥ 0   (sunlit metres, to a shade-seeker)
         =     shade_fraction   when α < 0   (shaded metres, to a sun-seeker)
```

`α` is the user's preference; `α = 0` is the plain shortest path. The `α = 0`
baseline is always computed alongside so the UI can say *"18% longer, 2.3× more
shade"* — that comparison is the product.

`α` is **signed**. Negative α is sun-seeking, and in Astana that is not a
novelty mode — it's the winter product.

The absolute value and the swap are not cosmetic. Written as the single line
`1 + α × (1 − shade_fraction)`, a negative α prices a sunlit street below zero,
and a walker could pace one back and forth forever to drive the total lower —
there is no cheapest path left to find. A* does not detect that; it settles
nodes assuming they can only get dearer, and answers anyway. Moving the penalty
onto the unwanted half instead keeps every weight ≥ length, which also keeps the
straight-line A* heuristic admissible at any α.

## The decision that shapes everything

Steps 2 and 3 are expensive and time-dependent. Step 1 is expensive and isn't.

**Quantize departure time into 15-minute buckets and precompute per-bucket edge
scores.** A route request then becomes a graph search over cached weights —
milliseconds. Otherwise you recompute a citywide polygon union per request.

Corollary: scope to Astana's bbox. Arbitrary origins would mean OSM downloads in
the request path.

## Data

### Sources

| Layer | Source | Notes |
|---|---|---|
| Walk network | OSM via osmnx | good coverage, nothing to decide |
| Footprints | OSM, backfill from Overture | 50,091 polygons in Astana, 25.4 km² of roof |
| Heights | OSM `building:levels` + prior + manual survey | the hard part, below |
| Sanity check | GHSL `GHS-BUILT-H` (100 m global raster) | too coarse per building; catches district-scale errors |

**Ruled out: Google Maps Platform and SerpAPI.** SerpAPI returns places, not
geometry — no footprints, no heights. Google's APIs either don't expose
footprint+height at city scale (Directions won't take a custom edge weight; 3D
Tiles is a photogrammetry mesh, not attributed geometry; Solar API's building
heights don't cover Kazakhstan) or are barred by terms that prohibit caching,
derived datasets, and display on a non-Google map. Precomputing a cached scored
graph rendered in MapLibre violates all three.

No LOD1/LOD2 3D city model exists for Kazakhstan.

### Measured coverage

From `scripts/height_coverage.py`, 50,091 Astana footprints:

| Signal | Buildings | % count | % area | % shadow-mass* |
|---|---|---|---|---|
| `height` tag | 207 | **0.4%** | 1.5% | 2.2% |
| `building:levels` | 20,189 | 40.3% | 50.9% | **66.9%** |
| neither | 29,855 | 59.6% | 49.0% | 33.0% |

<sub>*shadow-mass = √area × levels, a proxy for shadow actually thrown</sub>

`height` is effectively absent — the chain starts at `building:levels`.
Coverage correlates helpfully with what matters: `apartments` 75.7% tagged,
`retail` 82.5%, `commercial` 80.3%, `office` 73.0%. The untagged bulk is small
`building=yes` footprints.

### Height resolution

A (building type × size class) median-levels prior, validated 70/30:

```
(type × size) median   MAE 0.96 storeys   85.0% within 1
constant 1             MAE 1.41 storeys   80.3% within 1
constant 3             MAE 2.55 storeys    8.3% within 1
```

**The overall MAE is a trap.** On buildings ≥5 storeys the same prior scores
**MAE 5.10 storeys**. The flattering headline comes from 15,000 single-storey
sheds — `building=yes` under 150 m² is 99.5% one storey — while the ~3,000
buildings that cast every shadow worth routing around are where it fails.

Note also that a constant-3 fallback is the *worst* option here: Astana's
untagged population is mostly single-storey, so 3 inflates it. Constant 1 beats
it outright.

So: **two regimes, explicitly.**

- **Small / simple buildings** — the prior is fine and near-deterministic.
- **Shadow-relevant** (type in the big list, or footprint >500 m²; 10,550
  buildings, 21.1% of all, 53.6% tagged) — use the tagged value, or a manual
  override, or render as low confidence. Never let the prior silently invent a
  12-storey tower.

Every building carries a provenance column so the distinction survives into the
UI:

```
height_m: float
height_source: "osm_height" | "osm_levels" | "manual" | "type_prior"
```

With a third of shadow-mass resting on priors, greying out low-confidence
shadows is honest rather than decorative.

### Closing the gap

The untagged gap is concentrated, which makes it a bounded task:

```
50% of untagged shadow-mass  →  top  2,525 buildings  ( 8.4%)
60%                          →  top  5,114           (17.1%)
80%                          →  top 14,601           (48.8%)
```

`scripts/export_survey_queue.py` writes those buildings to
`data/survey_queue.csv` in priority order with an OSM link each. The top 1,000
are median 1,867 m² and mostly `apartments` — storeys countable off imagery in
seconds. Fill the `levels` column, load it as a `height_override` table keyed by
`(osm_element, osm_id)`, and contribute the same data back to OSM.

Keeping overrides local means the survey isn't blocked on OSM edit cycles.

### Latitude changes the product

Astana sits at **51.1°N**, which dominates the physics:

| Moment | Sun altitude | Shadow | 16 m height error becomes |
|---|---|---|---|
| Summer solstice, noon | 62.3° | 0.53 × h | 9 m |
| Equinox, noon | 38.8° | 1.24 × h | 20 m |
| Winter solstice, noon | 15.4° | 3.63 × h | 59 m |
| Low sun (7°) | 7.0° | 8.14 × h | 133 m |

Two consequences. **Sun-seeking is the winter product** — Astana is one of the
coldest capitals on earth and much of the city is in permanent geometric shade
in December; shade routing belongs to the mid-30s summer. And **height error is
tolerable exactly when it matters**: 9 m at midsummer noon is a pavement width,
while the 59–133 m winter errors land in the season where precision claims
should be softened anyway.

## API

```
POST /api/route
{
  "origin":           [lat, lon],
  "destination":      [lat, lon],
  "departure":        "2026-09-01T15:30:00+06:00",
  "shade_preference": 0.7
}
→
{
  "route":          <GeoJSON LineString>,
  "distance_m":     1840,
  "duration_s":     1360,
  "shade_fraction": 0.72,
  "confidence":     0.81,
  "baseline":       { "distance_m": 1520, "shade_fraction": 0.31 }
}

GET /api/health
```

Routes reach MapLibre as GeoJSON. Shadows do not, and deliberately so: the date
is fixed and only 16 hours of the day have sun, so there are just 16 shadow
fields and none of them ever change. `scripts/export_shadow_tiles.py` builds
each one into its own vector tileset ahead of time, and the browser reads them
the way it reads roads.

The map holds one source and one layer per hour, and the time slider only
changes which layer is visible. Nothing is computed on demand, so shadows are
already drawn wherever you pan and at every zoom, city-wide. All 16 tilesets
cost about 324 KB to open, because pmtiles is read by byte range — only the
tiles actually on screen are ever fetched.

## Layout

```
backend/
  scripts/
    height_coverage.py       measure OSM height coverage + prior accuracy
    export_survey_queue.py   emit the buildings worth surveying, by priority
    export_fixtures.py       write shadow GeoJSON fixtures for the frontend
    export_shadow_tiles.py   one .pmtiles shadow layer per daylight hour
  src/backend/
    main.py                  FastAPI app, CORS, routers
    config.py                bbox, cache dir, defaults, timezone
    api/
      routes.py              endpoints
      schemas.py             pydantic request/response models
    core/
      graph.py               OSM walk network load + cache
      buildings.py           footprints + HeightSource chain
      heights.py             prior, overrides, provenance
      solar.py               pysolar wrapper: (lat, lon, ts) → altitude, azimuth
      shadows.py             footprints + sun → unioned, indexed polygons
      scoring.py             edge sub-segmentation + shade fraction
      routing.py             weighted A*, baseline route, stats
      cache.py               disk cache keyed by (place, time-bucket)
    models.py                Segment, RouteResult, ShadowFrame

frontend/src/
  api/client.ts              typed fetch, mirrors schemas.py
  components/
    MapView.tsx
    layers/{Shadow,Route}Layer.tsx
    controls/{TimeSlider,ShadeSlider,EndpointPicker}.tsx
    RouteSummary.tsx
  hooks/{useRoute,useShadows}.ts
  state/store.ts             origin, destination, time, α

data/
  cache/                     gitignored: osmnx cache, geoparquet, scored graphs
  survey_queue.csv           buildings awaiting a manual storey count
```

## Running locally

Frontend:

```bash
cd frontend && npm install && npm run dev
```

Shadow tiles (needs `brew install tippecanoe`; takes a couple of minutes, and
only has to be redone if the date, the heights, or the footprints change):

```bash
cd backend && uv run python scripts/export_shadow_tiles.py
```

Data analysis:

```bash
cd backend && uv run python scripts/height_coverage.py
```

Backend (once `main.py` exists):

```bash
cd backend && uv run uvicorn backend.main:app --reload --port 8000
```

Vite proxies `/api` to `http://localhost:8000`, so the frontend uses relative
URLs in dev. In production, build the frontend to static assets and serve from
FastAPI's `StaticFiles` or a CDN.

## Known gaps

**Dependencies still to add:** `pydantic-settings`, `pytest` + `httpx`, and
`timezonefinder` (pysolar wants UTC; Astana is UTC+5 — or hardcode it while
single-city). `pyarrow` is already in.

**Districts aren't available.** OSM has only one of Astana's city districts as a
boundary polygon (Сарайшық ауданы, `admin_level=8`); Есіл, Алматы, Сарыарқа,
Байқоңыр and Нұра are absent. A district-keyed prior needs hand-drawn zones or
a distance-to-centre proxy.

**Model limits:** flat terrain, no trees, no awnings or arcades. Trees matter
most for perceived accuracy — OSM `natural=tree` gives points that could be
buffered into shade later. No DEM, so hills and their shadows are invisible.

## Attribution

Building and network data © OpenStreetMap contributors, ODbL. Attribution
belongs in the map corner from day one; note that ODbL's share-alike applies if
you ever redistribute the derived scored graph as a database.
