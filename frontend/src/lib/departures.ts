// The arithmetic behind "when should I leave?": which hour to recommend, and
// where each hour lands on the chart. None of it touches the DOM, which is what
// makes it testable in the node environment the suite already runs in -- and
// the recommendation is the one thing here worth being sure of, because it is
// the sentence the reader will act on without checking the picture.

import type { DayPlan, Departure } from '../api/client'
import { toMinutes } from './stamps'

// The chart's own coordinate space. Fixed, and scaled to whatever width the
// panel has: the viewBox keeps its aspect ratio, so a wider panel on a phone
// draws the same picture a little larger rather than a stretched one.
export const CHART = { width: 282, height: 86, pad: 5 }

// Sun is the other side of the measured shade fraction. A chart must use one
// direction throughout; a weather-driven day can contain both preferences.
export const share = (fraction: number, seekingSun: boolean) =>
    seekingSun ? 1 - fraction : fraction

// Improvements can be compared across modes; raw shade and sun shares cannot.
// Balanced weather has no exposure preference, so it earns no detour benefit.
export const gain = (row: Departure) => {
    if (row.preference.mode === 'balanced') return 0
    const seekingSun = row.preference.mode === 'sun'
    return share(row.shade_fraction, seekingSun) - share(row.baseline_shade_fraction, seekingSun)
}

export const preferredShare = (row: Departure) => row.preference.mode === 'balanced'
    ? null
    : share(row.shade_fraction, row.preference.mode === 'sun')

// Where each stamp sits along the x axis: by the clock, not by index. The
// stamps bunch up at dawn and dusk, because a low sun moves a shadow across a
// street inside the hour and those hours are cut into thirds. Spreading them
// evenly would stretch a twenty-minute step to the width of an hour on a chart
// whose entire x axis is time -- the dawn cluster is a real fact about the day
// and it should look like one.
export const spread = (departures: Departure[]): number[] => {
    const minutes = departures.map(row => toMinutes(row.time))
    const first = minutes[0]
    const span = minutes[minutes.length - 1] - first

    // Inset by the same margin the y axis keeps, and for the same reason: the
    // first and last stamps carry the marker for the recommended hour as often
    // as any other, and a dot centred on x=0 is a half-dot.
    const reach = CHART.width - 2 * CHART.pad

    // One stamp, or a day so short every stamp shares a minute: there is no
    // axis to place anything along, so it goes in the middle.
    return minutes.map(at =>
        (span > 0 ? CHART.pad + ((at - first) / span) * reach : CHART.width / 2))
}

// Share 1 at the top, 0 at the bottom, with room at both ends for a stroke to
// sit inside the box rather than half outside it.
export const heightOf = (value: number) =>
    CHART.pad + (1 - Math.min(Math.max(value, 0), 1)) * (CHART.height - 2 * CHART.pad)

export const polyline = (xs: number[], values: number[]) =>
    xs.map((x, index) => `${x.toFixed(1)},${heightOf(values[index]).toFixed(1)}`).join(' ')

// The same line, closed along the floor of the chart. A filled curve reads as
// a quantity where a bare stroke reads as a trend, and the quantity -- how much
// of the walk -- is the subject.
export const area = (xs: number[], values: number[]) =>
    `${xs[0].toFixed(1)},${CHART.height} ${polyline(xs, values)} `
    + `${xs[xs.length - 1].toFixed(1)},${CHART.height}`

// A click target per stamp, each reaching halfway to its neighbours, so every
// pixel of the chart belongs to exactly one hour. Without them the only way to
// choose a departure would be the button under the chart, and the shape you
// just read would not be something you could point at.
export const bands = (xs: number[]) =>
    xs.map((x, index) => {
        const left = index === 0 ? 0 : (xs[index - 1] + x) / 2
        const right = index === xs.length - 1 ? CHART.width : (x + xs[index + 1]) / 2
        return { x: left, width: Math.max(right - left, 0) }
    })

export type Verdict = {
    // A useful exposure improvement with less additional walking. If no hour
    // offers an improvement, use the shortest walk and keep the earlier tie.
    best: Departure
    bestShare: number | null
    bestGain: number
    extraDuration_s: number
    hasUsefulDetour: boolean
    // The largest improvement, independent of detour length. Kept separate
    // from the recommendation, which takes the extra walking into account.
    payoff: Departure
    payoffGain: number
}

export const verdict = (day: DayPlan): Verdict | null => {
    const rows = day.departures
    if (!rows.length) return null

    const extraDuration = (row: Departure) => Math.max(row.duration_s - day.baseline_duration_s, 0)
    // One minute of overhead prevents a tiny detour from dominating through
    // division by nearly zero. Below half a percentage point the improvement
    // cannot even be read in the UI; recommend the shortest walk instead.
    const useful = rows.filter(row => gain(row) >= 0.005)
    const value = (row: Departure) => gain(row) / (1 + extraDuration(row) / 60)
    const shortest = rows.reduce((a, b) => b.duration_s < a.duration_s ? b : a)
    const best = useful.length ? useful.reduce((a, b) => {
        const difference = value(b) - value(a)
        if (difference > 1e-9) return b
        if (Math.abs(difference) <= 1e-9 && b.duration_s < a.duration_s) return b
        return a
    }) : shortest
    const payoff = rows.reduce((a, b) => gain(b) > gain(a) ? b : a)

    return {
        best,
        bestShare: preferredShare(best),
        bestGain: gain(best),
        extraDuration_s: extraDuration(best),
        hasUsefulDetour: useful.length > 0,
        payoff,
        payoffGain: gain(payoff),
    }
}
