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

type RowProp = { label: string, leg: RoutePlan['route'], colour: string, seekingSun: boolean }

function Row({ label, leg, colour, seekingSun }: RowProp) {
    // The backend only ever measures shade. Sun is the other side of the same
    // number, not a second measurement.
    const share = seekingSun ? 1 - leg.shade_fraction : leg.shade_fraction

    return (
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginTop: 6 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: colour, flexShrink: 0 }} />
            <span style={{ flex: 1 }}>{label}</span>
            <span style={{ fontVariantNumeric: 'tabular-nums' }}>{metres(leg.distance_m)}</span>
            <span style={{ fontVariantNumeric: 'tabular-nums', width: 58, textAlign: 'right' }}>
                {percent(share)} {seekingSun ? 'sun' : 'shade'}
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
        body = <span style={{ color: '#c0392b' }}>{error}</span>
    } else if (pointCount === 0) {
        body = <span>Click the map to set a starting point.</span>
    } else if (pointCount === 1) {
        body = <span>Now click a destination.</span>
    } else if (loading || !plan) {
        body = <span>Finding a route…</span>
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
                <Row label={`${found} route`}
                    leg={plan.route} colour="#2563eb" seekingSun={seekingSun} />
                <Row label="direct route" leg={plan.baseline} colour="#9ca3af"
                    seekingSun={seekingSun} />
                <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid #e5e7eb' }}>
                    {gained < 0.005
                        ? `No ${more} route exists here — both are the same walk.`
                        : `${percent(longer)} longer, ${percent(gained)} more of the walk in ${wanted}.`}
                </div>
            </>
        )
    }

    return (
        <div style={{
            position: 'absolute', zIndex: 1, top: 16, right: 16, width: 300,
            padding: '12px 14px', borderRadius: 8, background: 'rgba(255,255,255,0.94)',
            boxShadow: '0 1px 4px rgba(0,0,0,0.25)', fontSize: 13, lineHeight: 1.4,
        }}>
            {body}
        </div>
    )
}

export default RouteSummary
