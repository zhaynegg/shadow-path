import type { ReactNode } from 'react'
import type { RoutePlan } from '../api/client'
import { metres, minutes, percent } from '../lib/format'
import { clockAfter } from '../lib/stamps'

type RouteSummaryProp = {
    plan: RoutePlan | null,
    loading: boolean,
    error: string | null,
    pointCount: number,
    // Signed, so this panel is the one place that knows whether the walk being
    // sold is the shady one or the sunny one. Everything below reads off it.
    alpha: number,
    // The stamp the walk was planned for, which is also when the walker is
    // assumed to set off -- so it is what the arrival clock counts from.
    time: string,
    // The departure chart, which only makes sense once there is a walk to
    // chart. Passed in rather than built here so this card stays what it
    // has always been -- two routes and the difference between them -- and
    // knows nothing about a second endpoint.
    children?: ReactNode,
}

type RowProp = {
    label: string,
    leg: RoutePlan['route'],
    seekingSun: boolean,
    // The route is a solid blue line on the map and the baseline is a grey
    // dashed one. The same two marks appear here, so a row and the line it
    // describes are matched by shape rather than by having to be told.
    mark: 'line' | 'dash',
}

function Row({ label, leg, seekingSun, mark }: RowProp) {
    // The backend only ever measures shade. Sun is the other side of the same
    // number, not a second measurement.
    const share = seekingSun ? 1 - leg.shade_fraction : leg.shade_fraction

    return (
        <div className="stat">
            <span className="stat-name">
                <span className={mark} />
                {label}
            </span>
            <span className="stat-dist">
                {/* Minutes first. "1.65 km" is a number a reader has to
                    convert before it means anything; the conversion is the
                    same one every time, so the panel does it for them. */}
                {minutes(leg.duration_s)}
                <span className="stat-far">{metres(leg.distance_m)}</span>
            </span>
            <span className="stat-bar">
                {/* Both bars are filled in the colour of the thing being
                    measured and drawn to the same scale, so the gap between
                    them is the answer, at a glance. */}
                <span className="bar">
                    <span className="bar-fill" style={{
                        width: `${Math.max(share * 100, 0)}%`,
                        background: seekingSun ? 'var(--sun)' : 'var(--shade)',
                    }} />
                </span>
                <span className="stat-share">
                    {percent(share)} {seekingSun ? 'sun' : 'shade'}
                </span>
            </span>
        </div>
    )
}

function RouteSummary({ plan, loading, error, pointCount, alpha, time, children }: RouteSummaryProp) {
    const seekingSun = alpha < 0
    const wanted = seekingSun ? 'sun' : 'shade'
    const found = seekingSun ? 'sunlit' : 'shaded'
    const more = seekingSun ? 'sunnier' : 'shadier'
    let body

    if (error) {
        body = <div className="summary-error">{error}</div>
    } else if (pointCount === 0) {
        body = (
            <div className="summary-hint">
                {/* Both ways in, named in the order they are reached for:
                    somebody who can see their street clicks it, and somebody
                    who cannot needs to be told the search box is there at all. */}
                <span className="pin pin-a">A</span>
                Click the map, or search, to set a starting point.
            </div>
        )
    } else if (pointCount === 1) {
        body = (
            <div className="summary-hint">
                <span className="pin pin-b">B</span>
                Now pick a destination.
            </div>
        )
    } else if (loading || !plan) {
        body = (
            <div className="summary-hint">
                <span className="spinner" />
                Finding a route…
            </div>
        )
    } else {
        // Deliberately not "2.3x more shade": at midday the direct route is
        // often 0% shaded, and nothing is a useful multiple of zero.
        const longer = plan.route.distance_m / plan.baseline.distance_m - 1
        // Shade gained, or shade given up -- which is sun gained. One
        // subtraction, read in whichever direction the walker asked for.
        const difference = plan.route.shade_fraction - plan.baseline.shade_fraction
        const gained = seekingSun ? -difference : difference

        body = (
            <>
                <Row label={`${found} route`} leg={plan.route}
                    seekingSun={seekingSun} mark="line" />
                <Row label="direct route" leg={plan.baseline}
                    seekingSun={seekingSun} mark="dash" />
                <div className="verdict">
                    {gained < 0.005
                        ? `No ${more} route exists here — both are the same walk.`
                        : (
                            <>
                                <strong>{percent(longer)}</strong> longer, for{' '}
                                <strong>{percent(gained)}</strong> more of the walk in {wanted}.
                            </>
                        )}
                </div>
                {/* The whole point of the minutes above, said as a clock:
                    the map is already showing one moment of one day, and this
                    is the other end of the walk that starts in it. */}
                <div className="arrival">
                    Set off at <strong>{time}</strong> and you are there by{' '}
                    <strong>{clockAfter(time, plan.route.duration_s)}</strong>.
                </div>
                {children}
            </>
        )
    }

    return (
        <div className="panel summary">
            <div className="summary-head">
                <span className="summary-title">Your walk</span>
            </div>
            {body}
        </div>
    )
}

export default RouteSummary
