import type { DayPlan, Departure } from '../api/client'
import { CHART, area, bands, heightOf, polyline, preferredShare, share, spread, verdict } from '../lib/departures'
import { minutes, percent } from '../lib/format'

type DeparturePlannerProp = {
    day: DayPlan | null,
    loading: boolean,
    error: string | null,
    time: string,
    onScan: () => void,
    onPick: (time: string) => void,
}

const exposureLabel = (row: Departure) => {
    const value = preferredShare(row)
    return value === null ? 'Shortest route' : `${percent(value)} ${row.preference.mode}`
}

function DeparturePlanner({ day, loading, error, time, onScan, onPick }: DeparturePlannerProp) {
    if (error) return <div className="summary-error depart-slot" role="alert">{error}</div>

    if (loading) {
        return (
            <div className="summary-hint depart-slot" role="status">
                <span className="spinner" aria-hidden="true" />
                Comparing departure times…
            </div>
        )
    }

    if (!day) {
        return (
            <button className="btn btn-wide depart-slot" onClick={onScan}>
                Compare departure times
            </button>
        )
    }

    const rows = day.departures
    const answer = verdict(day)
    if (!rows.length || !answer) {
        return <div className="summary-hint depart-slot">No daylight departures on this date.</div>
    }

    // Keep a single measurement across the chart when the weather changes the
    // preference. Joining a shade percentage to a sun percentage would create
    // an apparent change in exposure that never happened.
    const seekingSun = rows.every(row => row.preference.mode === 'sun')
    const chartLabel = seekingSun ? 'Sun exposure' : 'Shade'
    const mixed = new Set(rows.map(row => row.preference.mode)).size > 1
    const xs = spread(rows)
    const routeShare = rows.map(row => share(row.shade_fraction, seekingSun))
    const directShare = rows.map(row => share(row.baseline_shade_fraction, seekingSun))
    const chosen = rows.findIndex(row => row.time === answer.best.time)
    const now = rows.findIndex(row => row.time === time)
    const reading = (row: Departure) => `${row.time} · ${minutes(row.duration_s)} · ${exposureLabel(row)}`
    const extraMinutes = Math.round(answer.extraDuration_s / 60)

    return (
        <div className="depart">
            <div className="field-head">
                <span className="field-label">Departure times</span>
                <span className="field-value">{chartLabel}</span>
            </div>
            <svg className="chart" viewBox={`0 0 ${CHART.width} ${CHART.height}`}
                role="img" aria-label={
                    `${chartLabel} along the selected and direct routes, from `
                    + `${rows[0].time} to ${rows[rows.length - 1].time}. `
                    + (mixed ? 'The preferred exposure changes with the weather. ' : '')
                    + 'Choose a departure with the selector below.'}>
                <polygon className="chart-area" points={area(xs, routeShare)}
                    fill={seekingSun ? 'var(--sun)' : 'var(--shade)'} />
                <polyline className="chart-direct" points={polyline(xs, directShare)} />
                <polyline className="chart-route" points={polyline(xs, routeShare)} />
                {now >= 0 && (
                    <line className="chart-now" x1={xs[now]} x2={xs[now]} y1={0} y2={CHART.height} />
                )}
                {mixed && bands(xs).map((band, index) => (
                    <rect key={`mode-${rows[index].time}`} x={band.x} y={CHART.height - 2}
                        width={band.width} height={2} aria-hidden="true"
                        fill={rows[index].preference.mode === 'sun' ? 'var(--sun)'
                            : rows[index].preference.mode === 'shade' ? 'var(--shade)' : 'var(--ink-3)'} />
                ))}
                <circle className="chart-best" cx={xs[chosen]} cy={heightOf(routeShare[chosen])} r={3.4} />
                {bands(xs).map((band, index) => (
                    <rect key={rows[index].time} className="chart-hit"
                        x={band.x} y={0} width={band.width} height={CHART.height}
                        onClick={() => onPick(rows[index].time)}>
                        <title>{reading(rows[index])}</title>
                    </rect>
                ))}
            </svg>
            <div className="field-foot">
                <span>{rows[0].time}</span>
                <span>{mixed ? 'Sun / shade / shortest by weather' : 'Route — · direct – –'}</span>
                <span>{rows[rows.length - 1].time}</span>
            </div>

            <label className="depart-select">
                <span className="field-label">Choose a departure</span>
                <select value={now >= 0 ? time : ''}
                    onChange={event => onPick(event.target.value)}>
                    <option value="" disabled>Choose a daylight departure</option>
                    {rows.map(row => <option key={row.time} value={row.time}>{reading(row)}</option>)}
                </select>
            </label>

            <button className="depart-pick" onClick={() => onPick(answer.best.time)}>
                <span>Recommended <strong>{answer.best.time}</strong></span>
                <span className="depart-share">{minutes(answer.best.duration_s)}</span>
            </button>
            <div className="verdict">
                {answer.hasUsefulDetour ? (
                    <>
                        <strong>{Math.round(answer.bestGain * 100)} percentage points</strong>{' '}
                        more {answer.best.preference.mode} than direct
                        {answer.extraDuration_s > 1
                            ? extraMinutes > 0
                                ? <> · <strong>+{extraMinutes} min</strong> walking</>
                                : ' · under 1 min extra walking'
                            : ' · no extra walking'}.
                        {' '}Recommended by exposure gain and extra walking time.
                    </>
                ) : answer.best.preference.mode === 'balanced'
                    ? 'Shortest walk; the conditions do not favor sun or shade.'
                    : 'No useful exposure gain across the day. This is the shortest walk.'}
            </div>
        </div>
    )
}

export default DeparturePlanner
