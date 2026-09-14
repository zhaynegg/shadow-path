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

// What the reader actually asked for. The backend only ever measures shade; sun
// is the other side of the same number, not a second measurement. Exactly the
// rule RouteSummary reads its stat rows by, so the card and the chart cannot
// end up describing two different walks.
export const share = (fraction: number, seekingSun: boolean) =>
    seekingSun ? 1 - fraction : fraction

// How much the detour bought at this hour, in the direction asked for. Reads
// the same subtraction either way round: shade gained, or shade given up, which
// is sun gained.
export const gain = (row: Departure, seekingSun: boolean) =>
    share(row.shade_fraction, seekingSun) - share(row.baseline_shade_fraction, seekingSun)

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
    // The hour with the most of what was asked for. This is the answer to the
    // question as it was put, and on most walks it is dawn or dusk.
    best: Departure
    bestShare: number
    // The hour where detouring buys the most over the direct route. Not the
    // same question, and usually not the same hour: at dusk the whole city is
    // shaded and the clever route is worth nothing, which is worth saying out
    // loud next to a recommendation to leave at dusk.
    payoff: Departure
    payoffGain: number
}

export const verdict = (day: DayPlan, seekingSun: boolean): Verdict | null => {
    const rows = day.departures
    if (!rows.length) return null

    // Strict comparisons, so a tie keeps the earlier hour. Ties are ordinary
    // near dusk, where several stamps round to fully shaded, and an answer that
    // drifted to the last of them would be telling somebody to wait for no
    // reason.
    const best = rows.reduce((a, b) =>
        share(b.shade_fraction, seekingSun) > share(a.shade_fraction, seekingSun) ? b : a)
    const payoff = rows.reduce((a, b) => (gain(b, seekingSun) > gain(a, seekingSun) ? b : a))

    return {
        best,
        bestShare: share(best.shade_fraction, seekingSun),
        payoff,
        payoffGain: gain(payoff, seekingSun),
    }
}
