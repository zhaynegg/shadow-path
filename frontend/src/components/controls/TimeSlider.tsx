type TimeSliderProp = {
    labels: string[],
    value: number,
    onChange: (index: number) => void
}

function TimeSlider({ labels, value, onChange }: TimeSliderProp) {
    return (
        <div>
            <input type="range" min={0} max={labels.length - 1}
                step={1} value={value}
                style={{ width: '100%' }}
                onChange={(e) => onChange(Number(e.target.value))} />
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>time of day</span>
                <span>{labels[value]}</span>
            </div>
        </div>
    )
}

export default TimeSlider
