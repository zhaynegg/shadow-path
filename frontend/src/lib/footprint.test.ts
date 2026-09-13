import { describe, expect, it } from 'vitest'
import { footprintAt, inRing } from './footprint'

// Two unit squares, a gap apart. Small numbers rather than real coordinates:
// the arithmetic is the same and the expected answers can be read at a glance.
const LEFT = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
const RIGHT = [[10, 0], [12, 0], [12, 2], [10, 2], [10, 0]]

describe('inRing', () => {
    it('knows inside from outside', () => {
        expect(inRing(LEFT, 1, 1)).toBe(true)
        expect(inRing(LEFT, 5, 1)).toBe(false)
        expect(inRing(LEFT, 1, 9)).toBe(false)
    })

    it('handles a concave shape, where a bounding box would not', () => {
        // A C opening to the right. The notch is inside the box and outside
        // the building -- which is exactly the case a courtyard presents.
        const c = [[0, 0], [6, 0], [6, 2], [2, 2], [2, 4], [6, 4], [6, 6], [0, 6], [0, 0]]
        expect(inRing(c, 1, 3)).toBe(true)
        expect(inRing(c, 4, 3)).toBe(false)
    })
})

describe('footprintAt', () => {
    it('keeps a lone polygon that holds the point', () => {
        const hit = footprintAt({ type: 'Polygon', coordinates: [LEFT] }, 1, 1)
        expect(hit?.coordinates).toEqual([LEFT])
    })

    it('returns one building out of a tile that packed in many', () => {
        // The bug this exists for: zoomed out, the basemap hands back every
        // footprint in the tile as one MultiPolygon, and highlighting all of
        // it paints the district.
        const many = { type: 'MultiPolygon', coordinates: [[LEFT], [RIGHT]] }
        expect(footprintAt(many, 11, 1)?.coordinates).toEqual([RIGHT])
        expect(footprintAt(many, 1, 1)?.coordinates).toEqual([LEFT])
    })

    it('is null where no building stands', () => {
        // A courtyard, a street, a park -- all places a walk may start from.
        expect(footprintAt({ type: 'MultiPolygon', coordinates: [[LEFT], [RIGHT]] }, 5, 1)).toBeNull()
        expect(footprintAt({ type: 'Polygon', coordinates: [LEFT] }, 5, 5)).toBeNull()
    })

    it('ignores geometry that is not an area, and missing geometry', () => {
        expect(footprintAt({ type: 'LineString', coordinates: LEFT }, 1, 1)).toBeNull()
        expect(footprintAt(undefined, 1, 1)).toBeNull()
    })
})
