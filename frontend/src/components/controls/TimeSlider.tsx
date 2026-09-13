type TimeSliderProp = {
    labels: string[],
    value: number,
    onChange: (index: number) => void
}

const toMinutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3))

// The sky over Astana, as a handful of stops. The track is painted with these
// rather than a flat grey because the slider's whole subject is the sun: where
// the track is dark, no shadow exists to look at, and that should be visible
// before you drag into it rather than only after.
const SKY: [number, string][] = [
    [0, '#242844'],    // deep night
    [270, '#2a2f52'],
    [345, '#5f5684'],  // first light
    [420, '#d9905c'],  // sunrise
    [510, '#f7d49c'],
    [720, '#ffecc6'],  // noon
    [930, '#fdd9a2'],
    [1110, '#e08a55'], // sunset
    [1200, '#6a5c85'],
    [1290, '#2b2f52'],
    [1440, '#242844'],
]

const channels = (hex: string) =>
    [1, 3, 5].map(at => parseInt(hex.slice(at, at + 2), 16))

const mix = (from: string, to: string, t: number) => {
    const [a, b] = [channels(from), channels(to)]
    const byte = (i: number) => Math.round(a[i] + (b[i] - a[i]) * t).toString(16).padStart(2, '0')
    return `#${byte(0)}${byte(1)}${byte(2)}`
}

const skyAt = (minutes: number) => {
    const index = SKY.findIndex(([at]) => at >= minutes)
    if (index <= 0) return SKY[Math.max(index, 0)][1]
    const [from, fromColour] = SKY[index - 1]
    const [to, toColour] = SKY[index]
    return mix(fromColour, toColour, (minutes - from) / (to - from))
}

// A stop per slider position, not per hour. The stamps are not evenly spaced in
// time -- they bunch up at dawn and dusk -- so a gradient laid out by the clock
// would drift away from the notches the thumb actually lands on.
const trackGradient = (labels: string[]) => {
    if (labels.length < 2) return skyAt(720)
    const stops = labels.map((label, index) =>
        `${skyAt(toMinutes(label))} ${((index / (labels.length - 1)) * 100).toFixed(2)}%`)
    return `linear-gradient(90deg, ${stops.join(', ')})`
}

const period = (time: string) => {
    const minutes = toMinutes(time)
    if (minutes < 300) return 'night'
    if (minutes < 420) return 'dawn'
    if (minutes < 660) return 'morning'
    if (minutes < 840) return 'midday'
    if (minutes < 1080) return 'afternoon'
    if (minutes < 1230) return 'evening'
    return 'night'
}

function TimeSlider({ labels, value, onChange }: TimeSliderProp) {
    const now = labels[value] ?? '--:--'

    return (
        <div>
            <div className="field-head">
                <span className="field-label">Time of day</span>
                <span className="field-value is-clock">{now}</span>
            </div>
            <input
                type="range" className="range"
                min={0} max={Math.max(labels.length - 1, 0)} step={1} value={value}
                aria-label="Time of day"
                aria-valuetext={now}
                style={{ '--track': trackGradient(labels) } as React.CSSProperties}
                onChange={e => onChange(Number(e.target.value))} />
            <div className="field-foot">
                <span>{labels[0]}</span>
                <span>{period(now)}</span>
                <span>{labels[labels.length - 1]}</span>
            </div>
        </div>
    )
}

export default TimeSlider
