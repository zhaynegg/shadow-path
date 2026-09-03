
type TimeSliderProp = {
    labels: string[],
    value: number,
    onChange: (index: number) => void
}
function TimeSlider({labels, value, onChange} : TimeSliderProp){
    return (
        <div style={{position: 'absolute', zIndex: 1, bottom: 16, left: 16}}>
                <input type="range" min={0} max={labels.length - 1}
                step={1} value={value}
                onChange={(e) => onChange(Number(e.target.value))}/>
                <div>
                    <p>{labels[value]}</p>
                </div>
        </div>
    )
}

export default TimeSlider