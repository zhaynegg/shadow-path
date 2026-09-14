// The clock the map runs on: which stamps exist, where the slider can stop, and
// what time it is in the city. Pulled out of MapView because none of it touches
// maplibre or React -- it is arithmetic over "HH:MM" strings, and arithmetic
// that has already had a bug in it (see nearestStamp) is worth being able to
// test without standing up a map.
//
// toMinutes was also living in controls/TimeSlider.tsx, one copy each.

import type { ShadowManifest } from '../api/client'

// Every whole hour of the Astana day. The manifest adds the sub-hour stamps on
// top; these are here so the clock can always be moved into the night, which is
// a real state of the world rather than an error.
export const WHOLE_HOURS =
    Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, '0')}:00`)

// Which stamps have a tileset comes from the manifest, because it is a property
// of the date the tiles were built for -- both how long daylight is and how
// finely each hour is cut.
export const isDaylight = (time: string, manifest: ShadowManifest | null) =>
    manifest ? manifest.times.includes(time) : true

// Where the slider can stop. Not a uniform scale, deliberately: the stamps are
// twenty minutes apart at dawn and dusk and an hour apart in the middle of the
// day, so the clock moves in finer steps exactly where the picture changes
// faster. Sorting "HH:MM" as text is chronological.
export const sliderTimes = (manifest: ShadowManifest | null) =>
    manifest ? [...new Set([...WHOLE_HOURS, ...manifest.times])].sort() : WHOLE_HOURS

// The manifest's date, spelled out. "2026-09-13" in a corner reads as a build
// artefact; "13 Sep 2026" reads as the day the sun in front of you belongs to.
export const prettyDate = (iso: string) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString('en-GB',
        { day: 'numeric', month: 'short', year: 'numeric' })

export const toMinutes = (time: string) =>
    Number(time.slice(0, 2)) * 60 + Number(time.slice(3))

// The city's own clock, not the viewer's -- the shadows are Astana's whoever is
// looking at them. To the minute, because the stamps are now finer than an hour.
export const cityMinutes = () => {
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Almaty', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(new Date())
    const part = (type: string) => Number(parts.find(p => p.type === type)?.value ?? 0)
    return part('hour') * 60 + part('minute')
}

// Open on the slider stop nearest the city's clock. Nearest rather than the top
// of the hour, because sub-hour stamps exist exactly where an hour is too coarse
// -- rounding down to one there would throw away the resolution they were built
// for.
//
// This used to choose from the manifest alone, so that the map could never open
// outside daylight: a bare map was held to read as broken rather than as
// nightfall. The fix for that turned out to belong elsewhere -- the panel now
// says the sun is down in words -- and the clamp was left telling a worse lie
// in its place. Opened at 21:25 it showed 18:20, the last stamp of the day,
// with three hours of shadows that had already gone. A clock that quietly
// disagrees with the clock is the harder error to spot, because nothing about
// it looks empty.
export const nearestStamp = (minutes: number, times: string[]) =>
    times.length
        ? times.reduce((best, time) =>
            Math.abs(toMinutes(time) - minutes) < Math.abs(toMinutes(best) - minutes) ? time : best)
        : WHOLE_HOURS[12]

// What the clock says after a walk of this long. Wraps at midnight rather than
// running past it: a late walk that finishes at 00:10 finishes at 00:10, and
// "24:10" is not a time anybody reads.
//
// The date does not come with it, deliberately. Everything on this map belongs
// to one day -- the tiles, the scores, the stamps -- and a walk that crosses
// into the next one is a walk this app has no shadows for either side of.
export const clockAfter = (time: string, seconds: number) => {
    const at = (toMinutes(time) + Math.round(seconds / 60)) % 1440
    return `${String(Math.floor(at / 60)).padStart(2, '0')}:${String(at % 60).padStart(2, '0')}`
}

// Which stamp's shadows the far end of a walk actually happens in, or null when
// that is the one already on screen and there is nothing to say.
//
// The router now plans against the sun as it moves, so a long walk is weighted
// by shadows the map is not drawing: leave at 17:00 and the last kilometre is
// priced at 17:40, because that is when you are on it. The map can only show
// one moment at a time, which is the right thing for a map to do -- so the
// panel says the other one out loud rather than letting the line look wrong.
export const endsIn = (time: string, seconds: number, stamps: string[]) => {
    if (!stamps.length) return null

    const arriving = toMinutes(time) + Math.round(seconds / 60)
    // Past the last stamp there is nothing left to draw: the sun is down, and
    // "18:20's shadows" would be a claim about shadows that have gone.
    if (arriving > toMinutes(stamps[stamps.length - 1])) return 'dark'

    const landed = nearestStamp(arriving, stamps)
    return landed === time ? null : landed
}
