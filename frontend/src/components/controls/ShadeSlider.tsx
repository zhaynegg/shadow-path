type ShadeSliderProp = {
    value: number,
    onChange: (alpha: number) => void
}

// Measured on Astana in June: below about 3 the shaded alternatives never pay
// for the detour, so the whole useful range sits above it. See README.
const MAX_ALPHA = 12
const STEP = 0.5

function describe(alpha: number): string {
    if (alpha === 0) return 'shortest path'
    if (alpha < 3) return 'barely prefers shade'
    if (alpha < 7) return 'prefers shade'
    return 'strongly prefers shade'
}

function ShadeSlider({ value, onChange }: ShadeSliderProp) {
    return (
        <div>
            <input type="range" min={0} max={MAX_ALPHA}
                step={STEP} value={value}
                style={{ width: '100%' }}
                onChange={(e) => onChange(Number(e.target.value))} />
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>shade preference</span>
                <span>{describe(value)}</span>
            </div>
        </div>
    )
}

export default ShadeSlider
