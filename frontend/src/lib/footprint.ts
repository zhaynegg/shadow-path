// Picking one building out of what a vector tile hands back.
//
// queryRenderedFeatures returns the *feature* drawn under a point, and in the
// basemap's building layer a feature is not always one building. Zoomed out,
// the tiles pack many footprints into a single MultiPolygon -- so highlighting
// what comes back paints half the district. Zoomed in, the same query returns
// one Polygon and the problem is invisible, which is how it survives a first
// look at close range.

type Ring = number[][]
type Geometry = { type: string, coordinates: unknown }

/** Ray casting, on the outer ring. Even crossings out, odd crossings in. */
export const inRing = (ring: Ring, lon: number, lat: number): boolean => {
    let inside = false
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i]
        const [xj, yj] = ring[j]
        // Only edges that straddle the point's latitude can be crossed by a ray
        // cast east from it; of those, count the ones crossed to the west.
        const straddles = (yi > lat) !== (yj > lat)
        if (straddles && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside
    }
    return inside
}

/**
 * The single footprint standing at this point, or null if none of them is.
 *
 * Null is a real answer and not a failure: a point in a courtyard, on a street
 * or in a park has no building under it, and a walk can perfectly well start
 * from one.
 */
export const footprintAt = (geometry: Geometry | undefined, lon: number, lat: number) => {
    if (!geometry) return null

    if (geometry.type === 'Polygon') {
        const rings = geometry.coordinates as Ring[]
        return inRing(rings[0], lon, lat) ? { type: 'Polygon' as const, coordinates: rings } : null
    }

    if (geometry.type === 'MultiPolygon') {
        const polygons = geometry.coordinates as Ring[][]
        const found = polygons.find(rings => inRing(rings[0], lon, lat))
        return found ? { type: 'Polygon' as const, coordinates: found } : null
    }

    return null
}
