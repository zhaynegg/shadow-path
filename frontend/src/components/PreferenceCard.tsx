import type { Preference } from '../api/client'

type Props = { preference: Preference | null, unavailable: boolean }

const REASONS: Record<Preference['reason'], string> = {
    winter: 'More winter sun along the way.',
    summer: 'More shade for summer walks.',
    cold: 'It feels cool outside. Look for a little more sun.',
    warm: 'It feels warm outside. Look for a little more shade.',
    comfortable: 'Mild weather. Take the shortest walk.',
    overcast: 'Cloudy skies. Take the shortest walk.',
    precipitation: 'Rain or snow forecast. Keep the walk short.',
    seasonal_fallback: 'Using the season because a forecast is unavailable for this date.',
}

const MODES: Record<Preference['mode'], string> = {
    sun: 'A little more sun', shade: 'A little more shade', balanced: 'The shortest walk',
}

export default function PreferenceCard({ preference, unavailable }: Props) {
    return (
        <section className="weather-policy" aria-label="Automatic route preference" aria-live="polite">
            <div className="policy-icon" aria-hidden="true">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                    {preference?.mode === 'sun' ? <>
                        <circle cx="12" cy="12" r="4" />
                        <path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M19 5l-1.4 1.4M6.4 17.6 5 19" />
                    </> : preference?.mode === 'shade' ? <>
                        <path d="M4 13a8 8 0 0 1 16 0H4Zm8 0v6a2 2 0 0 0 4 0M12 3v2" />
                    </> : <>
                        <path d="M4 17h15a3 3 0 0 0 0-6 6 6 0 0 0-11.5-1A3.5 3.5 0 0 0 4 17Z" />
                    </>}
                </svg>
            </div>
            <div className="policy-copy">
                <div className="policy-title">{preference ? MODES[preference.mode] : 'Season & weather'}</div>
                <p className="policy-reason">{preference ? REASONS[preference.reason]
                    : unavailable ? 'Conditions are unavailable. Try again shortly.' : 'Checking conditions for your walk…'}</p>
                {preference?.weather && <div className="policy-weather">
                    {Math.round(preference.weather.temperature_c)}°C · Feels like{' '}
                    {Math.round(preference.weather.apparent_temperature_c)}°C ·{' '}
                    {Math.round(preference.weather.cloud_cover_percent)}% cloud
                </div>}
                <div className="policy-source">
                    Chosen automatically
                    {preference?.weather_url && <> · <a href={preference.weather_url}
                        target="_blank" rel="noreferrer">{preference.weather_provider}</a> forecast</>}
                </div>
            </div>
        </section>
    )
}
