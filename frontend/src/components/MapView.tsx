import 'maplibre-gl/dist/maplibre-gl.css'
import * as maplibregl from 'maplibre-gl'
import { useRef, useEffect, useState } from 'react'
import { Protocol } from 'pmtiles'
import { layers, GRAYSCALE } from '@protomaps/basemaps'
import TimeSlider from './controls/TimeSlider'

const protocol = new Protocol({ metadata: true })

// Every local hour the backend will answer for. Vite proxies /api to the
// backend in dev (see vite.config.ts), so this stays same-origin.
const HOURS = Array.from({ length: 24 }, (_, hour) => hour)
const HOUR_LABELS = HOURS.map(hour => `${String(hour).padStart(2, '0')}:00`)
const INITIAL_HOUR = 12

// Dragging the slider crosses many hours on the way to the one you want, and
// each is a real computation on the server. Wait for the drag to settle.
const DEBOUNCE_MS = 150

const shadowUrl = (hour: number) => `/api/shadows?hour=${hour}`
maplibregl.addProtocol('pmtiles', protocol.tile)

function MapView(){
    const containerRef = useRef<HTMLDivElement>(null)
    const mapRef = useRef<maplibregl.Map | null>(null)
    const [hour, setHour] = useState(INITIAL_HOUR)

    useEffect(() => {
        const timer = setTimeout(() => {
            const source = mapRef.current?.getSource('shadows') as maplibregl.GeoJSONSource | undefined
            source?.setData(shadowUrl(hour))
        }, DEBOUNCE_MS)

        // Runs before the next effect and on unmount: a pending fetch for an
        // hour the user has already scrolled past is cancelled here.
        return () => clearTimeout(timer)
    }, [hour])
    useEffect(() => {
        if(!containerRef.current) return

        const map = new maplibregl.Map({
            container: containerRef.current,
            style: {
                version: 8,
                sources: {
                    protomaps: {
                        type: 'vector',
                        url: 'pmtiles:///my_area.pmtiles',
                    },
                    shadows: {
                        type: 'geojson',
                        data: shadowUrl(INITIAL_HOUR),
                    },
                },
                layers: [
                    ...layers('protomaps', GRAYSCALE),
                    {
                        id: 'shadow',
                        type: 'fill',
                        source: 'shadows',
                        paint: {
                            'fill-color': '#4a4a68',
                            'fill-opacity': 0.3,
                        },
                    },
                    ...layers('protomaps', GRAYSCALE, { labelsOnly: true, lang: 'en'}),
                ],
                glyphs: 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf',
            },
            center: [71.4704, 51.1605],
            zoom: 11,
            maxBounds: [[70.37, 50.77], [72.39, 51.48]],
        })
        mapRef.current = map

        return () =>{
            map.remove()
            mapRef.current = null
        }
    }, []) // [] means "run this once, when the component first appears."
    return (
        <div style={{position: 'relative', height: '100vh'}}>
            <div style={{height: '100%'}} ref={containerRef}>

            </div>
            <TimeSlider labels={HOUR_LABELS} value={hour} onChange={setHour}/>
        </div>
    )
}

export default MapView