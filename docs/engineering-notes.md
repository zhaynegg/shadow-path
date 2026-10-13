# Engineering notes

This document explains the implementation behind [Shadow Path](../README.md), including the approximations and deployment requirements. It describes the current code rather than treating earlier experiments as production benchmarks.

## Offline geometry and runtime routing

The two processes have different workloads:

1. **Data preparation** resolves heights, calculates solar position, projects shadows, intersects them with street geometry, and exports tiles and edge scores.
2. **The API** loads the street tables and precomputed scores, chooses a routing preference, searches the network, and returns a route comparison.

The API does not download OSM data or calculate city-wide shadow geometry during a request. Missing runtime data is an explicit error rather than a trigger for an expensive rebuild.

[`export_shadow_tiles.py`](../backend/scripts/export_shadow_tiles.py) writes two representations of each shadow field:

| Output | Reader | Purpose |
| --- | --- | --- |
| `frontend/public/shadows/HHMM.pmtiles` | MapLibre and PMTiles | Draw the shadow geometry visible in the viewport |
| `frontend/public/shadows/index.json` | Frontend | Identify the dataset's date, available times, and tile layer |
| `frontend/public/tree-canopy.pmtiles` | MapLibre and PMTiles | Draw permanent crown footprints independently of departure time and season |
| `data/cache/scored/<radius>/<date>/…` | FastAPI | Look up each street edge's shade fraction |

The scores use the committed street tables' `(u, v, key)` edge index. A new graph snapshot can have different edges even when it describes the same city, so file existence alone does not prove compatibility. [`validate_runtime_assets.py`](../backend/scripts/validate_runtime_assets.py) checks the expected daylight stamps, index coverage, and shade fractions before deployment.

## Shadow geometry and scoring

Geometry is projected to the local metric coordinate system before measuring lengths or translating footprints. Solar altitude and azimuth come from PySolar.

For an object of height `h`, the projected shadow length is:

```text
shadow_length = min(h / tan(solar_altitude), 300 metres)
```

At low solar altitude this expression grows rapidly. The 300 m cap bounds the geometry and is a modelling compromise, particularly in winter. No unbounded shadow polygon is generated when the sun is below the horizon.

[`cast_shadow`](../backend/src/backend/core/shadows.py) sweeps the footprint away from the sun: it unions the original footprint, its translated copy, and the bands swept by the footprint's edges. Using a sweep rather than a convex hull avoids unnecessarily filling courtyards and concave outlines.

Overlapping building shadows are dissolved. Tree shadows are kept as a separate layer, with areas already covered by solid shadows removed. [`score_edges`](../backend/src/backend/core/scoring.py) uses a spatial join and vectorised intersections to measure the shaded portion of each original street edge:

```text
shade_fraction =
    (solid_shaded_length + 0.7 × canopy_shaded_length) / edge_length
```

The canopy factor models partial transmission through foliage. The map uses the same relative factor for canopy shadow opacity; permanent crown footprints have separate visual styling. It is a product assumption, not a measured transmission value for every tree.

Buildings are rendered above the ground shadows. A shadow polygon contains its casting footprint, but a shaded roof is not pavement that a pedestrian can use.

## Height estimates and canopy data

[`buildings.py`](../backend/src/backend/core/buildings.py) resolves a building's height in this order:

1. A manual override from `data/height_overrides.csv`.
2. A valid OSM `height` tag.
3. A valid `building:levels` value, multiplied by 3.2 m per storey.
4. A median estimate from nearby buildings in the same footprint-size class.
5. A building-type and size prior.
6. A one-storey fallback.

The loader records `height_source`, so estimated and tagged values remain distinguishable in the data. The current interface does not display per-building confidence. Non-positive and malformed numeric tags fall through the chain rather than silently making a building disappear from the shadow model.

[`height_neighbours.py`](../backend/scripts/height_neighbours.py) evaluates estimates using spatial blocks. A random row split can put neighbouring buildings from the same development on both sides of the split and overstate the model's ability to generalise. The analysis also separates tall buildings from the overall population: a low average error dominated by small buildings can hide large errors on the structures that cast the longest shadows.

Canopy preparation combines mapped OSM trees, Meta/WRI canopy-height data, and Sentinel-2-derived coverage. The measured canopy-height cache is preferred when present; model-derived coverage fills gaps. [`detect_trees.py`](../backend/scripts/detect_trees.py) uses a scikit-learn classifier and spatial validation, while [`fetch_canopy_height.py`](../backend/scripts/fetch_canopy_height.py) prepares the higher-resolution height data.

[`trees.py`](../backend/src/backend/core/trees.py) models foliage for shadows and routing from **1 May through 31 October**, inclusive. Tree shade is excluded from **1 November through 30 April**, while the crown footprints remain visible year-round. This fixed season and fallback heights are approximations; they do not capture individual species, pruning, or daily changes in foliage.

The map draws crown footprints independently of that season and of the departure time. [`export_tree_tiles.py`](../backend/scripts/export_tree_tiles.py) exports the same cached canopy geometry to the committed `frontend/public/tree-canopy.pmtiles` asset, without applying the leaf-on rule or projecting shadows. The permanent layer is drawn above ground shadows and below buildings, routes, and place labels. Rebuild it when the geometry caches change; the normal frontend deployment bundles it without waiting for a daily shadow export.

## Cost function and the shortest-path baseline

The server chooses an internal signed preference `alpha`. Positive values seek shade, negative values seek sun, and zero gives the shortest path. Clients cannot supply this value.

```text
unwanted = 1 - shade_fraction    when seeking shade
unwanted = shade_fraction        when seeking sun

cost = length × (1 + abs(alpha) × unwanted)
```

Every edge costs at least its stored length. Seeking sun cannot create negative edges or reward repeatedly walking around a cycle. The weights are calculated in float64, even though stored shade values use float32, to retain that cost bound.

The search uses projected straight-line distance as its heuristic. Stored OSM edge lengths and projected coordinate distances are not identical, so the cost bound alone does not prove strict heuristic admissibility on this dataset. Establishing a consistent metric or a conservative heuristic bound is a further correctness check, alongside the time-bucket approximation below.

The shortest path is calculated independently. During a day comparison its geometry is searched once, then its exposure is measured at each departure time. A large shade percentage alone can be misleading: near dusk, the shortest path may already be almost entirely shaded, leaving little benefit from a detour.

The seasonal and weather policy lives in [`preference.py`](../backend/src/backend/core/preference.py); its thresholds and fallback behaviour are documented in the [API README](../backend/README.md).

## Time-aware search and its approximation

[`search.py`](../backend/src/backend/core/search.py) implements A* over `(node, stamp)` states. The accumulated distance determines arrival time using the fixed walking pace, and that time selects the score row for the next edge.

[`solar.py`](../backend/src/backend/core/solar.py) chooses 20-minute intervals when the sun is below 25° at the start of an hour and hourly intervals otherwise, then keeps only daylight stamps. The extra resolution is concentrated where shadows move fastest.

This is not exact continuous-time routing. Two paths reaching the same node within one stamp are treated as one state even if their arrival times differ. An edge is also scored using the stamp at entry rather than continuously along its traversal. These choices bound the search state while retaining the main effect of a changing sun.

[`day.py`](../backend/src/backend/core/day.py) stores score rows and maps minutes to stamps, including a nighttime row. [`routing.py`](../backend/src/backend/core/routing.py) measures the route's exposure using the stamps encountered during the walk. The map displays one selected instant; the route can span several.

With a complete daily score bundle, routing follows the changing shadow table. If only the selected departure's score file is available, `/api/route` can use a fixed-stamp fallback. `/api/day` requires the complete daily bundle. If the required scores are unavailable, the API returns HTTP 503.

## Runtime memory and request handling

The OSMnx/NetworkX graph is useful for preparation, but the request path needs adjacency, lengths, node coordinates, and the shapes of the chosen route.

[`streets.py`](../backend/src/backend/core/streets.py) stores those inputs as Parquet tables and a NumPy coordinate buffer with offsets. The search uses compact arrays. Only the selected edges are converted into route geometry, avoiding a persistent geometry object for every street edge.

Street data is cached per process. A second Uvicorn worker would hold another copy, so the deployment uses one worker. Runtime score caches and accepted date ranges are bounded. Previous local latency and memory measurements are not presented as current guarantees; a reproducible benchmark suite remains useful future work.

Input points are snapped to the nearest mapped node, with a maximum snap distance of 300 m. Identical snapped endpoints are rejected. The API validates coordinate structure, dates, supported minute values, and unknown fields before routing; its input models do not currently enforce latitude/longitude ranges or finite coordinate values.

The frontend tags asynchronous answers with the endpoint and departure values they belong to and aborts obsolete requests. A slow answer for a previous pair of points must not replace the route currently requested by the user.

Weather is fetched on the server with a 3-second socket timeout. Successful forecasts are cached for 30 minutes, and failed requests for 60 seconds. The response identifies seasonal fallbacks, so an unavailable forecast is not shown as live weather. Forecasts affect route preference; they do not alter the geometric shadow polygons.

## Deployment runbook

The deployment definition is [`render.yaml`](../render.yaml). The nightly pipeline is [`.github/workflows/shadow-tiles.yml`](../.github/workflows/shadow-tiles.yml).

For an initial deployment:

1. Run **Shadow tiles** to generate and validate a dataset and publish `tiles-latest`.
2. Create the Render services using `render.yaml`.
3. Set the GitHub Actions secrets `RENDER_DEPLOY_HOOK_API` and `RENDER_DEPLOY_HOOK_WEB` to the corresponding Render deploy hooks.
4. Verify the frontend's `/shadows/index.json` date, the API's downloaded score date, and an actual route.

The static build downloads `shadows.tar.gz`; the API build downloads `scored.tar.gz` and validates it against the tracked street network. Download or validation failures stop the build. A workflow publishing a new release does not update Render unless the deploy hooks are configured.

PMTiles depends on HTTP range requests. The blueprint serves the archives as `application/octet-stream` to avoid CDN compression interfering with byte ranges, and uses `no-cache` for the date manifest.

For an incompatible score bundle, rebuild the release from the checkout containing the intended street tables and redeploy both services. Redeploying only one can leave the map and API referring to different dates. When updating local data, remove or move aside older score dates if using automatic date inference in the validation script, or validate an explicit date with `--date YYYY-MM-DD`.

`/api/health` reports process health; it is not a readiness check for the data. A successful build, a valid manifest, and a real route are separate checks. For frontend map changes, verify the production preview as well as the development server, since MapLibre's worker is bundled separately.

## Testing scope

The [fast CI workflow](../.github/workflows/ci.yml) runs backend lint and pytest, plus frontend lint, Vitest, and a TypeScript/Vite production build. Unit tests use small geometry and graph fixtures; API tests replace expensive data operations.

Some canopy preparation tests run only when the optional `ml` dependencies are installed. The full tile build runs in its separate workflow. Fast CI therefore does not establish field accuracy, exercise a real city route, or prove the deployed browser and API work together. A small reproducible end-to-end dataset would help close that gap.
