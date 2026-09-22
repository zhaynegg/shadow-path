// Where the reader is standing, for anyone who would rather not find themselves
// on a map of a city they may not know by sight.
//
// The position comes from the browser and never from us: navigator.geolocation
// asks the device, the device asks the reader, and this page only ever sees an
// answer somebody has already agreed to give. Three things follow from that,
// and this module exists for all three -- the reader can refuse, the device can
// fail to know, and a fix that succeeds perfectly well can still land somewhere
// this router has no streets for.

import type { LatLon } from '../api/client'

// The point the graph is measured from -- LAT, LON in backend/config.py.
export const CITY: LatLon = [51.1605, 71.4704]

// And how far it reaches: GRAPH_RADIUS, same file. Copied rather than fetched,
// because the whole job of this number here is to answer before a request is
// made. A fix in another oblast is one the backend would refuse anyway, and
// refusing it on this side is the difference between being told how far away
// you are and watching a route fail for reasons nobody explains. If
// GRAPH_RADIUS moves, move this.
export const REACH_M = 15000

// A phone with a GPS fix reports a few metres. A laptop positioned off wifi or
// an IP address can be out by kilometres and reports that in the same field, in
// the same tone -- so past this, *how well* the device knows is part of what the
// reader has to be told. The pin looks equally certain either way.
export const COARSE_M = 100

// Enough for a cold fix indoors; short enough that somebody watching a spinner
// has not already given up by the time it resolves.
const TIMEOUT_MS = 12_000

// A fix from the last minute is still where you are standing. Asking the device
// to establish it again costs seconds and battery for a pin that would not move.
const MAX_AGE_MS = 60_000

const EARTH_M = 6_371_008.8

const radians = (degrees: number) => (degrees * Math.PI) / 180

/** Great-circle metres between two points. */
export const metresBetween = ([aLat, aLon]: LatLon, [bLat, bLon]: LatLon): number => {
    const dLat = radians(bLat - aLat)
    const dLon = radians(bLon - aLon)
    const h = Math.sin(dLat / 2) ** 2
        + Math.cos(radians(aLat)) * Math.cos(radians(bLat)) * Math.sin(dLon / 2) ** 2
    return 2 * EARTH_M * Math.asin(Math.min(1, Math.sqrt(h)))
}

// Distances nobody needs to the metre: an accuracy radius and a distance from
// the city. Deliberately not lib/format.ts's `metres`, which spells 900 km as
// "900.00 km" because it exists to quote route lengths that differ by tens of
// metres. Nothing here is that precise, and printing it as though it were is a
// claim about the fix that the fix does not support.
export const roughly = (m: number) => {
    if (m < 1000) return `${Math.round(m / 10) * 10} m`
    const km = m / 1000
    return `${km < 10 ? km.toFixed(1) : Math.round(km)} km`
}

/** A point the device believes it is at, and the radius it believes that within. */
export type Fix = { point: LatLon, accuracy: number }

/**
 * Why this point cannot be an end of a walk, or null if it can be.
 *
 * Spelled from the constants rather than described in prose, for the reason
 * routing.py gives where it says the same thing on the server: that message
 * read "only covers the city centre" for as long as the graph was a 1.7 km
 * disc, and went on saying it long after the graph was not.
 */
export const outOfReach = (point: LatLon): string | null => {
    const away = metresBetween(CITY, point)
    if (away <= REACH_M) return null
    return `You are ${roughly(away)} from the centre of Astana, and the walking map `
        + `reaches ${REACH_M / 1000} km. Pick a point in the city instead.`
}

// What this needs of navigator.geolocation, which is one method. Named as a
// parameter below so a test can hand over a stub: there is no real device in
// a test runner, and the interesting cases here are the refusals.
type Geo = Pick<Geolocation, 'getCurrentPosition'>

// The codes are a fixed, tiny enum in the spec -- 1 denied, 2 unavailable, 3
// timed out -- and the browser's own `message` for them is written for whoever
// wrote the page, not for whoever is reading it. Every one of these says what
// the reader can do next, because all three are recoverable.
const REFUSED: Record<number, string> = {
    1: 'Location is blocked for this site. Allow it in the browser’s address bar, then try again.',
    2: 'Your device could not work out where it is. Pick your start on the map instead.',
    3: 'Locating took too long. Try again, or pick your start on the map.',
}

/**
 * The device's best idea of where it is.
 *
 * getCurrentPosition is older than promises and takes two callbacks, so this
 * wraps it in one: everything else here awaits, and a single callback API in
 * the middle of that would have to be handled its own way every time it was
 * called. Note what is *not* wrapped -- failure is a routine outcome of this
 * call, not an exception. Somebody saying no is not a bug.
 */
export function locate(geo: Geo | undefined = globalThis.navigator?.geolocation): Promise<Fix> {
    // Missing rather than refusing. The API is handed only to a secure context,
    // so this is what an http:// page gets -- which is every phone that opens
    // the dev server over the LAN, and so the first thing to suspect when the
    // button does nothing on a device where it plainly ought to work.
    if (!geo) {
        return Promise.reject(new Error(
            'This browser will not give a location to an insecure page. Open the site over https.'))
    }

    return new Promise((resolve, reject) => {
        geo.getCurrentPosition(
            position => resolve({
                point: [position.coords.latitude, position.coords.longitude],
                accuracy: position.coords.accuracy,
            }),
            error => reject(new Error(REFUSED[error.code] ?? error.message)),
            { enableHighAccuracy: true, timeout: TIMEOUT_MS, maximumAge: MAX_AGE_MS },
        )
    })
}
