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
            <input type="range" min={MIN_ALPHA} max={MAX_ALPHA}
                step={STEP} value={value}
                style={{ width: '100%' }}
                onChange={(e) => onChange(Number(e.target.value))} />
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>sun ←→ shade</span>
                <span>{describe(value)}</span>
            </div>
        </div>
    )
}

export default ShadeSlider
