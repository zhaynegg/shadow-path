// How numbers are spelled on screen. Two lines, in one place, because the
// summary card and the departure chart quote the same quantities at each other
// -- "71% shade" in a stat row and "71% shade" under a chart have to round the
// same way, or the panel reads as though it is describing two different walks.

export const percent = (fraction: number) => `${Math.round(fraction * 100)}%`

export const metres = (m: number) =>
    (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`)
