import 'maplibre-gl/dist/maplibre-gl.css'
import * as maplibregl from 'maplibre-gl'
import { useRef, useEffect, useState } from 'react'
import { Protocol } from 'pmtiles'
import { layers, GRAYSCALE } from '@protomaps/basemaps'
import TimeSlider from './controls/TimeSlider'
import ShadeSlider from './controls/ShadeSlider'
import RouteSummary from './RouteSummary'
import { fetchRoute, type LatLon, type LineGeometry, type RoutePlan } from '../api/client'

const protocol = new Protocol({ metadata: true })

// Every local hour the backend will answer for. Vite proxies /api to the
// backend in dev (see vite.config.ts), so this stays same-origin.
const HOURS = Array.from({ length: 24 }, (_, hour) => hour)
const HOUR_LABELS = HOURS.map(hour => `${String(hour).padStart(2, '0')}:00`)
const INITIAL_HOUR = 12
const INITIAL_ALPHA = 6

// Dragging a slider crosses many values on the way to the one you want, and
// each is a real computation on the server. Wait for the drag to settle.
const DEBOUNCE_MS = 150

const ROUTE_COLOUR = '#2563eb'
const BASELINE_COLOUR = '#9ca3af'

const EMPTY = { type: 'FeatureCollection' as const, features: [] }
const shadowUrl = (hour: number) => `/api/shadows?hour=${hour}`

// A source wants a FeatureCollection; a route is a bare geometry until wrapped.
const asFeature = (geometry: LineGeometry | undefined) =>
    geometry
        ? { type: 'FeatureCollection' as const, features: [{ type: 'Feature' as const, geometry, properties: {} }] }
        : EMPTY

maplibregl.addProtocol('pmtiles', protocol.tile)

function MapView() {
    const containerRef = useRef<HTMLDivElement>(null)
    const mapRef = useRef<maplibregl.Map | null>(null)

    const [hour, setHour] = useState(INITIAL_HOUR)
    const [alpha, setAlpha] = useState(INITIAL_ALPHA)
    const [points, setPoints] = useState<LatLon[]>([])
    const [plan, setPlan] = useState<RoutePlan | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)

    // --- shadows follow the clock ------------------------------------------
    useEffect(() => {
        const timer = setTimeout(() => {
            const source = mapRef.current?.getSource('shadows') as maplibregl.GeoJSONSource | undefined
            source?.setData(shadowUrl(hour))
        }, DEBOUNCE_MS)

        // Runs before the next effect and on unmount: a pending fetch for an
        // hour the user has already scrolled past is cancelled here.
        return () => clearTimeout(timer)
    }, [hour])

    // --- ask the backend for a route ---------------------------------------
    useEffect(() => {
        if (points.length < 2) {
            setPlan(null)
            setError(null)
            return
        }

        const controller = new AbortController()
        const timer = setTimeout(() => {
            setLoading(true)
            fetchRoute({ origin: points[0], destination: points[1], hour, alpha }, controller.signal)
                .then(result => {
                    setPlan(result)
                    setError(null)
                })
                .catch((err: Error) => {
                    // An abort is us cancelling on purpose, not a failure.
                    if (err.name !== 'AbortError') setError(err.message)
                })
                .finally(() => {
                    if (!controller.signal.aborted) setLoading(false)
                })
        }, DEBOUNCE_MS)

        // abort() cancels a request already in flight, so a slow answer for an
        // old hour can never arrive after a fast answer for the current one.
        return () => {
            clearTimeout(timer)
            controller.abort()
        }
    }, [points, hour, alpha])

    // --- draw whatever came back -------------------------------------------
    useEffect(() => {
        const map = mapRef.current
        if (!map) return
        ;(map.getSource('route') as maplibregl.GeoJSONSource | undefined)?.setData(asFeature(plan?.route.geometry))
        ;(map.getSource('baseline') as maplibregl.GeoJSONSource | undefined)?.setData(asFeature(plan?.baseline.geometry))
    }, [plan])

    // --- markers for the two clicked points --------------------------------
    useEffect(() => {
        const map = mapRef.current
        if (!map) return

        const markers = points.map(([lat, lon], index) =>
            new maplibregl.Marker({ color: index === 0 ? '#15803d' : '#b91c1c' })
                .setLngLat([lon, lat])
                .addTo(map))

        return () => markers.forEach(marker => marker.remove())
    }, [points])

    // --- build the map once ------------------------------------------------
    useEffect(() => {
        if (!containerRef.current) return

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
                    baseline: { type: 'geojson', data: EMPTY },
                    route: { type: 'geojson', data: EMPTY },
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
                    // Baseline under the route: where they overlap, the shaded
                    // route should be the one you see.
                    {
                        id: 'baseline-line',
                        type: 'line',
                        source: 'baseline',
                        paint: {
                            'line-color': BASELINE_COLOUR,
                            'line-width': 3,
                            'line-dasharray': [2, 2],
                        },
                    },
                    {
                        id: 'route-line',
                        type: 'line',
                        source: 'route',
                        layout: { 'line-cap': 'round', 'line-join': 'round' },
                        paint: {
                            'line-color': ROUTE_COLOUR,
                            'line-width': 5,
                            'line-opacity': 0.9,
                        },
                    },
                    ...layers('protomaps', GRAYSCALE, { labelsOnly: true, lang: 'en' }),
                ],
                glyphs: 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf',
            },
            center: [71.4704, 51.1605],
            zoom: 11,
            maxBounds: [[70.37, 50.77], [72.39, 51.48]],
        })
        mapRef.current = map

        // First click sets the start, second the destination, third starts over.
        // The updater form is essential: this handler is registered once and
        // would otherwise capture the empty array it saw at mount forever.
        map.on('click', event => {
            const { lat, lng } = event.lngLat
            setPoints(previous => (previous.length >= 2 ? [[lat, lng]] : [...previous, [lat, lng]]))
        })
        map.getCanvas().style.cursor = 'crosshair'

        // The container gets its height from CSS, which can resolve after the
        // map is constructed -- maplibre then keeps its 400x300 fallback canvas
        // inside a full-size box. Watching the element makes it catch up.
        const observer = new ResizeObserver(() => map.resize())
        observer.observe(containerRef.current)

        // The observer only fires on a *change*. If the box was already correct
        // when maplibre measured it wrong, nothing would ever wake it up.
        map.once('load', () => map.resize())

        return () => {
            observer.disconnect()
            map.remove()
            mapRef.current = null
        }
    }, []) // [] means "run this once, when the component first appears."

    return (
        <div style={{ position: 'relative', height: '100vh' }}>
            <div style={{ height: '100%' }} ref={containerRef} />

            <RouteSummary plan={plan} loading={loading} error={error} pointCount={points.length} />

            <div style={{
                position: 'absolute', zIndex: 1, bottom: 16, left: 16, width: 260,
                padding: '12px 14px', borderRadius: 8, background: 'rgba(255,255,255,0.94)',
                boxShadow: '0 1px 4px rgba(0,0,0,0.25)', fontSize: 13, lineHeight: 1.4,
                display: 'flex', flexDirection: 'column', gap: 10,
            }}>
                <TimeSlider labels={HOUR_LABELS} value={hour} onChange={setHour} />
                <ShadeSlider value={alpha} onChange={setAlpha} />
                <button
                    onClick={() => setPoints([])}
                    disabled={points.length === 0}
                    style={{ padding: '4px 8px', fontSize: 12, cursor: points.length ? 'pointer' : 'default' }}>
                    Clear points
                </button>
            </div>
        </div>
    )
}

export default MapView
