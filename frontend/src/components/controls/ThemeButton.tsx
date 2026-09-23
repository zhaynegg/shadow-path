import type { Theme } from '../../lib/theme'

type ThemeButtonProps = {
    theme: Theme,
    onChange: (theme: Theme) => void,
}

// Day or Night, in the brand panel. A toggle, so it keeps one name -- "Night
// theme" -- and says whether it is on with aria-pressed, rather than changing
// its name every time it is pressed. The picture shows where a press goes:
// the moon while it is day, the sun while it is night.
export default function ThemeButton({ theme, onChange }: ThemeButtonProps) {
    const night = theme === 'night'

    return (
        <button
            className="btn btn-icon brand-theme"
            onClick={() => onChange(night ? 'light' : 'night')}
            aria-label="Night theme"
            aria-pressed={night}
            // The icon is all anybody sees, so hovering has to say what it does.
            title={night ? 'Switch to the day theme' : 'Switch to the night theme'}>
            {night ? (
                // The sun from the brand mark.
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                    stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="4.2" fill="currentColor" stroke="none" />
                    <path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2
                        M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6" />
                </svg>
            ) : (
                // The moon from the night note.
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M20.7 14.6A8.6 8.6 0 0 1 9.4 3.3a8.6 8.6 0 1 0 11.3 11.3z" />
                </svg>
            )}
        </button>
    )
}
