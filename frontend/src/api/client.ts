// Typed edge between this app and the Python that feeds it. Mirrors
// RouteRequest in backend/main.py and the manifest written by
// scripts/export_shadow_tiles.py -- when either changes, this file changes
// with it.

export type LatLon = [number, number]

// What scripts/export_shadow_tiles.py wrote beside the tiles. Which stamps have
// a tileset is a property of the date twice over -- daylight runs 16 hours in
// June and 8 in December, and how finely each hour is cut depends on how high
// the sun gets, since a low sun moves a shadow across a street inside the hour.
// So the map reads the answer rather than assuming a window and a step that are
// only ever right for one season. "HH:MM", sorted ascending; drop the colon and
// it is the tileset filename.
export type ShadowManifest = {
    date: string
    times: string[]
    layer: string
    generated_at: string
}

// A static file next to the tiles, not an endpoint: same origin either way, and
// it ships with whatever built the tiles, so the two can never disagree.
export async function fetchShadowManifest(signal?: AbortSignal): Promise<ShadowManifest> {
    const response = await fetch('/shadows/index.json', { signal })

    // A dev server answers an unknown path with index.html and a cheerful 200,
    // so a missing manifest arrives as HTML rather than as a 404. Checking the
    // status alone lets that reach JSON.parse, which then fails with a message
    // about an unexpected '<' -- true, and no help at all to whoever forgot to
    // build the tiles.
    const isJson = response.headers.get('content-type')?.includes('application/json')
    if (!response.ok || !isJson) {
        throw new Error(`no shadow manifest at /shadows/index.json (${response.status})`
            + ' -- run backend/scripts/export_shadow_tiles.py')
    }
    return response.json() as Promise<ShadowManifest>
}

export type LineGeometry = {
    type: 'LineString'
    coordinates: [number, number][]
}

export type Leg = {
    distance_m: number
    shade_fraction: number
    geometry: LineGeometry
}

// What POST /api/route returns: the route the user asked for, plus the plain
// shortest path for comparison. The comparison is the product.
export type RoutePlan = {
    route: Leg
    baseline: Leg
}

export type RouteRequest = {
    origin: LatLon
    destination: LatLon
    // The date the map is showing, straight from the manifest. The backend
    // weights streets by the same sun the tiles were drawn for rather than
    // guessing from its own clock -- a failed rebuild would put them a day
    // apart, and the route would avoid shade that is not on screen.
    date: string
    hour: number
    // Minutes past the hour, and only ever 0, 20 or 40 -- the steps a low-sun
    // hour is cut into. The backend rejects anything else, because every
    // distinct stamp is a scored graph of its own.
    minute: number
    alpha: number
}

// One POST, one JSON answer, one way of reading a failure. Both endpoints take
// the same shaped body and fail the same way, so the error handling lives here
// rather than once per caller -- it is the part that is easy to get subtly
// wrong and never notice, because a mishandled error still shows *something*.
async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal,
    })

    if (!response.ok) {
        // FastAPI puts the reason in `detail` -- a plain string for the errors we
        // raise ourselves, a list of field problems for validation failures.
        const text = await response.text()
        let message = text.slice(0, 200)
        try {
            const detail = JSON.parse(text).detail
            if (typeof detail === 'string') message = detail
        } catch {
            // Not JSON. Keep the raw text.
        }
        throw new Error(message)
    }

    return response.json() as Promise<T>
}

export async function fetchRoute(request: RouteRequest, signal?: AbortSignal): Promise<RoutePlan> {
    return post<RoutePlan>('/api/route', request, signal)
}

// --- the whole day at once -------------------------------------------------

// One stamp's answer to "what if I left then?". No geometry: the answer is a
// time, and once the reader picks one the map asks /api/route for that stamp
// the way it always did. Two dozen polylines to draw one of them would be the
// larger half of the payload and none of the point.
export type Departure = {
    // "HH:MM", and always a stamp the time slider can stop on -- the backend
    // takes them from the same daylight_times that cut the tiles.
    time: string
    distance_m: number
    shade_fraction: number
    // The same hour's shade on the plain shortest path. The comparison is the
    // product here as much as it is on a single route: a shade curve alone
    // peaks at dusk on every walk in the city, which is a fact about the sun
    // rather than about the route.
    baseline_shade_fraction: number
}

// What POST /api/day returns: one row per daylight stamp, ascending.
export type DayPlan = {
    // Stated once because it is one number. The direct route is the same path
    // at every hour -- at alpha 0 the weight is the edge's own length, and a
    // length does not depend on where the sun is.
    baseline_distance_m: number
    departures: Departure[]
}

// RouteRequest without the stamp. The scan is the one call that names no time,
// because asking about all of them is the question.
export type DayRequest = {
    origin: LatLon
    destination: LatLon
    date: string
    alpha: number
}

export async function fetchDayScan(request: DayRequest, signal?: AbortSignal): Promise<DayPlan> {
    return post<DayPlan>('/api/day', request, signal)
}
