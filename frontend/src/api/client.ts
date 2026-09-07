// Typed wrapper around the backend. Mirrors RouteRequest in backend/main.py --
// when that model changes, this file changes with it.

export type LatLon = [number, number]

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
