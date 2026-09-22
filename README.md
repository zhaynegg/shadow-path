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
   │  POST /api/day    {origin, destination, date, alpha} — all stamps at once
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
            edge scores are memoised per (date, hour, minute),
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

The weight is read from whichever stamp the walker is actually in when they
reach the edge — see **The sun moves while you walk** below.

`α` is **signed**. Negative α is sun-seeking, and in Astana that is not a
novelty mode — it's the winter product.

The absolute value and the swap are not cosmetic. Written as the single line
`1 + α × (1 − shade_fraction)`, a negative α prices a sunlit street below zero,
and a walker could pace one back and forth forever to drive the total lower —
there is no cheapest path left to find. A* does not detect that; it settles
nodes assuming they can only get dearer, and answers anyway. Moving the penalty
onto the unwanted half instead keeps every weight ≥ length, which also keeps the
straight-line A* heuristic admissible at any α.

### The sun moves while you walk

The router used to price a whole walk at one instant. You asked for 17:40 and
the last kilometre was weighted by 17:40's shadows even though you reach it at
18:25, by which time they had gone. Fine for twenty minutes at midday — a +20
minute step there moves 3.6% of the network — and poor for three quarters of an
hour at dusk, where the same step moves a quarter of it and an hour moves 59%.

So the search carries a clock. The state is `(node, stamp)`, not `node`, and
each edge is priced from the stamp the walker sets off along it in. This is why
`nx.astar_path` is gone: A* labels each node once, which is only valid when
"the cheapest way to node X" is a single fact, and under a moving sun it is not.

Two things make it cheap. **Walking pace does not depend on shade**, so where
you are in the day is a function of how far you have come and nothing else —
there is no feedback loop of the kind traffic has, and no node turns out to be
reachable at more than a stamp or two. And the **straight-line heuristic
survives unchanged**: every weight is at least the edge's own length at every
stamp, so it can never overestimate whichever sun ends up pricing an edge.
(That invariant is also why the weight table is float64 while `day.shade` is
float32 — rounding the product down to float32 puts it under the length by an
ulp on every edge whose multiplier is exactly 1.)

Measured on the 15 km graph, against the same search written the old way:

| walk | one frozen stamp | moving sun | states |
|---|---|---|---|
| ~1.5 km | 0.7 ms | 1.2 ms | 834 → 935 |
| ~3.8 km | 2.0 ms | 3.8 ms | 2,334 → 2,594 |
| ~9 km | 22.1 ms | 26.7 ms | 20,525 → 16,601 |

Dropping networkx from the hot path more than paid for the clock: `/api/route`
went from 0.24 s to **0.02 s** warm, and `/api/day` from 1.3 s to **0.24 s**.
`nx.set_edge_attributes` went with it, and so did the lock that existed only
because two concurrent requests were writing weights onto one shared graph.

**What it buys.** Mostly honesty, and some route. On a 13 September walk across
the centre at α = 6, comparing the old path to the new one, both measured
against the sun as it really moves:

| depart | walk | claimed | actually | new route |
|---|---|---|---|---|
| 08:00 | 9 km | 73.0% | 62.6% | 63.5% |
| 12:00 | 9 km | 53.1% | 56.4% | 56.5% |
| 15:00 | 9 km | 65.7% | 70.4% | 74.5% |
| 17:40 | 9 km | 88.0% | 98.3% | 98.2% |

The gap between *claimed* and *actually* is the bug: up to ten points, and
signed by the time of day — a morning walk was over-claimed because shadows
shrink as the sun climbs, an evening one under-claimed because they grow. The
new route is worth another 0 to 4 points on top, and twice in that table it
trades a fraction of a point of shade for a materially shorter walk (390 m at
08:00, 960 m at 17:40) — which is exactly what α = 6 asks it to do.

**The approximation that is left.** Settling on `(node, stamp)` treats two paths
that arrive in the same stamp as one state even if one walked further to get
there. The error that hides is bounded by how much the shade moves between
neighbouring stamps — which is what the stamps are spaced by, finest at dawn and
dusk. Where it would hurt most, the buckets are narrowest.

The map can only draw one moment, so when a walk runs on past the stamp on
screen the panel says so: *"the far end of it is walked in 11:00's shadows, not
the 10:00 on the map."*

## The decision that shapes everything

Steps 2 and 3 are expensive and time-dependent. Step 1 is expensive and isn't.

**Quantize departure time and cache the scored edges per `(date, hour,
minute)`.** A route request then becomes a graph search over cached weights —
milliseconds. Otherwise you recompute a citywide polygon union per request.

The date belongs in that key, not just the time. 13:00 in June and 13:00 in
December are different suns — mean shade across the walk network is 0.033
against 0.291 — so a time-only cache serves one for the other the first time
the map rolls forward a day.

**How coarsely to quantize is set by the sun, not the clock.** An hour is a fine
step while the sun is high and much too coarse once it is low, because how far a
shadow travels per minute is a function of altitude. Measured on 13 September
across the 10,066-edge walk network:

| from | step | mean shade | edges whose `shade_fraction` moved > 0.1 |
|---|---|---|---|
| 13:00 (41°) | +20 min | 0.335 | 3.6% |
| | +60 min | 0.358 | **16.8%** |
| 17:00 (18°) | +20 min | 0.659 | 24.2% |
| | +60 min | 0.820 | **59.1%** |

An hour at 17:00 redraws well over half the network and moves mean shade by
0.22 — larger than the June/December gap the cache is keyed on the date for.
Somebody leaving at 17:45 was being routed against 17:00's city.

So `daylight_times` in `solar.py` cuts any hour whose sun is below
`LOW_SUN_DEG = 25°` into thirds — `:00`, `:20`, `:40` — and leaves the rest
hourly. That is the whole of it: 24 stamps on 13 September against 13 hourly
ones, 29 in midsummer, and in December all 24, because Astana's winter sun never
clears 25° at all. Uniform 20-minute steps would have cost 39 and 48 for a
midday refinement worth 3.6% — well inside the error already in the height
priors.

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

### Ask the neighbours instead

The prior asks a global question — how tall is a `building=apartments` of this
size, anywhere in Astana? The city answers a local one better. Astana went up as
Soviet-planned microdistricts, in uniform series, so a nine-storey panel block
is usually surrounded by other nine-storey panel blocks — and that is already in
the footprints, with nothing to download.

`scripts/height_neighbours.py` scores it under **spatial** cross-validation:
folds are 1 km blocks of city, not random rows, and neighbours are drawn only
from the training fold. Both matter. Split at random and halves of the same
terrace land on either side, so the model is scored on buildings it has
effectively already seen.

```
                       MAE   within 1   MAE tall   within 1 tall
prior                 0.98      83.9%       5.21           18.2%
neighbour             1.27      77.3%       5.02           24.8%
neighbour by size     0.95      83.2%       4.23           32.2%
prior + by size       0.90      81.3%       4.43           16.9%
```

Read the *tall* columns. **Neighbour-by-size cuts the error on the buildings
that matter by 19% and nearly doubles how often it lands within a storey**, and
it costs nothing but a KD-tree over footprints you already have.

The size restriction is the whole trick. Unrestricted, the nearest buildings to
a panel block are garages, kiosks and transformer huts, and a median over that
mix falls back towards low-rise — the same failure the global prior has, just
measured locally. `neighbour` on its own is barely better than `prior`.

Note that `prior + by size` wins the overall column and loses the tall one. The
overall column is the trap again, so it is not what ships.

**This is what ships**, above the global prior in the chain. On Astana's data it
answers all 29,860 untagged buildings, which means the prior is now never
reached — it stays as the rung beneath in case that ever stops being true. Three
quarters of those buildings get the same answer the prior gave; the other
quarter is where it earns its place, and 1% of them move by 11 storeys or more.
Citywide shadow area at 17:00 on 13 September goes from 64.7 km² to **70.5 km²,
+8.9%** — this is not a cosmetic change.

It is still a guess. Every one of those buildings carries
`height_source="neighbour"`, and a surveyed count still outranks it — which also
means the survey compounds: each building counted through
`scripts/survey_heights.py` improves not only itself but every untagged building
standing near it.

So: **two regimes, explicitly.**

- **Small / simple buildings** — the estimate is fine and near-deterministic.
- **Shadow-relevant** (type in the big list, or footprint >500 m²; 10,550
  buildings, 21.1% of all, 53.6% tagged) — use the tagged value, or a manual
  override, or render as low confidence. Never let an estimate silently invent
  a 12-storey tower.

Every building carries a provenance column so the distinction survives into the
UI:

```
height_m: float
height_source: "tag" | "levels" | "override" | "neighbour" | "prior" | "fallback"
```

Only the first three are measurements. `neighbour` and `prior` are guesses, and
`fallback` is a shrug. With a third of shadow-mass resting on the guesses,
greying out low-confidence shadows is honest rather than decorative.

**A tag is not automatically a measurement.** Four Astana buildings carry
`height=0` and a fifth `building:levels=0`, none with another tag to fall back
on. Parsed as a number, zero wins the chain — it outranks the prior and the
fallback, and is beaten only by a hand survey — so a hotel and three apartment
blocks, up to 1,766 m² of footprint, stood 0 m tall and cast no shadow anywhere
on the map. Nothing errored. They were simply gone, and the provenance column
said `osm_height`, which is to say it said they had been measured.

So `parse_numeric` returns nan for anything at or below zero, and those five
fall through to the prior like any other untagged building. That is not
pretending to know their height; it is declining to treat a data-entry slip as a
survey. The same column also holds `ё` twice and «Многофункциональный комплекс»
once, which never parsed to a number and so were never a problem — the danger
is junk that happens to be numeric.

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
seconds. The counts land in `data/height_overrides.csv` keyed by
`(osm_element, osm_id)`, at the top of the height chain, and belong back in OSM
as well.

Keeping overrides local means the survey isn't blocked on OSM edit cycles.

Doing that from a spreadsheet means copying an id, pasting a URL, waiting for a
map, finding the building and coming back, 2,500 times, so
`scripts/survey_heights.py` serves a page that does the fetching:

```bash
cd backend && uv run python scripts/survey_heights.py    # localhost:8001
```

One building at a time, already framed with its footprint outlined. Type the
count, press enter, and it saves and advances. Four things it does on purpose:

- **Resumes** where the counting stopped, so this is not one long sitting.
- **`s` skips**, into `data/survey_skipped.csv` rather than the overrides — a
  building you cannot read is better left to the prior than given a guess
  wearing an override's rank, and it is not offered again.
- **Corrections replace.** `load_overrides` refuses a file with duplicate keys,
  so appending a fix would break every build after it.
- **Hides the prior** behind `p`. Shown by default it is an anchor, and a
  surveyor who has just read "10" is measurably likelier to count ten.

Imagery is **Esri World Imagery**, which is licensed for tracing into OSM.
That is not incidental — Google and Bing are ruled out for the same reason
recorded above, and it is the whole reason these counts can go back to OSM at
all. Mapillary would be better for counting storeys, but Astana's coverage is
arterials and the centre: the outer residential districts, where the untagged
buildings are, have almost none.

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
  "minute":      20,
  "alpha":       6.0
}
→
{
  "route":    { "distance_m": 2550, "duration_s": 1889,
                "shade_fraction": 0.37, "geometry": <LineString> },
  "baseline": { "distance_m": 1990, "duration_s": 1474,
                "shade_fraction": 0.00, "geometry": <LineString> }
}

GET /api/health
```

```
POST /api/day          the same walk at every stamp of one day
{
  "origin":      [lat, lon],
  "destination": [lat, lon],
  "date":        "2026-09-12",
  "alpha":       6.0
}
→
{
  "baseline_distance_m": 3831,
  "baseline_duration_s": 2838,
  "departures": [
    { "time": "06:00", "distance_m": 3961, "duration_s": 2934,
      "shade_fraction": 0.97, "baseline_shade_fraction": 0.94 },
    ...one row per daylight stamp, ascending
  ]
}
```

`/api/day` is "when should I leave?", and it is the question a shadow map is
uniquely able to answer. No stamp goes in — asking about all of them *is* the
question — and no geometry comes back: the answer is a time, and once the reader
picks one the map asks `/api/route` for that stamp the way it always did.

It is close to free, but only because of `core/scores.py`. The nightly export
already scores the whole graph for every stamp, so a scan is a parquet read and
an A* per hour rather than twenty-four unions of the city. Two things had to
move for that to be true:

- **Snapping and the edge frame are built once, not per call.** They depend on
  the graph alone, not on the sun. `nearest_node` was 0.36s of a 0.45s route
  against A*'s 9ms, and `graph_to_gdfs` another 0.6s per cache miss — naively,
  a day was ~21s of setup and 0.4s of searching. `/api/route` got the same
  speedup for free: 0.9s → 0.24s.
- **The direct route is searched once and priced twenty-four times.** At α = 0
  the weight is the edge's own length, and a length does not depend on where the
  sun is, so it is one path all day. What changes is how much of it happens to
  be in shade.

Measured on the 15 km graph: 134 ms a stamp, 4.6s for a cold September day and
1.3s once `scored_edges` holds it.

The scan declines with a 503 rather than falling back for a date with no
precomputed scores. One missing stamp is a cache miss the API absorbs by
computing the field itself; twenty-four of them is twelve minutes of one request
holding the process, which is not a slow answer but an outage a caller can cause.

Both curves come back because only the pair says anything. A shade curve alone
peaks at dusk on every walk in the city — true, and a fact about the sun rather
than about the route. Read against the direct path it says where detouring buys
something, which is usually a different hour: on the walk above, 18:20 is 99%
shaded and the direct route is 98% shaded, while 15:00 is 71% against 46%.

`date` is sent by the client, not read from the server's clock, and so are
`hour` and `minute`. All three come from the tile manifest, so the router
weights streets by the same sun the map drew: if a nightly rebuild fails, both
stay a day behind together instead of quietly disagreeing. It is bounded to within a year of today — every distinct date is a
fresh shadow field over the routing footprints, and an unbounded range is an
unbounded amount of work a caller can ask for. `minute` is bounded for the same
reason and more tightly — it must be one of `0`, `20`, `40`, since anything else
is a scored graph built for a sun no tileset was ever drawn for.

Routes reach MapLibre as GeoJSON. Shadows do not, and deliberately so: within a
day the sun repeats, so a stamp's shadow field never changes once it is built.
`scripts/export_shadow_tiles.py` builds each one into its own vector tileset
ahead of time, named `HHMM.pmtiles`, and the browser reads them the way it reads
roads.

How many tilesets exist is a property of the date, not a constant — 29 in June,
24 in mid-September, 24 at the winter solstice. Those are close together while
the daylight behind them is not: June has 16 hours of sun and December 8. The
split above is what evens them out, cutting December's few hours into thirds all
day and leaving June's long high middle hourly. The script writes what it
produced to `shadows/index.json`: the date, the times, the layer name. The
frontend builds its sources from that manifest, because a daylight window and a
step size hardcoded on the other side are right for exactly one date, and asking
for `0500.pmtiles` in December is a 404.

The map holds one source and one layer per stamp, and the time slider only
changes which layer is visible. The slider's stops come from the manifest too,
so it moves in twenty-minute steps at dawn and dusk and hourly in between —
finer exactly where the picture changes faster. Nothing is computed on demand, so shadows are
already drawn wherever you pan and at every zoom, city-wide. Opening every
tileset costs a few hundred KB, because pmtiles is read by byte range — only the
tiles actually on screen are ever fetched.

### How long it takes

Every leg carries `duration_s` beside its `distance_m`, because "10% longer" is
a ratio a reader has to convert before it means anything and "three minutes" is
the thing they are actually deciding about. The panel leads with the minutes and
keeps the distance behind them, and turns the departure stamp into an arrival
clock: *set off at 10:00 and you are there by 10:20.*

It is `distance / WALK_SPEED_MS`, computed in `measure()` — the one function
every leg on both endpoints passes through, so the two can never quote different
paces. 1.35 m/s is 4.9 km/h, the ordinary adult pace on the flat, and Astana is
built on steppe so there is no slope model to want.

One number for everybody, and it is the optimistic one. Four months of ice, the
heat this app exists because of, a pram, a crossing, or being 70 all cost more
than it admits. Read the minutes as the length of the walk rather than as a
promise about the clock.

The pace is what turns distance into time of day for the search as well as
minutes for the panel, which only works because it does not depend on shade —
see **The sun moves while you walk** above.

Arrival is a clock, not a date: it wraps at midnight, because everything on this
map belongs to one day.

## Layout

```
backend/
  scripts/
    height_coverage.py       measure OSM height coverage + prior accuracy
    height_neighbours.py     do the neighbours predict height? (spatial CV)
    export_survey_queue.py   emit the buildings worth surveying, by priority
    survey_heights.py        count storeys off imagery, one building at a time
    export_fixtures.py       write shadow GeoJSON fixtures for the frontend
    export_shadow_tiles.py   a .pmtiles layer per daylight hour, + index.json
                             and the routing scores cut from the same field
    fetch_trees.py           cache OSM tree rows and points
    detect_trees.py          find canopy in Sentinel-2 (needs --group ml)
  src/backend/
    main.py                  FastAPI app: /api/route, /api/day, /api/health
    config.py                paths, radius, timezone, city centre, today()
    core/
      graph.py               OSM walk network load + cache
      buildings.py           footprints + height chain + provenance
      solar.py               sun angles, and which stamps a date gets tiles for
      shadows.py             footprints + sun → unioned, indexed polygons
      trees.py               canopy polygons + the leaf-on season
      scoring.py             edge sub-segmentation + shade fraction
      scores.py              last night's shade per edge, so routing has
                             no geometry left to do at request time
      day.py                 one date's stamps stacked, plus night, plus
                             the clock that says which one a minute is in
      search.py              the graph as flat arrays, and an A* over
                             (node, stamp) -- it knows time, not sun
      routing.py             what a metre costs a walker with a preference,
                             how fast they get through it, and the day scan
  tests/
    test_scoring.py, test_routing.py, test_api.py, test_buildings.py,
    test_scores.py, test_day.py

frontend/src/
  api/client.ts              typed fetch; mirrors main.py and the tile manifest
  lib/
    stamps.ts                which stamps exist, and what time it is in the city
    departures.ts            the day scan's chart maths and its recommendation
    locate.ts                the device's own position, and whether the graph
                             reaches it -- REACH_M mirrors GRAPH_RADIUS
    footprint.ts, format.ts
  components/
    MapView.tsx              map, shadow + building layers, routes, the clock
    RouteSummary.tsx         the two routes, and the difference between them
    DeparturePlanner.tsx     shade against departure time, and when to leave
    controls/{TimeSlider,ShadeSlider,SearchBox,LocateButton}.tsx

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

Shadow tiles (needs `brew install tippecanoe`). About 40 s per stamp and
roughly flat across the day, so a mid-September date is 24 of them and a
quarter of an hour.
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

**Dependencies still to add:** `pydantic-settings`. `pytest`, `ruff` and
`httpx` are in the dev group, `rasterio` and `scikit-learn` in an `ml` group the
API never installs, and `pyarrow` and `scipy` are in the main one — `scipy` for
the KD-tree behind the neighbour height estimate, which both the API and the
tile export reach through `load_buildings`.
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

The tiles carry the same split as a `kind` property per blob, and the map reads
it on two channels: hue for which kind of shade it is, opacity for how much.
Canopy is green at 0.7 of a wall's opacity, so the picture still never overstates
the shade the router weights by. Opacity alone was not enough. A crown at 0.21
against a wall at 0.3, both in the same violet-grey, is a difference of nine
parts in an alpha channel — trees read as buildings, and the split the tiles
went to the trouble of carrying was invisible.

**Draw the buildings after the shadows, not before.** `cast_shadow` unions a
footprint with its translated copy, so every blob contains the building that
threw it. Draw the field over the basemap and every building in the city is
filled with shadow colour by its own shadow — not a palette problem, since a
building and a shadow are then the same pixels, and no choice of colours
separates them. It is also the wrong claim: this is a ground-plane model, and
shade on a roof is not somewhere anybody walks. So the buildings layer is lifted
out of the basemap and redrawn above the shadows, warm and lighter than the
ground, because everything shaded here is cool and darker — which separates a
building from a shadow at the zooms where a footprint is only a few pixels wide
and its shape is no help.

Two things to hold against all of it. **The model finds where canopy is, never
how tall** — every polygon still leaves with the flat 8 m constant, tagged
`height_source="canopy_model"`, a guess twice over. And the imagery is **August
2024** against a map that says 2026, in a city planting hard, so it under-reports.

**Model limits:** flat terrain, no awnings or arcades. No DEM, so hills and
their shadows are invisible.

**The tiles have grown, twice.** Canopy roughly tripled them, 59 MB to 151 MB a
day. Cutting the low-sun hours into thirds took mid-September from 13 tilesets
to 24, and **144 MB to 269.5 MB**. Still nothing next to a Pages site limit, and
the browser only byte-ranges what is on screen, but it changes the arithmetic on
whatever ends up serving them — and it is why the split follows the sun instead
of the clock. Uniform `:00/:20/:40` would have been 39 tilesets and about 430 MB
for a midday refinement worth 3.6% of edges.

## Attribution

Building and network data © OpenStreetMap contributors, ODbL. Attribution
belongs in the map corner from day one; note that ODbL's share-alike applies if
you ever redistribute the derived scored graph as a database.
