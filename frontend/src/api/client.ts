// Typed edge between this app and the Python that feeds it. Mirrors
// RouteRequest in backend/main.py and the manifest written by
// scripts/export_shadow_tiles.py -- when either changes, this file changes
// with it.

export type LatLon = [number, number]

// What scripts/export_shadow_tiles.py wrote beside the tiles. Which hours have
// a tileset is a property of the date -- 16 of them in June, 8 in December --
// so the map reads the answer rather than assuming a daylight window that is
// only ever right for one season. `hours` is sorted ascending.
export type ShadowManifest = {
    date: string
    hours: number[]
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
    alpha: number
}

export async function fetchRoute(request: RouteRequest, signal?: AbortSignal): Promise<RoutePlan> {
    const response = await fetch('/api/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal,
    })

    if (!response.ok) {
        // FastAPI puts the reason in `detail` -- a plain string for the errors we
        // raise ourselves, a list of field problems for validation failures.
        const body = await response.text()
        let message = body.slice(0, 200)
        try {
            const detail = JSON.parse(body).detail
            if (typeof detail === 'string') message = detail
        } catch {
            // Not JSON. Keep the raw text.
        }
        throw new Error(message)
    }

    return response.json()
}
