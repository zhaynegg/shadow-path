import type { DayPlan } from '../api/client'
import { CHART, area, bands, heightOf, polyline, share, spread, verdict } from '../lib/departures'
import { minutes, percent } from '../lib/format'
import { toMinutes } from '../lib/stamps'

type DeparturePlannerProp = {
    day: DayPlan | null,
    loading: boolean,
    error: string | null,
    // Signed, exactly as the summary reads it: which of the two things on the
    // chart the reader came looking for.
    alpha: number,
    // The stamp the map is showing, marked on the chart so the picture and the
    // clock are visibly the same moment.
    time: string,
    onScan: () => void,
    onPick: (time: string) => void,
}

// Asked for rather than fetched on sight. A scan is two dozen searches, and
// nobody wants one every time they nudge a pin -- the button is what makes it
// a question the reader put, and the answer worth the second it costs.
function DeparturePlanner({ day, loading, error, alpha, time, onScan, onPick }: DeparturePlannerProp) {
    const seekingSun = alpha < 0
    const wanted = seekingSun ? 'sun' : 'shade'

    if (error) return <div className="summary-error depart-slot">{error}</div>

    if (loading) {
        return (
            <div className="summary-hint depart-slot">
                <span className="spinner" />
                Planning every departure today…
            </div>
        )
    }

    if (!day) {
        return (
            <button className="btn btn-wide depart-slot" onClick={onScan}>
                When should I leave?
            </button>
        )
    }

    const rows = day.departures
    const answer = verdict(day, seekingSun)
    if (!rows.length || !answer) {
        return <div className="summary-hint depart-slot">The sun never rises on that date.</div>
    }

    const xs = spread(rows)
    const routeShare = rows.map(row => share(row.shade_fraction, seekingSun))
    const directShare = rows.map(row => share(row.baseline_shade_fraction, seekingSun))

    const chosen = rows.findIndex(row => row.time === answer.best.time)
    // Only when the clock is actually inside the lit part of the day. After
    // dusk the slider is somewhere this chart does not reach, and a marker
    // pinned to the edge would claim otherwise.
    const now = rows.findIndex(row => row.time === time)

    const reading = (index: number) =>
        `${rows[index].time} — ${percent(routeShare[index])} ${wanted}, `
        + `${minutes(rows[index].duration_s)}`

    return (
        <div className="depart">
            <div className="field-head">
                <span className="field-label">When to leave</span>
                <span className="field-value">{percent(answer.bestShare)} {wanted}</span>
            </div>

            {/* Described rather than read: the marks are a shape, and the two
                sentences under the chart say everything the shape does. The
                bands below are still clickable, which is a convenience for a
                mouse rather than the only way through. */}
            <svg className="chart" viewBox={`0 0 ${CHART.width} ${CHART.height}`}
                role="img" aria-label={
                    `How much of the walk is in ${wanted}, by departure time, from `
                    + `${rows[0].time} to ${rows[rows.length - 1].time}.`}>
                <polygon className="chart-area" points={area(xs, routeShare)}
                    fill={seekingSun ? 'var(--sun)' : 'var(--shade)'} />
                {/* Dashed, like the grey line on the map and the dash in the
                    stat row above: the same walk, marked the same way. */}
                <polyline className="chart-direct" points={polyline(xs, directShare)} />
                <polyline className="chart-route" points={polyline(xs, routeShare)} />

                {now >= 0 && (
                    <line className="chart-now" x1={xs[now]} x2={xs[now]} y1={0} y2={CHART.height} />
                )}
                <circle className="chart-best" cx={xs[chosen]} cy={heightOf(routeShare[chosen])} r={3.4} />

                {bands(xs).map((band, index) => (
                    <rect key={rows[index].time} className="chart-hit"
                        x={band.x} y={0} width={band.width} height={CHART.height}
                        onClick={() => onPick(rows[index].time)}>
                        <title>{reading(index)}</title>
                    </rect>
                ))}
            </svg>

            <div className="field-foot">
                <span>{rows[0].time}</span>
                <span>{toMinutes(answer.best.time) < 720 ? 'morning' : 'afternoon'} is best</span>
                <span>{rows[rows.length - 1].time}</span>
            </div>

            <button className="depart-pick" onClick={() => onPick(answer.best.time)}>
                <span>Leave at <strong>{answer.best.time}</strong></span>
                <span className="depart-share">{percent(answer.bestShare)} {wanted}</span>
            </button>

            <div className="verdict">
                {/* The honest caveat on every shade curve: it peaks at dusk on
                    every walk in the city, because by then the whole city is
                    shaded. Read against the direct route it says something the
                    sun alone does not -- which is where this line comes from,
                    and why it names a different hour more often than not. */}
                {answer.payoffGain < 0.005
                    ? `Going the long way buys nothing today — both routes are the same walk at every hour.`
                    : (
                        <>
                            Detouring pays most at <strong>{answer.payoff.time}</strong>:{' '}
                            <strong>{percent(answer.payoffGain)}</strong> more of the walk in{' '}
                            {wanted} than going direct.
                        </>
                    )}
            </div>
        </div>
    )
}

export default DeparturePlanner
