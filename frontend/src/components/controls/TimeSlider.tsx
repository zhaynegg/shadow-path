import { toMinutes } from '../../lib/stamps'

type TimeSliderProp = {
    labels: string[],
    value: number,
    onChange: (index: number) => void
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
                <span className="field-label">Departure time</span>
                <span className="field-value is-clock">{now}</span>
            </div>
            <input
                type="range" className="range"
                min={0} max={Math.max(labels.length - 1, 0)} step={1} value={value}
                aria-label="Departure time"
                aria-valuetext={now}
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
