import { describe, expect, it } from 'vitest'
import { CITY, locate, metresBetween, outOfReach, roughly, type Fix } from './locate'

// A stand-in for the device, which a test runner does not have. Both callbacks
// are fired synchronously, which is the one thing a real one never does -- the
// promise still settles either way, and that is what is under test.
const device = (coords: { latitude: number, longitude: number, accuracy: number }) => ({
    getCurrentPosition: (ok: PositionCallback) =>
        ok({ coords, timestamp: 0 } as unknown as GeolocationPosition),
})

const refusing = (code: number) => ({
    getCurrentPosition: (_ok: PositionCallback, fail?: PositionErrorCallback | null) =>
        fail?.({ code, message: 'User denied Geolocation' } as GeolocationPositionError),
})

describe('metresBetween', () => {
    it('is zero for one point against itself', () => {
        expect(metresBetween(CITY, CITY)).toBe(0)
    })

    it('measures a hundredth of a degree, north and east', () => {
        // North is a hundredth of a meridian wherever you stand. East is not:
        // at Astana's latitude the parallels have closed to 63% of that, and a
        // flat lat/lon distance would be wrong by a third.
        expect(metresBetween(CITY, [51.1705, 71.4704])).toBeCloseTo(1111.95, 1)
        expect(metresBetween(CITY, [51.1605, 71.4804])).toBeCloseTo(697.35, 1)
    })
})

describe('roughly', () => {
    it('rounds to something the fix can actually support', () => {
        expect(roughly(24)).toBe('20 m')
        expect(roughly(118)).toBe('120 m')
        expect(roughly(1420)).toBe('1.4 km')
        expect(roughly(970_573)).toBe('971 km')
    })
})

describe('outOfReach', () => {
    it('accepts a point inside the graph', () => {
        expect(outOfReach(CITY)).toBeNull()
        // 15.0 km out is the last place a walk can start from.
        expect(outOfReach([51.2953, 71.4704])).toBeNull()
    })

    it('rejects a real fix in another city, and says how far', () => {
        const message = outOfReach([43.2380, 76.8829])
        expect(message).toContain('971 km')
        // The limit is quoted from the constant, not written into the sentence:
        // the same message on the server went on naming a 1.7 km graph for
        // months after the graph stopped being one.
        expect(message).toContain('15 km')
    })

    it('rejects a point that is close by, but still off the map', () => {
        expect(outOfReach([49.8047, 73.1094])).toContain('190 km')
    })
})

describe('locate', () => {
    it('hands back the point and how well the device knows it', async () => {
        const fix: Fix = await locate(device({ latitude: 51.09, longitude: 71.41, accuracy: 18 }))
        expect(fix.point).toEqual([51.09, 71.41])
        expect(fix.accuracy).toBe(18)
    })

    it('turns each refusal into something the reader can act on', async () => {
        await expect(locate(refusing(1))).rejects.toThrow(/Allow it in the browser/)
        await expect(locate(refusing(2))).rejects.toThrow(/could not work out where it is/)
        await expect(locate(refusing(3))).rejects.toThrow(/took too long/)
    })

    it('falls back to the browser’s own words for a code it has never heard of', async () => {
        await expect(locate(refusing(99))).rejects.toThrow('User denied Geolocation')
    })

    it('names the real reason when the API is not there at all', async () => {
        // What an http:// page gets -- a phone opening the dev server over the
        // LAN -- where the button would otherwise simply do nothing.
        await expect(locate(undefined)).rejects.toThrow(/insecure page/)
    })
})
