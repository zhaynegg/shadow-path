import { useState } from 'react'
import { locate, type Fix } from '../../lib/locate'

type LocateButtonProps = {
    onFix: (fix: Fix) => void,
    // Failure is routine here -- a refusal, a device that cannot tell, a page
    // served over http -- so it is handed up rather than thrown. The dock says
    // it, in the same place it says a missing manifest and a sun that is down.
    onFail: (message: string) => void,
    // Which end of the walk this would set, for the same reason SearchBox is
    // told: the map's rule -- first pick starts, second finishes -- is legible
    // while you are dropping pins and invisible from a button in the corner.
    next: 'start' | 'destination',
}

export default function LocateButton({ onFix, onFail, next }: LocateButtonProps) {
    const [busy, setBusy] = useState(false)

    const ask = () => {
        // The browser may have to wake a radio, and a second click while it is
        // doing that opens a second prompt over the first one.
        if (busy) return
        setBusy(true)

        locate()
            .then(onFix)
            .catch((error: Error) => onFail(error.message))
            .finally(() => setBusy(false))
    }

    const what = `Use my location as the ${next}`

    return (
        <button
            className={`btn btn-icon${busy ? ' is-busy' : ''}`}
            onClick={ask}
            disabled={busy}
            // The button is an icon, so the name it is read out by and the name
            // it shows on hover both have to be written here.
            title={busy ? 'Locating…' : what}
            aria-label={what}
            aria-busy={busy}>
            {/* A crosshair with a dot in it: the same mark every map in the
                world uses for this, which is worth more than any icon of our
                own would be. The ring spins while the device is thinking. */}
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <circle className="locate-ring" cx="12" cy="12" r="7"
                    strokeDasharray={busy ? '11 33' : undefined} />
                <circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none" />
                <path d="M12 1.6v3.1M12 19.3v3.1M1.6 12h3.1M19.3 12h3.1" />
            </svg>
        </button>
    )
}
