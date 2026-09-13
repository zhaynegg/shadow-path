# shadow-path

Shade-aware pedestrian routing for **Astana**. Given an origin, a destination,
and a departure time, find the walk that keeps you out of the sun — or in it —
and show what that detour costs against the plain shortest path.

> **Status:** working end to end for Astana. The map opens on the city's
> current hour, draws that day's shadows city-wide, and routes against them. A
> nightly GitHub Actions job rebuilds the tiles for the new date. Not deployed:
> CI uploads the tiles as a workflow artifact and nothing serves them yet. The
> data findings below are measured, not assumed — see [Data](#data).

## How it works

Buildings cast shadows, shadows fall on streets, and a street graph whose edges
know how shaded they are can be routed over with a weight that trades distance
against sun exposure.

```
 Browser (React + MapLibre)
   │  POST /api/route  {origin, destination, date, hour, alpha}
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
            data/cache/  (graphml, geoparquet)
            edge scores are memoised per (date, hour),
            in process — nothing on disk
```

### 1. Ingest — once per city, cached

`osmnx` pulls the pedestrian network (`network_type="walk"`) and building
footprints. Everything is reprojected from EPSG:4326 to the local UTM zone
immediately; every geometric step downstream wants meters, not degrees.

Heights are resolved here, once, and baked into the cache with a provenance
column — see [Height resolution](#height-resolution).

The graph persists as GraphML and the buildings as GeoParquet under
`data/cache/`, which `.gitignore` carves out as "regenerable, large" — with one
exception. `astana_buildings.parquet` is tracked, because the nightly tile build
runs on a fresh CI runner with no cache, and nothing in this repo can refetch it
(the Overpass call lives in `height_coverage.py`, which the export never calls).
Tracking it also pins the OSM snapshot, so the tiles change when the date
changes rather than when somebody edits a building in Astana.

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

**Quantize departure time into whole hours and cache the scored edges per
`(date, hour)`.** A route request then becomes a graph search over cached
weights — milliseconds. Otherwise you recompute a citywide polygon union per
request.

The date belongs in that key, not just the hour. 13:00 in June and 13:00 in
December are different suns — mean shade across the walk network is 0.033
against 0.291 — so an hour-only cache serves one for the other the first time
the map rolls forward a day.

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
  "origin":      [lat, lon],
  "destination": [lat, lon],
  "date":        "2026-09-12",
  "hour":        16,
  "alpha":       6.0
}
→
{
  "route":    { "distance_m": 2550, "shade_fraction": 0.37, "geometry": <LineString> },
  "baseline": { "distance_m": 1990, "shade_fraction": 0.00, "geometry": <LineString> }
}

GET /api/health
```

`date` is sent by the client, not read from the server's clock. It comes from
the tile manifest, so the router weights streets by the same sun the map drew:
if a nightly rebuild fails, both stay a day behind together instead of quietly
disagreeing. It is bounded to within a year of today — every distinct date is a
fresh shadow field over the routing footprints, and an unbounded range is an
unbounded amount of work a caller can ask for.

Routes reach MapLibre as GeoJSON. Shadows do not, and deliberately so: within a
day the sun repeats, so an hour's shadow field never changes once it is built.
`scripts/export_shadow_tiles.py` builds each daylight hour into its own vector
tileset ahead of time, and the browser reads them the way it reads roads.

How many tilesets exist is a property of the date, not a constant — 16 in June,
13 in mid-September, 8 at the winter solstice. The script writes what it
produced to `shadows/index.json`: the date, the hours, the layer name. The
frontend builds its sources from that manifest, because a daylight window
hardcoded on the other side is right for exactly one date, and asking for
`05.pmtiles` in December is a 404.

The map holds one source and one layer per hour, and the time slider only
changes which layer is visible. Nothing is computed on demand, so shadows are
already drawn wherever you pan and at every zoom, city-wide. Opening every
tileset costs a few hundred KB, because pmtiles is read by byte range — only the
tiles actually on screen are ever fetched.

## Layout

```
backend/
  scripts/
    height_coverage.py       measure OSM height coverage + prior accuracy
    height_neighbours.py     do the neighbours predict height? (spatial CV)
    export_survey_queue.py   emit the buildings worth surveying, by priority
    export_fixtures.py       write shadow GeoJSON fixtures for the frontend
    export_shadow_tiles.py   a .pmtiles layer per daylight hour, + index.json
    fetch_trees.py           cache OSM tree rows and points
    detect_trees.py          find canopy in Sentinel-2 (needs --group ml)
  src/backend/
    main.py                  FastAPI app: /api/route, /api/health
    config.py                paths, radius, timezone, city centre, today()
    core/
      graph.py               OSM walk network load + cache
      buildings.py           footprints + height chain + provenance
      solar.py               pysolar wrapper: (lat, lon, ts) → altitude, azimuth
      shadows.py             footprints + sun → unioned, indexed polygons
      trees.py               canopy polygons + the leaf-on season
      scoring.py             edge sub-segmentation + shade fraction
      routing.py             weighted A*, baseline route, stats
  tests/
    test_scoring.py, test_routing.py, test_api.py

frontend/src/
  api/client.ts              typed fetch; mirrors main.py and the tile manifest
  components/
    MapView.tsx              map, shadow layers, route layers, the clock
    RouteSummary.tsx
    controls/{TimeSlider,ShadeSlider}.tsx

.github/workflows/
  shadow-tiles.yml           nightly rebuild of the tiles for the current date

data/
  cache/                     gitignored (osmnx cache, graphml, scored graphs)
    astana_buildings.parquet ...except this, tracked so CI has footprints
    astana_trees.parquet     ...and this, 101 KB of mapped trees
    astana_canopy.parquet    ...and the detected canopy, 2.7 MB
  height_overrides.csv       hand-entered storey counts
  survey_queue.csv           buildings awaiting a manual storey count
```

## Running locally

Frontend:

```bash
cd frontend && npm install && npm run dev
```

Shadow tiles (needs `brew install tippecanoe`; about 12 s per daylight hour).
Defaults to today in Astana, and deletes any tileset the new date has no sun
for, so the directory always holds exactly one day:

```bash
cd backend && uv run python scripts/export_shadow_tiles.py
cd backend && uv run python scripts/export_shadow_tiles.py --date 2026-12-21
```

The same command runs nightly in CI — `.github/workflows/shadow-tiles.yml`, at
19:00 UTC, which is midnight in Astana. For now it uploads the result as a
workflow artifact; publishing is still an open decision.

Data analysis:

```bash
cd backend && uv run python scripts/height_coverage.py
```

Tests — fast, and none of them need the cache:

```bash
cd backend && uv run pytest
```

Backend:

```bash
cd backend && uv run uvicorn backend.main:app --reload --port 8000
```

Vite proxies `/api` to `http://localhost:8000`, so the frontend uses relative
URLs in dev. In production, build the frontend to static assets and serve from
FastAPI's `StaticFiles` or a CDN.

## Known gaps

**Dependencies still to add:** `pydantic-settings`. `pytest`, `ruff`, `scipy`
and `httpx` are in the dev group, `rasterio` and `scikit-learn` in an `ml`
group the API never installs, and `pyarrow` is in the main one.
The timezone is hardcoded to UTC+5 in `config.py` — the documented shortcut
while this is single-city, and `timezonefinder` is what replaces it.

**Nothing is deployed.** The tiles are built nightly and uploaded as an
artifact. Publishing them needs a decision about the backend too: GitHub Pages
is static, so `/api/route` would 404 there and routing would not work until
FastAPI is hosted somewhere.

**The endpoint is never exercised against the real city.** `test_api.py` covers
what a caller may ask for and what gets cached: the date bounds, the `alpha`
bounds — which are there to reject `inf` and `nan` as much as to fix a range,
since either sails through A* and comes back as a route with no error attached
— and that `scored_edges` keys on the date, which was a real bug and an
invisible one. What it deliberately does not do is route. A request through
`/api/route` pulls the graph and the footprints off disk, which is minutes on a
warm cache and a failure on a cold one, so the tests stand the expensive calls
aside and nothing checks that the pieces fit together with a real graph in
them.

**Districts aren't available.** OSM has only one of Astana's city districts as a
boundary polygon (Сарайшық ауданы, `admin_level=8`); Есіл, Алматы, Сарыарқа,
Байқоңыр and Нұра are absent. A district-keyed prior needs hand-drawn zones or
a distance-to-centre proxy.

**Trees are modelled, and mostly detected rather than mapped.** OSM has 2,909
tree features for all of Astana, reaching 0.1% of the walk network — nowhere
near enough to change a route. `scripts/detect_trees.py` fills in the rest from
Sentinel-2 imagery and takes that to **62%**.

The detector is a gradient-boosted tree over per-pixel spectral features — 7
bands plus NDVI, NDWI, NBR, a SWIR ratio and red-edge NDVI — trained on what OSM
already knows. Positives are `natural=tree` and `natural=tree_row`; negatives are
building roofs and, the ones that matter, `landuse=grass`, `meadow`, `farmland`
and `leisure=pitch`. Without the grass classes the model learns "vegetation",
flags every lawn in the city, and buries Astana in shade.

Scored on 1 km blocks of city, never on random pixels — neighbouring pixels are
nearly the same measurement, so a random split tests on trees it trained on:

```
ROC AUC 0.903    average precision 0.517    (base rate 0.089)

recall on OSM-mapped trees            75%
predicted canopy landing on roofs   0.66%   <- unambiguous false positives
predicted canopy landing on grass   1.02%   <- the class it was taught to reject
```

Those last two are what make it believable: it is not simply painting anything
green.

Imagery is Sentinel-2 because it is openly licensed and the output can therefore
be published. Google and Bing tiles are higher resolution and would be a licence
breach for exactly the reason recorded above — a canopy layer traced from them is
a derived dataset displayed on a non-Google map.

**Canopy is dappled, and both the router and the map now say so.** A crown is
not a wall. `layered_field` splits each hour's shadow into solid and canopy,
with the canopy half cut out of the solid one so nothing is counted twice, and
`score_edges_layered` weights the canopy contribution by `CANOPY_OPACITY = 0.7`.
Mean shade across the walk network at 13:00 on 13 September:

| | mean shade_fraction |
|---|---|
| buildings only | 0.085 |
| canopy as a wall | 0.431 |
| canopy dappled (shipped) | **0.327** |

The tiles carry the same split as a `kind` property per blob, so the map draws a
crown at 0.7 of a wall's opacity. That is also why you can finally see the trees:
before this, canopy and building shadow were the same flat colour.

Two things to hold against all of it. **The model finds where canopy is, never
how tall** — every polygon still leaves with the flat 8 m constant, tagged
`height_source="canopy_model"`, a guess twice over. And the imagery is **August
2024** against a map that says 2026, in a city planting hard, so it under-reports.

**Model limits:** flat terrain, no awnings or arcades. No DEM, so hills and
their shadows are invisible.

**The tiles have grown.** Canopy roughly tripled them, 59 MB to 151 MB a day.
Still nothing next to a Pages site limit, but it changes the arithmetic on
whatever ends up serving them.

## Attribution

Building and network data © OpenStreetMap contributors, ODbL. Attribution
belongs in the map corner from day one; note that ODbL's share-alike applies if
you ever redistribute the derived scored graph as a database.
