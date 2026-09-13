// Turning a place name into a point, for anyone who does not already know where
// they are on a map of Astana.
//
// This used to ask nominatim.openstreetmap.org. It no longer asks anybody: the
// whole searchable city is one static file built by
// scripts/export_search_index.py and served from the same origin as the tiles.
//
// The reason is not only the usage policy that forbade searching as you type.
// It is that Nominatim answers from live OSM while this router answers from a
// pinned snapshot -- astana_buildings.parquet and a cached graph -- so the two
// could disagree, and the geocoder could hand back a street the graph had never
// heard of. An index built from the graph agrees with it by construction.
//
// Three sources go in: street names off the routing graph, named buildings, and
// the ~5,000 named places that are not buildings. Measuring decided that: an
// index of building address tags alone reached 36% of named walkable metres.

import type { LatLon } from './client'

const INDEX_URL = '/search-index.json'

// Two characters is enough now that matching costs a scan of an array already
// in memory. It was three when every query was somebody else's server.
export const MIN_QUERY = 2

// More than fits the panel without scrolling is more than anybody reads.
const LIMIT = 7

// How a name is written, folded, and where it is. Short keys because there are
// nearly eight thousand of them: `c` is the name as written, `l` the same in
// Latin, and both already hold every spelling OSM knows -- name:en, name:kk,
// name:ru, alt_name -- so "Supreme Court" finds Жоғарғы сот.
type Entry = { n: string, d: string, p: [number, number], c: string, l: string }

// The folding rules travel inside the index rather than living here. A query
// has to be folded exactly as the keys were, and a second copy of these tables
// would be right only until somebody edited one of them.
type Fold = {
    chars: Record<string, string>
    strip: string[]
    translit: Record<string, string>
    collapse: [string, string][]
}

type Index = { entries: Entry[], fold: Fold, generated_at: string }

export type Place = {
    id: number
    label: string
    // What kind of thing it is, and the street it stands on where OSM knows --
    // which is how two shops of the same name are told apart, and in a city of
    // chains that is most of them.
    detail: string
    point: LatLon
}

const PUNCT = /[^\p{L}\p{N}\s]/gu

/** A name or a query reduced to letters, in whatever script it was written. */
export const fold = (text: string, rules: Fold) => {
    let s = [...text.toLowerCase()].map(ch => rules.chars[ch] ?? ch).join('')
    for (const word of rules.strip) s = s.split(word).join(' ')
    return s.replace(PUNCT, ' ').split(/\s+/).filter(Boolean).join(' ')
}

/** The same, folded into Latin, so the two scripts meet in one place. */
export const latinise = (text: string, rules: Fold) => {
    let s = [...fold(text, rules)].map(ch => rules.translit[ch] ?? ch).join('')
    for (const [from, to] of rules.collapse) s = s.split(from).join(to)
    return s.split(/\s+/).filter(Boolean).join(' ')
}

// Matched at the start of the name or of any word in it, ahead of matched
// somewhere in the middle: typing "ab" should offer Абай before Кабанбай.
const startsWord = (key: string, query: string) =>
    key.startsWith(query) || key.includes(` ${query}`)

export const rank = (index: Index, query: string): Place[] => {
    const c = fold(query, index.fold)
    const l = latinise(query, index.fold)
    if (!c && !l) return []

    const hits: { entry: Entry, at: number, lead: boolean }[] = []
    index.entries.forEach((entry, at) => {
        const byScript = c && entry.c.includes(c)
        const byLatin = l && entry.l.includes(l)
        if (!byScript && !byLatin) return
        hits.push({
            entry, at,
            lead: (!!byScript && startsWord(entry.c, c)) || (!!byLatin && startsWord(entry.l, l)),
        })
    })

    hits.sort((a, b) =>
        // A leading match first, then the shortest name -- a query that is
        // nearly the whole of a name is likelier to be about that place than
        // about a longer one that merely contains it.
        Number(b.lead) - Number(a.lead)
        || a.entry.n.length - b.entry.n.length
        || a.at - b.at)

    return hits.slice(0, LIMIT).map(({ entry, at }) => ({
        id: at,
        label: entry.n,
        detail: entry.d,
        point: [entry.p[0], entry.p[1]],
    }))
}

// Fetched once and kept. The file is a few hundred kilobytes and never changes
// between reloads, so the first search pays for it and no other one does.
let pending: Promise<Index> | null = null

// Deliberately takes no AbortSignal. The load is shared by every search there
// will ever be, so cancelling it on behalf of one of them is never right --
// wired to a keystroke it would abort on the next keystroke, every time, and
// the index would never finish arriving at all. Staleness is handled where it
// belongs, by the caller ignoring an answer to a query since edited.
export const loadIndex = (): Promise<Index> => {
    pending ??= fetch(INDEX_URL).then(response => {
        // A dev server answers an unknown path with index.html and a cheerful
        // 200, so a missing index arrives as HTML rather than as a 404 -- the
        // same trap fetchShadowManifest documents.
        const isJson = response.headers.get('content-type')?.includes('application/json')
        if (!response.ok || !isJson) {
            throw new Error(`No search index at ${INDEX_URL} (${response.status})`
                + ' -- run backend/scripts/export_search_index.py')
        }
        return response.json() as Promise<Index>
    }).catch(error => {
        // Never cache the failure: a reload of the page is not required to fix
        // a file that was simply not built yet.
        pending = null
        throw error
    })
    return pending
}

export async function searchPlaces(query: string): Promise<Place[]> {
    return rank(await loadIndex(), query)
}
