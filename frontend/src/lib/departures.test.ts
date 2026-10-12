import { describe, expect, it } from 'vitest'
import type { DayPlan, Departure, Preference } from '../api/client'
import { CHART, area, bands, gain, heightOf, polyline, preferredShare, share, spread, verdict } from './departures'
import { minutes } from './format'
import { clockAfter, endsIn } from './stamps'

const preference = (mode: Preference['mode'] = 'shade'): Preference => ({
    mode,
    season: 'autumn',
    source: 'weather',
    reason: 'comfortable',
    weather: null,
    weather_provider: null,
    weather_url: null,
})

type TestRow = [string, number, number, Preference['mode']?, number?]
const BASELINE_DURATION = 3831 / 1.35
const day = (rows: TestRow[]): DayPlan => ({
    baseline_distance_m: 3831,
    baseline_duration_s: BASELINE_DURATION,
    preference: preference(),
    departures: rows.map(([time, shade, baseline, mode = 'shade', duration]): Departure => ({
        time,
        distance_m: 4200,
        duration_s: duration ?? 4200 / 1.35,
        shade_fraction: shade,
        baseline_shade_fraction: baseline,
        preference: preference(mode),
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
    it('recommends a useful detour rather than the city-wide shade peak at dusk', () => {
        const answer = verdict(SEPTEMBER)

        expect(answer?.best.time).toBe('15:00')
        expect(answer?.bestShare).toBeCloseTo(0.71)
        expect(answer?.bestGain).toBeCloseTo(0.25)
        expect(answer?.hasUsefulDetour).toBe(true)
    })

    it('takes the extra walking into account instead of maximizing gain alone', () => {
        const answer = verdict(day([
            ['08:00', 0.8, 0.3, 'shade', BASELINE_DURATION + 1200],
            ['12:00', 0.65, 0.45, 'shade', BASELINE_DURATION + 60],
        ]))

        expect(answer?.best.time).toBe('12:00')
        expect(answer?.extraDuration_s).toBeCloseTo(60)
        expect(answer?.payoff.time).toBe('08:00')
        expect(answer?.payoffGain).toBeCloseTo(0.5)
    })

    it('uses each departure’s sun preference', () => {
        const answer = verdict(day([
            ['08:00', 0.7, 0.72, 'sun'],
            ['12:00', 0.2, 0.5, 'sun'],
            ['16:00', 0.4, 0.55, 'sun'],
        ]))

        expect(answer?.best.time).toBe('12:00')
        expect(answer?.bestShare).toBeCloseTo(0.8)
        expect(answer?.bestGain).toBeCloseTo(0.3)
    })

    it('compares improvements when weather changes the preferred exposure', () => {
        const answer = verdict(day([
            ['08:00', 0.2, 0.7, 'sun'],
            ['12:00', 0.95, 0.9, 'shade'],
            ['16:00', 0.99, 0.1, 'balanced', BASELINE_DURATION],
        ]))

        // Noon has the largest preferred share, but almost all of it is
        // already on the direct walk. The morning route adds much more sun.
        expect(answer?.best.time).toBe('08:00')
        expect(answer?.best.preference.mode).toBe('sun')
        expect(answer?.bestGain).toBeCloseTo(0.5)
        expect(answer?.payoff.time).toBe('08:00')
    })

    it('recommends the shortest walk when weather has no exposure preference', () => {
        const answer = verdict(day([
            ['08:00', 0.99, 0.1, 'balanced', BASELINE_DURATION + 120],
            ['12:00', 0.2, 0.3, 'balanced', BASELINE_DURATION],
            ['16:00', 0.9, 0.4, 'balanced', BASELINE_DURATION],
        ]))

        expect(answer?.best.time).toBe('12:00')
        expect(answer?.bestShare).toBeNull()
        expect(answer?.hasUsefulDetour).toBe(false)
        expect(answer?.payoffGain).toBe(0)
    })

    it('does not recommend an extra walk for an imperceptible exposure gain', () => {
        const answer = verdict(day([
            ['08:00', 0.904, 0.9, 'shade', BASELINE_DURATION + 180],
            ['12:00', 0.5, 0.5, 'shade', BASELINE_DURATION],
        ]))

        expect(answer?.best.time).toBe('12:00')
        expect(answer?.hasUsefulDetour).toBe(false)
    })

    it('keeps the earlier hour when two are equally good', () => {
        const answer = verdict(day([['17:40', 1.0, 0.5], ['18:00', 1.0, 0.5]]))

        expect(answer?.best.time).toBe('17:40')
    })

    it('has nothing to say about a day with no daylight in it', () => {
        expect(verdict(day([]))).toBeNull()
    })
})

describe('gain', () => {
    it('measures gain in the direction selected by the server', () => {
        const row = SEPTEMBER.departures[3]   // 15:00, 0.71 against 0.46

        expect(gain(row)).toBeCloseTo(0.25)
        expect(gain({ ...row, preference: preference('sun') })).toBeCloseTo(-0.25)
    })

    it('does not invent an exposure gain for a neutral route', () => {
        const row = day([['12:00', 0.9, 0.1, 'balanced']]).departures[0]

        expect(gain(row)).toBe(0)
        expect(preferredShare(row)).toBeNull()
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

describe('minutes', () => {
    it('rounds to the minute, because the pace it comes from is one constant', () => {
        expect(minutes(222)).toBe('4 min')
        expect(minutes(1320)).toBe('22 min')
    })

    it('breaks into hours for the far edge of the graph', () => {
        // The walk network reaches 15 km from the centre, which is a three-hour
        // walk end to end. "187 min" is not a number anybody reads.
        expect(minutes(60 * 60)).toBe('1 h')
        expect(minutes(82 * 60)).toBe('1 h 22')
        // Padded, so the minutes past the hour cannot be read as a count.
        expect(minutes(65 * 60)).toBe('1 h 05')
    })
})

describe('clockAfter', () => {
    it('adds the walk to the departure', () => {
        expect(clockAfter('10:00', 22 * 60)).toBe('10:22')
        expect(clockAfter('09:45', 30 * 60)).toBe('10:15')
    })

    it('wraps at midnight rather than running past it', () => {
        // A late walk that finishes at 00:10 finishes at 00:10. "24:10" is not
        // a time, and neither is "23:70".
        expect(clockAfter('23:50', 20 * 60)).toBe('00:10')
        expect(clockAfter('23:00', 60 * 60)).toBe('00:00')
    })
})

describe('endsIn', () => {
    // A September day, thinned: whole hours in the middle, twenty-minute steps
    // at dusk where a low sun moves a shadow across a street inside the hour.
    const STAMPS = ['12:00', '13:00', '16:00', '16:20', '16:40', '17:00', '17:20']

    it('says nothing when the walk stays inside the hour on screen', () => {
        // Twenty minutes from 12:00 is still nearest 12:00. There is no second
        // sun to warn anybody about.
        expect(endsIn('12:00', 20 * 60, STAMPS)).toBeNull()
    })

    it('names the stamp the far end is really walked in', () => {
        // An hour and a half from 12:00 lands at 13:30, which is nearest 13:00.
        expect(endsIn('12:00', 90 * 60, STAMPS)).toBe('13:00')
        expect(endsIn('16:00', 45 * 60, STAMPS)).toBe('16:40')
    })

    it('calls it dark once the walk runs past the last stamp', () => {
        // Not "17:20's shadows" -- those have gone, and claiming them would be
        // the same lie in the other direction.
        expect(endsIn('17:00', 60 * 60, STAMPS)).toBe('dark')
    })

    it('has nothing to say without a manifest', () => {
        expect(endsIn('12:00', 90 * 60, [])).toBeNull()
    })
})
