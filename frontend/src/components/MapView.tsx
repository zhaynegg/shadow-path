import 'maplibre-gl/dist/maplibre-gl.css'
import * as maplibregl from 'maplibre-gl'
import { useRef, useEffect } from 'react'
import { Protocol } from 'pmtiles'


const protocol = new Protocol({ metadata: true })
maplibregl.addProtocol('pmtiles', protocol.tile)

function MapView(){
    const containerRef = useRef<HTMLDivElement>(null)
    const mapRef = useRef<maplibregl.Map | null>(null)
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
                    }
                },
                layers: [
                    {
                        id: 'background',
                        type: 'background',
                        paint: { 'background-color': '#e0e0e0' },
                    },
                    {
                        id: 'water',
                        type: 'fill',
                        source: 'protomaps',
                        paint: { 'fill-color': '#a0c8f0' },
                        'source-layer': 'water',
                    },
                    {
                        id: 'water-lines',
                        type: 'line',
                        source: 'protomaps',
                        paint: { 'line-color': '#a0c8f0', 'line-width': 2 },
                        'source-layer': 'water',
                    },

                ],
            },
            center: [71.4704, 51.1605],
            zoom: 11,
        })
        mapRef.current = map

        return () =>{
            map.remove()
            mapRef.current = null
        }
    }, []) // [] means "run this once, when the component first appears."
    return (
        <div style={{'backgroundColor': "grey", 'height': "100vh"}} ref={containerRef}>
            
        </div>
    )
}

export default MapView