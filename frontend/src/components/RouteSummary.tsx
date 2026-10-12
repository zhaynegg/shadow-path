import type { ReactNode } from 'react'
import type { Preference, RoutePlan } from '../api/client'
import { metres, minutes, percent } from '../lib/format'
import { clockAfter, endsIn } from '../lib/stamps'

type RouteSummaryProp = {
    plan: RoutePlan | null,
    preference?: Preference | null,
    loading: boolean,
    error: string | null,
    pointCount: number,
    time: string,
    stamps: string[],
    children?: ReactNode,
}

type RowProp = {
    label: string,
    leg: RoutePlan['route'],
    mode: Preference['mode'],
    mark: 'line' | 'dash',
}

function Row({ label, leg, mode, mark }: RowProp) {
    const share = mode === 'sun' ? 1 - leg.shade_fraction : leg.shade_fraction

    return (
        <div className="stat">
            <span className="stat-name">
                <span className={mark} aria-hidden="true" />
                {label}
            </span>
            <span className="stat-dist">
                {minutes(leg.duration_s)}
                <span className="stat-far">{metres(leg.distance_m)}</span>
            </span>
            {mode !== 'balanced' && (
                <span className="stat-bar">
                    <span className="bar" aria-hidden="true">
                        <span className="bar-fill" style={{
                            width: `${Math.min(Math.max(share * 100, 0), 100)}%`,
                            background: mode === 'sun' ? 'var(--sun)' : 'var(--shade)',
                        }} />
                    </span>
                    <span className="stat-share">{percent(share)} {mode}</span>
                </span>
            )}
        </div>
    )
}

function RouteSummary({ plan, preference, loading, error, pointCount, time, stamps,
    children }: RouteSummaryProp) {
    const policy = plan?.preference ?? preference
    const mode = policy?.mode ?? 'balanced'
    const modeLabel = mode === 'sun' ? 'More sun' : mode === 'shade' ? 'More shade' : 'Shortest route'
    let body

    if (error) {
        body = <div className="summary-error" role="alert">{error}</div>
    } else if (pointCount === 0) {
        body = (
            <div className="summary-hint">
                <span className="pin pin-a" aria-hidden="true">A</span>
                Click the map or search for your starting point.
            </div>
        )
    } else if (pointCount === 1) {
        body = (
            <div className="summary-hint">
                <span className="pin pin-b" aria-hidden="true">B</span>
                Pick a destination.
            </div>
        )
    } else if (loading || !plan) {
        body = (
            <div className="summary-hint" role="status">
                <span className="spinner" aria-hidden="true" />
                Finding your route…
            </div>
        )
    } else {
        const difference = plan.route.shade_fraction - plan.baseline.shade_fraction
        const gained = mode === 'sun' ? -difference : difference
        const extraSeconds = Math.max(plan.route.duration_s - plan.baseline.duration_s, 0)
        const extraMinutes = Math.round(extraSeconds / 60)
        const lands = endsIn(time, plan.route.duration_s, stamps)

        body = (
            <>
                <div className="summary-primary">
                    <strong className="summary-duration">{minutes(plan.route.duration_s)}</strong>
                    <span className={`summary-mode mode-${mode}`}>
                        {modeLabel}
                    </span>
                </div>
                <div className="summary-meta">
                    {metres(plan.route.distance_m)} · Arrive at{' '}
                    <strong>{clockAfter(time, plan.route.duration_s)}</strong>
                </div>

                <Row label={mode === 'balanced' ? 'Shortest route' : 'Your route'}
                    leg={plan.route} mode={mode} mark="line" />
                {mode !== 'balanced' && (
                    <>
                        <Row label="Direct route" leg={plan.baseline} mode={mode} mark="dash" />
                        <div className="verdict">
                            {gained < 0.005
                                ? 'No extra exposure benefit at this departure.'
                                : (
                                    <>
                                        <strong>{Math.round(gained * 100)} percentage points</strong>{' '}
                                        more {mode}{extraSeconds > 1
                                            ? extraMinutes > 0
                                                ? <> · <strong>+{extraMinutes} min</strong> walking</>
                                                : ' · under 1 min extra walking'
                                            : ' · no extra walking time'}.
                                    </>
                                )}
                        </div>
                    </>
                )}
                {mode === 'balanced' && <div className="verdict">Taking the shortest walk.</div>}
                {lands && mode !== 'balanced' && (
                    <div className="arrival drift">
                        {lands === 'dark'
                            ? 'You arrive after daylight ends.'
                            : <>Shadows change during this walk; arrival is closest to{' '}
                                <strong>{lands}</strong>.</>}
                    </div>
                )}
                {children}
            </>
        )
    }

    return (
        <div className="summary">
            <div className="summary-head">
                <span className="summary-title">Your walk</span>
            </div>
            {body}
        </div>
    )
}

export default RouteSummary
