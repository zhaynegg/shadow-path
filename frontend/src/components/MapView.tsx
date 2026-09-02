import 'maplibre-gl/dist/maplibre-gl.css'
import * as maplibregl from 'maplibre-gl'
import { useRef, useEffect } from 'react'
import { Protocol } from 'pmtiles'
import { layers, GRAYSCALE } from '@protomaps/basemaps'

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
                layers: layers('protomaps', GRAYSCALE, {lang: 'en'}),
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
        <div style={{'backgroundColor': "grey", 'height': "100vh"}} ref={containerRef}>
            
        </div>
    )
}

export default MapView