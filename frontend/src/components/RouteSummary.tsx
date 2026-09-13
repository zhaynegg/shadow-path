import type { RoutePlan } from '../api/client'

type RouteSummaryProp = {
    plan: RoutePlan | null,
    loading: boolean,
    error: string | null,
    pointCount: number,
    // Signed, so this panel is the one place that knows whether the walk being
    // sold is the shady one or the sunny one. Everything below reads off it.
    alpha: number,
}

const percent = (fraction: number) => `${Math.round(fraction * 100)}%`
const metres = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`)

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
            <span className="stat-dist">{metres(leg.distance_m)}</span>
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

function RouteSummary({ plan, loading, error, pointCount, alpha }: RouteSummaryProp) {
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
