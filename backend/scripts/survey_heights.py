"""Count storeys off imagery, one queued building at a time.

    uv run python scripts/survey_heights.py        # then open localhost:8001

Not part of the product. This exists because `data/height_overrides.csv` is the
highest-value empty file in the repo -- the prior is off by 5.10 storeys on the
buildings that cast every shadow worth routing around -- and filling it by hand
from a spreadsheet means copying an id, pasting a URL, waiting for a map,
finding the building, and coming back, about 2,500 times.

Here the building is already on screen with its footprint outlined. You type the
number and it saves and advances. Same work, without the parts that are not
work.

Imagery is Esri World Imagery, which is licensed for tracing into OSM -- which
matters because the point of this file is to end up contributing the counts back
there. Google and Bing are ruled out for the reason recorded in the README.
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path

import geopandas as gpd
import pandas as pd
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.config import CACHE_DIR, DATA_DIR, HEIGHT_OVERRIDES
from backend.core.buildings import LEVEL_HEIGHT, load_buildings

QUEUE = DATA_DIR / "survey_queue.csv"

# Buildings looked at and deliberately not answered. Their own file, so that
# height_overrides.csv holds only measurements -- the pipeline reads that one,
# and a row meaning "I could not tell" does not belong in something whose values
# outrank every other source of height.
SKIPPED = DATA_DIR / "survey_skipped.csv"

COLUMNS = ["osm_element", "osm_id", "levels", "source"]

app = FastAPI()


class Answer(BaseModel):
    osm_element: str
    osm_id: int
    # None is a skip, which is a real answer: a building you cannot read is
    # better left to the prior than given a guess wearing an override's rank.
    levels: int | None = Field(None, ge=1, le=80)


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(path)


def without(frame: pd.DataFrame, element: str, osm_id: int) -> pd.DataFrame:
    if not len(frame):
        return frame
    key = (frame["osm_element"] == element) & (frame["osm_id"] == osm_id)
    return frame[~key]


def write_answer(answer: Answer) -> dict:
    """Record one answer, replacing any earlier one for the same building.

    Read-modify-write rather than append. Appending would let a correction sit
    as a second row for the same key, and `load_overrides` refuses a file with
    duplicates -- so one fixed typo would break every build after it.
    """
    counted = answer.levels is not None
    path = HEIGHT_OVERRIDES if counted else SKIPPED
    other = SKIPPED if counted else HEIGHT_OVERRIDES

    frame = without(read_csv(path), answer.osm_element, answer.osm_id)
    row = {"osm_element": answer.osm_element, "osm_id": answer.osm_id,
           "levels": answer.levels, "source": "survey"}
    pd.concat([frame, pd.DataFrame([row])], ignore_index=True).to_csv(path, index=False)

    # Answering something previously skipped, or skipping something previously
    # answered, has to clear the other file too -- or it comes back on resume.
    kin = read_csv(other)
    pruned = without(kin, answer.osm_element, answer.osm_id)
    if len(pruned) != len(kin):
        pruned.to_csv(other, index=False)

    return {"counted": len(read_csv(HEIGHT_OVERRIDES)), "skipped": len(read_csv(SKIPPED))}


def build_payload() -> dict:
    """The queue, with each building's footprint and whatever is already known."""
    queue = pd.read_csv(QUEUE)
    footprints = gpd.read_parquet(CACHE_DIR / "astana_buildings.parquet")
    footprints = footprints[["element", "id", "geometry", "name"]].to_crs(4326)

    merged = queue.merge(
        footprints, left_on=["osm_element", "osm_id"], right_on=["element", "id"], how="left")
    merged = gpd.GeoDataFrame(merged, geometry="geometry", crs=4326)
    missing = int(merged.geometry.isna().sum())
    if missing:
        print(f"  {missing} queued buildings are not in the footprint cache, dropped")
        merged = merged[merged.geometry.notna()]

    # What the pipeline would guess without a survey. Sent, but hidden behind a
    # keypress in the page: shown by default it is an anchor, and a surveyor who
    # has just read "10" is measurably more likely to count ten.
    priors = load_buildings(CACHE_DIR).set_index(["element", "id"])["height_m"] / LEVEL_HEIGHT

    answered = {(r.osm_element, r.osm_id): r.levels for r in read_csv(HEIGHT_OVERRIDES).itertuples()}
    passed = {(r.osm_element, r.osm_id) for r in read_csv(SKIPPED).itertuples()}

    geojson = json.loads(merged.to_json())
    items = []
    for row, feature in zip(merged.itertuples(), geojson["features"]):
        key = (row.osm_element, row.osm_id)
        items.append({
            "osm_element": row.osm_element,
            "osm_id": int(row.osm_id),
            "building": row.building,
            "name": None if pd.isna(row.name) else str(row.name),
            "area_m2": float(row.area_m2),
            "shadow_mass": float(row.shadow_mass),
            "geometry": feature["geometry"],
            "prior_levels": None if key not in priors.index else round(float(priors.loc[key])),
            "answered": None if key not in answered else int(answered[key]),
            "skipped": key in passed,
        })

    return {"items": items, "total_mass": sum(i["shadow_mass"] for i in items)}


@app.get("/api/queue")
def queue() -> JSONResponse:
    return JSONResponse(build_payload())


@app.post("/api/answer")
def answer(body: Answer) -> dict:
    return write_answer(body)


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return PAGE


PAGE = """
<!doctype html>
<meta charset="utf-8">
<title>storey survey</title>
<link href="https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/4.7.1/maplibre-gl.css" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/maplibre-gl/4.7.1/maplibre-gl.js"></script>
<style>
  :root { --ink:#1a1a1a; --dim:#6b7280; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.45 system-ui,-apple-system,sans-serif; color:var(--ink);
         height:100vh; display:flex; flex-direction:column; }
  #map { flex:1; }
  #bar { padding:10px 14px; border-top:1px solid #e5e7eb; background:#fff;
         display:flex; gap:18px; align-items:center; flex-wrap:wrap; }
  #entry { font-size:34px; font-weight:600; min-width:74px; letter-spacing:1px;
           font-variant-numeric:tabular-nums; }
  #entry.empty { color:#d1d5db; }
  .dim { color:var(--dim); }
  .grow { flex:1; }
  kbd { background:#f3f4f6; border:1px solid #d1d5db; border-bottom-width:2px;
        border-radius:4px; padding:1px 5px; font:12px ui-monospace,monospace; }
  #prior { visibility:hidden; }
  #prior.show { visibility:visible; }
  .done { color:#15803d; font-weight:600; }
</style>
<div id="map"></div>
<div id="bar">
  <div id="entry" class="empty">&mdash;</div>
  <div>
    <div id="what"><b>loading&hellip;</b></div>
    <div class="dim" id="where"></div>
  </div>
  <div class="grow"></div>
  <div class="dim" id="prior"></div>
  <div id="progress" class="dim"></div>
  <div class="dim">
    <kbd>0-9</kbd> <kbd>enter</kbd> &middot; <kbd>s</kbd> skip &middot;
    <kbd>&larr;</kbd> back &middot; <kbd>p</kbd> prior &middot; <kbd>o</kbd> OSM
  </div>
</div>
<script>
const EMPTY = { type: 'FeatureCollection', features: [] }
let items = [], at = 0, buffer = '', totalMass = 0

const map = new maplibregl.Map({
  container: 'map',
  style: {
    version: 8,
    sources: {
      esri: {
        type: 'raster',
        // Licensed for tracing into OSM, which is where these counts are going.
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        tileSize: 256, maxzoom: 19,
        attribution: 'Esri, Maxar, Earthstar Geographics and the GIS User Community',
      },
      target: { type: 'geojson', data: EMPTY },
    },
    layers: [
      { id: 'esri', type: 'raster', source: 'esri' },
      { id: 'target-fill', type: 'fill', source: 'target',
        paint: { 'fill-color': '#ffd400', 'fill-opacity': 0.08 } },
      // A dark casing under a bright line, because the background is whatever
      // the imagery happens to be -- yellow alone vanishes over dry grass and
      // pale roofs, which is most of Astana from above.
      { id: 'target-casing', type: 'line', source: 'target',
        paint: { 'line-color': '#000', 'line-width': 6, 'line-opacity': 0.55,
                 'line-blur': 1 } },
      { id: 'target-line', type: 'line', source: 'target',
        paint: { 'line-color': '#ffd400', 'line-width': 2.5 } },
    ],
  },
  center: [71.47, 51.16], zoom: 17,
})
map.addControl(new maplibregl.NavigationControl(), 'top-right')

const el = id => document.getElementById(id)
const corners = geometry => {
  const depth = geometry.type === 'MultiPolygon' ? 2 : 1
  return geometry.coordinates.flat(depth).reduce((box, c) => [
    Math.min(box[0], c[0]), Math.min(box[1], c[1]),
    Math.max(box[2], c[0]), Math.max(box[3], c[1])
  ], [Infinity, Infinity, -Infinity, -Infinity])
}

function show() {
  const item = items[at]
  if (!item) { el('what').innerHTML = '<b>queue finished</b>'; return }
  frame(item)

  const counted = items.filter(i => i.answered != null)
  const massDone = counted.reduce((s, i) => s + i.shadow_mass, 0)
  el('what').innerHTML = `<b>${item.building || 'building'}</b>`
    + (item.name ? ` &middot; ${item.name}` : '')
    + ` <span class="dim">&middot; ${Math.round(item.area_m2).toLocaleString()} m&sup2;</span>`
    + (item.answered != null ? ` <span class="done">&middot; recorded ${item.answered}</span>` : '')
    + (item.skipped ? ' <span class="dim">&middot; skipped</span>' : '')
  el('where').textContent = `#${at + 1} of ${items.length}`
  el('progress').innerHTML = `<b>${counted.length}</b> counted, `
    + `${items.filter(i => i.skipped).length} skipped &middot; `
    + `<b>${(100 * massDone / totalMass).toFixed(1)}%</b> of queued shadow mass`
  el('prior').textContent = item.prior_levels == null
    ? 'no prior' : `prior says ${item.prior_levels}`
  el('prior').classList.remove('show')
  buffer = ''
  paint()
}

function frame(item) {
  // A backgrounded tab pauses maplibre and never fires its load event, so the
  // text above must not wait on the map -- otherwise a page opened behind
  // another tab reads "loading..." until somebody looks at it.
  if (!map.getSource('target')) { map.once('load', () => frame(items[at])); return }
  map.getSource('target').setData({ type: 'Feature', geometry: item.geometry, properties: {} })

  // Generous padding on purpose: a footprint filling the frame is hard to read,
  // because the cues are the balcony rows and the shadow on the ground beside it.
  const b = corners(item.geometry)
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 150, duration: 0, maxZoom: 19 })
}

function paint() {
  el('entry').innerHTML = buffer || '&mdash;'
  el('entry').classList.toggle('empty', !buffer)
}

async function send(levels) {
  const item = items[at]
  await fetch('/api/answer', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ osm_element: item.osm_element, osm_id: item.osm_id, levels }),
  })
  if (levels == null) { item.skipped = true; item.answered = null }
  else { item.answered = levels; item.skipped = false }
  at = Math.min(at + 1, items.length - 1)
  show()
}

addEventListener('keydown', event => {
  const k = event.key
  if (k >= '0' && k <= '9') { buffer = (buffer + k).slice(0, 2); paint(); return }
  if (k === 'Backspace') { buffer = buffer.slice(0, -1); paint(); return }
  if (k === 'Enter') {
    const n = parseInt(buffer, 10)
    // Guard both ends: 0 storeys is the exact bug this survey corrects, and
    // nothing in Astana is 80 floors.
    if (n >= 1 && n <= 80) send(n)
    buffer = ''; paint(); return
  }
  if (k === 's') { send(null); return }
  if (k === 'p') { el('prior').classList.toggle('show'); return }
  if (k === 'ArrowLeft') { at = Math.max(0, at - 1); show(); return }
  if (k === 'ArrowRight') { at = Math.min(items.length - 1, at + 1); show(); return }
  if (k === 'o') {
    const item = items[at]
    open(`https://www.openstreetmap.org/${item.osm_element}/${item.osm_id}`, '_blank')
  }
})

fetch('/api/queue').then(r => r.json()).then(data => {
  items = data.items
  totalMass = data.total_mass
  // Resume where the counting stopped, not at the top of the queue.
  const next = items.findIndex(i => i.answered == null && !i.skipped)
  at = next === -1 ? 0 : next
  show()
})
</script>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    payload = build_payload()
    counted = sum(1 for i in payload["items"] if i["answered"] is not None)
    print(f"{len(payload['items']):,} queued, {counted:,} already counted")
    print(f"counts -> {HEIGHT_OVERRIDES}")
    print(f"skips  -> {SKIPPED}")
    print(f"\nhttp://127.0.0.1:{args.port}/")
    if not args.no_open:
        webbrowser.open(f"http://127.0.0.1:{args.port}/")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
