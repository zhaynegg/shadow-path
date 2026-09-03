import 'maplibre-gl/dist/maplibre-gl.css'
import * as maplibregl from 'maplibre-gl'
import { useRef, useEffect, useState } from 'react'
import { Protocol } from 'pmtiles'
import { layers, GRAYSCALE } from '@protomaps/basemaps'
import TimeSlider from './controls/TimeSlider'

const protocol = new Protocol({ metadata: true })
const FIXTURES = [
    { label: '08:00', path: '/fixtures/shadows-0800.geojson'},
    { label: '12:00', path: '/fixtures/shadows-1200.geojson'},
    { label: '16:00', path: '/fixtures/shadows-1600.geojson'},
    { label: '19:00', path: '/fixtures/shadows-1900.geojson'},
]
maplibregl.addProtocol('pmtiles', protocol.tile)

function MapView(){
    const containerRef = useRef<HTMLDivElement>(null)
    const mapRef = useRef<maplibregl.Map | null>(null)
    const [index, setIndex] = useState(0)

    useEffect(() => {
        const source = mapRef.current?.getSource('shadows') as maplibregl.GeoJSONSource | undefined
        source?.setData(FIXTURES[index].path)
    }, [index])
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
                        data: '/fixtures/shadows-1200.geojson'
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
            <TimeSlider labels={FIXTURES.map(f => f.label)} value = {index} onChange={setIndex}/>
        </div>
    )
}

export default MapView