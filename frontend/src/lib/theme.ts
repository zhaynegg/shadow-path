import { useEffect, useState } from 'react'

// Day or Night. The ids are the values of data-theme on <html>, which is what
// index.css keys its Night block on -- so the attribute is the one place the
// current theme lives, and everything here reads and writes that.
export type Theme = 'light' | 'night'

// What the reader chose, if they chose. The script at the top of index.html
// reads the same key before the page first paints, so the two must agree.
const KEY = 'shadow-path-theme'

const DARK = '(prefers-color-scheme: dark)'

// The browser's colour for its own toolbar on phones. The map's ground, so
// the chrome and the map run into each other without a seam.
const TOOLBAR: Record<Theme, string> = { light: '#cccccc', night: '#141414' }

/**
 * The theme to open on: a choice the reader made wins, and without one the
 * page follows the device. Anything else in storage -- an old value, a value
 * from some other version of this page -- counts as no choice at all.
 */
export const pickTheme = (stored: string | null, prefersDark: boolean): Theme =>
    stored === 'light' || stored === 'night' ? stored : (prefersDark ? 'night' : 'light')

const deviceTheme = (): Theme => (matchMedia(DARK).matches ? 'night' : 'light')

// localStorage throws rather than returning null in some private windows, and
// a theme is not worth a crash. Both sides fail quietly to "no choice".
const stored = (): string | null => {
    try {
        return localStorage.getItem(KEY)
    } catch {
        return null
    }
}

const remember = (theme: Theme | null) => {
    try {
        if (theme) localStorage.setItem(KEY, theme)
        else localStorage.removeItem(KEY)
    } catch {
        // Nothing to do: the theme still changes, it just is not remembered.
    }
}

/** What the page is showing right now. */
export const currentTheme = (): Theme =>
    document.documentElement.dataset.theme === 'night' ? 'night' : 'light'

const apply = (theme: Theme) => {
    document.documentElement.dataset.theme = theme
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', TOOLBAR[theme])
}

/**
 * The current theme and a way to change it.
 *
 * A choice that matches the device is stored as no choice. That is the whole
 * of the "follow my device" setting: switch to what the phone is already doing
 * and the page goes back to following it, so nobody is left pinned to Day
 * after flipping it twice.
 */
export function useTheme(): [Theme, (theme: Theme) => void] {
    const [theme, setTheme] = useState(currentTheme)

    // The device can change under the page -- a phone that goes dark at
    // sunset. Followed only while the reader has not said otherwise.
    useEffect(() => {
        const query = matchMedia(DARK)
        const follow = () => {
            if (stored()) return
            const next = pickTheme(null, query.matches)
            apply(next)
            setTheme(next)
        }
        query.addEventListener('change', follow)
        return () => query.removeEventListener('change', follow)
    }, [])

    const choose = (next: Theme) => {
        remember(next === deviceTheme() ? null : next)
        apply(next)
        setTheme(next)
    }

    return [theme, choose]
}
