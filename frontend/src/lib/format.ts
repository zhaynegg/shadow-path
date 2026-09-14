// How numbers are spelled on screen. Two lines, in one place, because the
// summary card and the departure chart quote the same quantities at each other
// -- "71% shade" in a stat row and "71% shade" under a chart have to round the
// same way, or the panel reads as though it is describing two different walks.

export const percent = (fraction: number) => `${Math.round(fraction * 100)}%`

export const metres = (m: number) =>
    (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`)

// Minutes, because that is the unit somebody deciding whether to walk thinks
// in. Rounded to the minute: the pace it comes from is one constant for every
// walker in the city, so a second of precision would be precision about
// nothing. Over an hour it breaks into hours -- the graph reaches 15 km out,
// which is a three-hour walk from the centre at the far edge.
export const minutes = (seconds: number) => {
    const total = Math.round(seconds / 60)
    if (total < 60) return `${total} min`

    const rest = total % 60
    return rest === 0
        ? `${total / 60} h`
        : `${Math.floor(total / 60)} h ${String(rest).padStart(2, '0')}`
}
