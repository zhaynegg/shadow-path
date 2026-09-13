type ShadeSliderProp = {
    value: number,
    onChange: (alpha: number) => void
}

// Measured on Astana in June: below about 3 the alternatives never pay for the
// detour, so the whole useful range sits above it. See README.
const MAX_ALPHA = 12
const STEP = 0.5

// Signed, and the negative half is not a novelty: Astana is one of the coldest
// capitals on earth, and for half the year the walk you want is the sunny one.
const MIN_ALPHA = -MAX_ALPHA

// Amber on the left, indigo on the right, and the map's own two colours at the
// ends. Pale through the middle, because the middle is where the preference
// stops mattering and the walk is simply the short one.
const TRACK = 'linear-gradient(90deg, #e8963c 0%, #f0c894 32%, #ded9d2 50%,'
    + ' #9a9ac4 68%, #3b3b6d 100%)'

function describe(alpha: number): string {
    if (alpha === 0) return 'shortest path'

    const want = alpha > 0 ? 'shade' : 'sun'
    const strength = Math.abs(alpha)
    if (strength < 3) return `barely prefers ${want}`
    if (strength < 7) return `prefers ${want}`
    return `strongly prefers ${want}`
}

function ShadeSlider({ value, onChange }: ShadeSliderProp) {
    return (
        <div>
            <div className="field-head">
                <span className="field-label">Preference</span>
                <span className="field-value">{describe(value)}</span>
            </div>
            <div className="range-wrap">
                <input
                    type="range" className="range"
                    min={MIN_ALPHA} max={MAX_ALPHA} step={STEP} value={value}
                    aria-label="Sun or shade preference"
                    aria-valuetext={describe(value)}
                    style={{ '--track': TRACK } as React.CSSProperties}
                    onChange={e => onChange(Number(e.target.value))} />
                {/* Marks alpha 0. Worth finding by eye: it is the only setting
                    that asks the router for nothing at all. */}
                <span className="range-detent" />
            </div>
            <div className="field-foot">
                <span>seek sun</span>
                <span>seek shade</span>
            </div>
        </div>
    )
}

export default ShadeSlider
