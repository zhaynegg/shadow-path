import { describe, expect, it } from 'vitest'
import type { DayPlan, Departure } from '../api/client'
import { CHART, area, bands, gain, heightOf, polyline, share, spread, verdict } from './departures'

// [time, route shade, direct shade] -- the three numbers a stamp comes back as,
// with the distance left out because nothing here reads it.
const day = (rows: [string, number, number][]): DayPlan => ({
    baseline_distance_m: 3831,
    departures: rows.map(([time, shade, baseline]): Departure => ({
        time,
        distance_m: 4200,
        shade_fraction: shade,
        baseline_shade_fraction: baseline,
    })),
})

// A September day over one real walk, thinned to five stamps: shade highest at
// either end because the sun is low, lowest at noon, and the detour worth most
// in the middle of the afternoon. Measured, not invented -- these are rows from
// /api/day for the city centre on 13 September 2026.
const SEPTEMBER = day([
    ['06:00', 0.97, 0.94],
    ['07:00', 0.88, 0.68],
    ['12:00', 0.57, 0.41],
    ['15:00', 0.71, 0.46],
    ['18:20', 0.99, 0.98],
])

describe('share', () => {
    it('reads the other side of the number for a sun-seeker', () => {
        expect(share(0.7, false)).toBeCloseTo(0.7)
        expect(share(0.7, true)).toBeCloseTo(0.3)
    })
})

describe('verdict', () => {
    it('recommends the hour with the most of what was asked for', () => {
        const answer = verdict(SEPTEMBER, false)

        expect(answer?.best.time).toBe('18:20')
        expect(answer?.bestShare).toBeCloseTo(0.99)
    })

    it('names the hour the detour pays for separately from the shadiest one', () => {
        // The reason both numbers exist. A shade curve peaks at dusk on every
        // walk in the city, because by then the whole city is shaded -- at
        // 18:20 the direct route is already 98% shaded and the clever route is
        // worth one point. The afternoon is where the routing earns anything.
        const answer = verdict(SEPTEMBER, false)

        expect(answer?.payoff.time).toBe('15:00')
        expect(answer?.payoffGain).toBeCloseTo(0.25)
        expect(answer?.payoff.time).not.toBe(answer?.best.time)
    })

    it('turns around for a sun-seeker', () => {
        // Astana is one of the coldest capitals on earth and for half the year
        // the walk you want is the sunny one. A chart that always recommended
        // the shadiest hour would send that reader out at dusk.
        const answer = verdict(SEPTEMBER, true)

        expect(answer?.best.time).toBe('12:00')
        expect(answer?.bestShare).toBeCloseTo(0.43)
        // Sun gained is shade given up: the direct route at noon is 59% sunlit
        // against the sun-seeking route's 43%, so detouring here costs.
        expect(answer?.payoffGain).toBeLessThanOrEqual(0)
    })

    it('keeps the earlier hour when two are equally good', () => {
        // Ordinary near dusk, where several stamps round to fully shaded. An
        // answer that drifted to the last of them would be telling somebody to
        // wait forty minutes for nothing.
        const answer = verdict(day([['17:40', 1.0, 0.5], ['18:00', 1.0, 0.5]]), false)

        expect(answer?.best.time).toBe('17:40')
    })

    it('has nothing to say about a day with no daylight in it', () => {
        expect(verdict(day([]), false)).toBeNull()
    })
})

describe('gain', () => {
    it('is the same subtraction read in whichever direction was asked for', () => {
        const row = SEPTEMBER.departures[3]   // 15:00, 0.71 against 0.46

        expect(gain(row, false)).toBeCloseTo(0.25)
        expect(gain(row, true)).toBeCloseTo(-0.25)
    })
})

describe('spread', () => {
    it('places the stamps by the clock, not by their turn', () => {
        // The stamps bunch up at dawn: a low sun moves a shadow across a street
        // inside the hour, so those hours are cut into thirds. Spaced by index
        // the three dawn stamps below would take up two thirds of the axis;
        // spaced by the clock they are the forty minutes they really are.
        const xs = spread(day([
            ['06:00', 1, 1], ['06:20', 1, 1], ['06:40', 1, 1], ['12:00', 1, 1],
        ]).departures)

        expect(xs[0]).toBeCloseTo(CHART.pad)
        expect(xs[3]).toBeCloseTo(CHART.width - CHART.pad)
        expect(xs[2]).toBeLessThan(CHART.width * 0.15)
    })

    it('puts a lone stamp in the middle rather than dividing by nothing', () => {
        expect(spread(day([['12:00', 1, 1]]).departures)).toEqual([CHART.width / 2])
    })
})

describe('heightOf', () => {
    it('puts the whole walk at the top and none of it at the bottom', () => {
        expect(heightOf(1)).toBeCloseTo(CHART.pad)
        expect(heightOf(0)).toBeCloseTo(CHART.height - CHART.pad)
    })

    it('clamps, so a fraction a hair over one cannot draw outside the box', () => {
        expect(heightOf(1.02)).toBeCloseTo(CHART.pad)
        expect(heightOf(-0.02)).toBeCloseTo(CHART.height - CHART.pad)
    })
})

describe('bands', () => {
    it('covers the chart edge to edge, one hour per pixel', () => {
        // Every pixel belongs to exactly one stamp, or clicking the chart picks
        // a departure only where the gaps happen not to be.
        const xs = spread(SEPTEMBER.departures)
        const covered = bands(xs)

        expect(covered[0].x).toBe(0)
        expect(covered.reduce((total, band) => total + band.width, 0)).toBeCloseTo(CHART.width)

        for (const [index, band] of covered.entries()) {
            if (index === 0) continue
            const previous = covered[index - 1]
            expect(band.x).toBeCloseTo(previous.x + previous.width)
        }
    })
})

describe('polyline and area', () => {
    it('draws a point per stamp', () => {
        const xs = spread(SEPTEMBER.departures)
        const values = SEPTEMBER.departures.map(row => row.shade_fraction)

        expect(polyline(xs, values).split(' ')).toHaveLength(5)
        // The same line plus the two corners that close it along the floor.
        expect(area(xs, values).split(' ')).toHaveLength(7)
        const right = (CHART.width - CHART.pad).toFixed(1)
        expect(area(xs, values).endsWith(`${right},${CHART.height}`)).toBe(true)
    })
})
