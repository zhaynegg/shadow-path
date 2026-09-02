import 'maplibre-gl/dist/maplibre-gl.css'
import * as maplibregl from 'maplibre-gl'
import { useRef, useEffect } from 'react'

function MapView(){
    const containerRef = useRef<HTMLDivElement>(null)
    const mapRef = useRef<maplibregl.Map | null>(null)

    useEffect(() => {
        if(!containerRef.current) return

        const map = new maplibregl.Map({
            container: containerRef.current,
            style: {
                version: 8,
                sources: {},
                layers: [
                    {
                        id: 'background',
                        type: 'background',
                        paint: { 'background-color': '#e0e0e0' },
                    },
                ],
            },
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