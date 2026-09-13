import { useEffect, useState } from 'react'
import { MIN_QUERY, searchPlaces, type Place } from '../../api/geocode'

type SearchBoxProp = {
    onPick: (place: Place) => void,
    // What the next point picked will become. Worth saying out loud: the map's
    // rule -- first pick starts the walk, second finishes it -- is legible when
    // you are clicking pins onto a map and invisible when you are typing.
    next: 'start' | 'destination',
}

// What the last search said, tagged with the query it was about. The same shape
// MapView keeps its route answer in, and it does the same work here: the index
// arrives asynchronously the first time, so an answer can still be in flight
// when the words it belongs to have been edited.
type Answer = { query: string, places?: Place[], message?: string }

export default function SearchBox({ onPick, next }: SearchBoxProp) {
    const [query, setQuery] = useState('')
    const [answer, setAnswer] = useState<Answer | null>(null)

    // Which row the arrow keys are on. -1 is "none yet", so the first ArrowDown
    // lands on the first result rather than the second.
    const [active, setActive] = useState(-1)

    const text = query.trim()
    const long = text.length >= MIN_QUERY

    const answered = answer?.query === text ? answer : null
    const places = answered?.places ?? []

    // Searching as you type, which the previous version could not do. It is not
    // a change of mind about Nominatim's policy -- that forbade exactly this --
    // but a change of what is being searched: a few thousand entries in an
    // array already in memory, on this machine, belonging to nobody else. There
    // is no request to throttle, so there is nothing to make the reader wait for.
    useEffect(() => {
        if (!long) return

        let current = true
        searchPlaces(text)
            .then(found => {
                if (!current) return
                setAnswer({ query: text, places: found })
                setActive(-1)
            })
            .catch((err: Error) => {
                if (current) setAnswer({ query: text, message: err.message })
            })

        // Nothing to abort -- the index load is shared and must not be
        // cancelled -- so a stale answer is simply dropped instead.
        return () => { current = false }
    }, [text, long])

    const pick = (place: Place) => {
        onPick(place)
        // Cleared rather than left sitting in the box: the next thing anybody
        // asks for is the other end of the walk, not this end again.
        setQuery('')
    }

    const onKeyDown = (event: React.KeyboardEvent) => {
        if (event.key === 'Escape') {
            setQuery('')
            return
        }
        if (event.key === 'Enter') {
            // Nothing selected takes the top result, which is the one anybody
            // who typed an exact name is already looking at.
            const chosen = places[active === -1 ? 0 : active]
            if (chosen) {
                event.preventDefault()
                pick(chosen)
            }
            return
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            if (!places.length) return
            event.preventDefault()
            const step = event.key === 'ArrowDown' ? 1 : -1
            setActive(current => (current + step + places.length) % places.length)
        }
    }

    return (
        <div className="search-wrap">
            <svg className="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none"
                stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
                <circle cx="10.5" cy="10.5" r="6.5" />
                <path d="M15.4 15.4 21 21" />
            </svg>

            <input
                className="search-input"
                type="search"
                value={query}
                onChange={event => setQuery(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder={`Search for a ${next}`}
                aria-label={`Search for a ${next} by name or address`}
                role="combobox"
                aria-expanded={long}
                aria-controls="search-results"
                aria-autocomplete="list"
                aria-activedescendant={active >= 0 ? `search-result-${active}` : undefined}
            />

            {long && (answered || places.length > 0) && (
                <div className="search-drop">
                    {answered?.message && <p className="search-note is-error">{answered.message}</p>}

                    {answered?.places && !places.length && (
                        // Not "no results". The index holds only what is inside
                        // the disc the router has a graph for, and saying so is
                        // the difference between a broken box and a limit.
                        <p className="search-note">Nothing by that name within reach of Astana.</p>
                    )}

                    <ul className="search-list" id="search-results" role="listbox">
                        {places.map((place, index) => (
                            <li key={place.id} id={`search-result-${index}`} role="option"
                                aria-selected={index === active}
                                className={`search-hit${index === active ? ' is-active' : ''}`}
                                // mousedown, not click: the input blurs first on
                                // a click, and a blur that closed this list
                                // would take the row out from under the cursor.
                                onMouseDown={event => { event.preventDefault(); pick(place) }}
                                onMouseEnter={() => setActive(index)}>
                                <span className="search-hit-name">{place.label}</span>
                                {place.detail && <span className="search-hit-where">{place.detail}</span>}
                            </li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    )
}
