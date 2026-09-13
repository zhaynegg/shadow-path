import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ShadowManifest } from '../api/client'
import {
    WHOLE_HOURS, cityMinutes, isDaylight, nearestStamp, prettyDate, sliderTimes, toMinutes,
} from './stamps'

// A real mid-September manifest, because its shape is the whole reason this
// module exists: daylight runs 06:00 to 18:20, the low-sun hours at either end
// are cut into thirds, and the middle of the day stays hourly. A uniform day
// would let a wrong assumption about the step pass unnoticed.
const SEPTEMBER: ShadowManifest = {
    date: '2026-09-13',
    times: [
        '06:00', '06:20', '06:40', '07:00', '07:20', '07:40', '08:00', '08:20', '08:40',
        '09:00', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00', '16:20',
        '16:40', '17:00', '17:20', '17:40', '18:00', '18:20',
    ],
    layer: 'shadows',
    generated_at: '2026-09-13T10:47:36+00:00',
}

describe('toMinutes', () => {
    it('counts from midnight', () => {
        expect(toMinutes('00:00')).toBe(0)
        expect(toMinutes('06:20')).toBe(380)
        expect(toMinutes('12:00')).toBe(720)
        expect(toMinutes('23:59')).toBe(1439)
    })
})

describe('WHOLE_HOURS', () => {
    it('covers the night as well as the day', () => {
        expect(WHOLE_HOURS).toHaveLength(24)
        expect(WHOLE_HOURS[0]).toBe('00:00')
        expect(WHOLE_HOURS.at(-1)).toBe('23:00')
    })

    it('zero-pads, so sorting as text stays chronological', () => {
        expect(WHOLE_HOURS[9]).toBe('09:00')
        expect(WHOLE_HOURS.every(time => /^\d\d:\d\d$/.test(time))).toBe(true)
    })
})

describe('isDaylight', () => {
    it('follows the manifest rather than a guess at sunset', () => {
        expect(isDaylight('06:00', SEPTEMBER)).toBe(true)
        expect(isDaylight('19:00', SEPTEMBER)).toBe(false)
    })

    it('is false inside the day for an hour that was not cut', () => {
        // 09:20 is broad daylight, but the sun is high enough by then that the
        // hour is not worth thirds, so no tileset exists for it.
        expect(isDaylight('09:20', SEPTEMBER)).toBe(false)
    })

    it('assumes daylight before the manifest arrives', () => {
        // Nothing is known yet. Greying the whole slider on every load would
        // read as a broken map more often than it would be right.
        expect(isDaylight('13:00', null)).toBe(true)
    })
})

describe('sliderTimes', () => {
    it('falls back to the whole hours without a manifest', () => {
        expect(sliderTimes(null)).toEqual(WHOLE_HOURS)
    })

    it('merges the manifest in without duplicating the hours it shares', () => {
        const times = sliderTimes(SEPTEMBER)
        expect(times.filter(t => t === '13:00')).toHaveLength(1)
        expect(times).toContain('06:20')
    })

    it('keeps every whole hour, including the ones with no tiles', () => {
        // This is what lets the clock be dragged into the night at all.
        const times = sliderTimes(SEPTEMBER)
        for (const hour of WHOLE_HOURS) expect(times).toContain(hour)
    })

    it('comes out chronological', () => {
        const times = sliderTimes(SEPTEMBER)
        expect(times).toEqual([...times].sort((a, b) => toMinutes(a) - toMinutes(b)))
    })
})

describe('nearestStamp', () => {
    it('returns a stamp it lands on exactly', () => {
        expect(nearestStamp(toMinutes('13:00'), sliderTimes(SEPTEMBER))).toBe('13:00')
    })

    it('rounds to the nearest stop, not down to the preceding one', () => {
        expect(nearestStamp(toMinutes('13:15'), ['13:00', '13:20'])).toBe('13:20')
    })

    it('breaks a tie towards the earlier stamp', () => {
        expect(nearestStamp(toMinutes('13:10'), ['13:00', '13:20'])).toBe('13:00')
    })

    it('opens on the hour at hand after dark, not on the last daylight stamp', () => {
        // The regression. This once chose from the manifest alone, so opening
        // the map at 21:25 showed 18:20 -- three hours of shadows that had
        // already gone, with nothing about the picture to say so. Choosing from
        // the slider's own stops is the fix, and the two have to be tested
        // together because either alone looks right.
        expect(nearestStamp(toMinutes('21:25'), sliderTimes(SEPTEMBER))).toBe('21:00')
    })

    it('falls back to noon when there are no stamps at all', () => {
        expect(nearestStamp(toMinutes('21:25'), [])).toBe('12:00')
    })
})

describe('prettyDate', () => {
    it('spells the date out', () => {
        expect(prettyDate('2026-09-13')).toBe('13 Sept 2026')
    })

    it('does not slip a day west of Greenwich', () => {
        // Parsed bare, "2026-01-01" is UTC midnight, which is still 31 December
        // in New York. prettyDate pins noon to put the date out of reach of any
        // offset. Runs under TZ=America/New_York -- see vitest.config.ts.
        expect(prettyDate('2026-01-01')).toBe('1 Jan 2026')
    })
})

describe('cityMinutes', () => {
    afterEach(() => vi.useRealTimers())

    const at = (instant: string) => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date(instant))
        return cityMinutes()
    }

    it('reads Astana\'s clock, not the viewer\'s', () => {
        // 09:00 UTC is 14:00 in a city five hours ahead. Under this suite's
        // New York clock the same instant is 05:00, so a function that had
        // quietly dropped the timeZone would answer 300 here.
        expect(at('2026-09-13T09:00:00Z')).toBe(14 * 60)
    })

    it('has already rolled over when the viewer is still on yesterday', () => {
        // 19:00 UTC is midnight in Astana. This is the same edge the backend's
        // today() is written around, and the map has to agree with it.
        expect(at('2026-09-13T19:30:00Z')).toBe(30)
    })
})
