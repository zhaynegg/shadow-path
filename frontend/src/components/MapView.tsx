import 'maplibre-gl/dist/maplibre-gl.css'
import * as maplibregl from 'maplibre-gl'
import { useRef, useEffect, useState } from 'react'
import { Protocol } from 'pmtiles'
import { layers, GRAYSCALE } from '@protomaps/basemaps'
import TimeSlider from './controls/TimeSlider'
import ShadeSlider from './controls/ShadeSlider'
import RouteSummary from './RouteSummary'
import {
    fetchRoute, fetchShadowManifest,
    type LatLon, type LineGeometry, type RoutePlan, type ShadowManifest,
} from '../api/client'

const protocol = new Protocol({ metadata: true })

// Every hour of the Astana day the backend will answer for. Vite proxies /api
// backend in dev (see vite.config.ts), so this stays same-origin.
const HOURS = Array.from({ length: 24 }, (_, hour) => hour)
const HOUR_LABELS = HOURS.map(hour => `${String(hour).padStart(2, '0')}:00`)
const INITIAL_ALPHA = 6
const INITIAL_ZOOM = 14

// Dragging a slider crosses many values on the way to the one you want. Each
// would re-plan a route on the server and re-point the shadow layer. Wait for
// the drag to settle.
const DEBOUNCE_MS = 150

const ROUTE_COLOUR = '#2563eb'
const BASELINE_COLOUR = '#9ca3af'

const EMPTY = { type: 'FeatureCollection' as const, features: [] }

// Which hours have a tileset comes from the manifest, because it is a property
// of the date the tiles were built for -- 16 hours in June, 8 in December. The
// slider still runs the whole 24, so it can always be moved somewhere the sun
// is down; that is a real state of the world, not an error.
const isDaylight = (hour: number, manifest: ShadowManifest | null) =>
    manifest ? manifest.hours.includes(hour) : true

// The city's own clock, not the viewer's -- the shadows are Astana's whoever is
// looking at them.
const cityHour = () => Number(new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Almaty',
    hour: 'numeric',
    hourCycle: 'h23',
}).format(new Date()))

// Open inside daylight: land outside it and the map opens bare, which reads as
// broken rather than as nightfall. `hours` is sorted, so the ends are the
// first and last light of whatever date the tiles are for.
const clampToDaylight = (hour: number, hours: number[]) =>
    hours.length ? Math.min(Math.max(hour, hours[0]), hours[hours.length - 1]) : hour

// Every hour gets its own source and layer, and changing the clock only flips
// which one is visible. Pointing one source at a new file means asking maplibre
// to reload it -- which raced itself when the slider was dragged and left the
// wrong hour on screen. A visibility flip is local and cannot race.
const shadowTiles = (hour: number) =>
    `pmtiles:///shadows/${String(hour).padStart(2, '0')}.pmtiles`
const shadowSource = (hour: number) => `shadows-${hour}`
const shadowLayer = (hour: number) => `shadow-${hour}`

const SHADOW_COLOUR = '#4a4a68'
const SHADOW_OPACITY = 0.3

// A source wants a FeatureCollection; a route is a bare geometry until wrapped.
const asFeature = (geometry: LineGeometry | undefined) =>
    geometry
        ? { type: 'FeatureCollection' as const, features: [{ type: 'Feature' as const, geometry, properties: {} }] }
        : EMPTY

maplibregl.addProtocol('pmtiles', protocol.tile)

function MapView() {
    const containerRef = useRef<HTMLDivElement>(null)
    const mapRef = useRef<maplibregl.Map | null>(null)

    // The hour the style was built around. The style is built once, from the
    // manifest, so this is read there rather than recomputed -- an hour that
    // ticked over in between would leave the slider and the map disagreeing.
    const initialHourRef = useRef<number | null>(null)

    const [manifest, setManifest] = useState<ShadowManifest | null>(null)
    const [manifestError, setManifestError] = useState<string | null>(null)
    const [hour, setHour] = useState(cityHour)
    const [alpha, setAlpha] = useState(INITIAL_ALPHA)
    const [points, setPoints] = useState<LatLon[]>([])
    const [plan, setPlan] = useState<RoutePlan | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)

    // --- what the tiles are, before any of them can be drawn ---------------
    useEffect(() => {
        const controller = new AbortController()

        fetchShadowManifest(controller.signal)
            .then(loaded => {
                const start = clampToDaylight(cityHour(), loaded.hours)
                initialHourRef.current = start
                setManifest(loaded)
                setHour(start)
            })
            .catch((err: Error) => {
                // An abort is us cancelling on purpose, not a failure.
                if (err.name !== 'AbortError') setManifestError(err.message)
            })

        return () => controller.abort()
    }, [])

    // --- shadows follow the clock ------------------------------------------
    useEffect(() => {
        const map = mapRef.current

        // The shadow layers arrive with the manifest, and getLayer is undefined
        // until the style holding them has loaded. Until then the style is
        // already showing the hour it was built around -- nothing to correct.
        if (!map || !manifest?.hours.length) return
        if (!map.getLayer(shadowLayer(manifest.hours[0]))) return

        const timer = setTimeout(() => {
            // At night no hour matches, every layer hides, and the map is bare
            // -- which is the right picture when the sun is down.
            for (const candidate of manifest.hours) {
                map.setLayoutProperty(shadowLayer(candidate), 'visibility',
                    candidate === hour ? 'visible' : 'none')
            }
        }, DEBOUNCE_MS)

        // A drag across the slider passes through hours nobody stops on.
        return () => clearTimeout(timer)
    }, [hour, manifest])

    // --- ask the backend for a route ---------------------------------------
    useEffect(() => {
        // No manifest means no map to click on, so this is belt and braces --
        // but the date it carries is not optional to the request below.
        if (!manifest || points.length < 2) {
            setPlan(null)
            setError(null)
            return
        }

        const controller = new AbortController()
        const timer = setTimeout(() => {
            setLoading(true)
            fetchRoute(
                { origin: points[0], destination: points[1], date: manifest.date, hour, alpha },
                controller.signal)
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
    }, [points, hour, alpha, manifest])

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

    // --- build the map once the manifest says what to build ----------------
    useEffect(() => {
        // The shadow sources are part of the style, and the style is built once.
        // Waiting costs one small same-origin fetch; guessing costs a map that
        // asks for tilesets the current date has no sun for.
        if (!containerRef.current || !manifest) return

        const map = new maplibregl.Map({
            container: containerRef.current,
            style: {
                version: 8,
                sources: {
                    protomaps: {
                        type: 'vector',
                        url: 'pmtiles:///my_area.pmtiles',
                    },
                    // Shadows are tiles like the basemap, not a query: the
                    // browser pulls only the ones on screen, and they are
                    // already drawn when you arrive.
                    ...Object.fromEntries(manifest.hours.map(hour => [
                        shadowSource(hour),
                        { type: 'vector' as const, url: shadowTiles(hour) },
                    ])),
                    baseline: { type: 'geojson', data: EMPTY },
                    route: { type: 'geojson', data: EMPTY },
                },
                layers: [
                    ...layers('protomaps', GRAYSCALE),
                    ...manifest.hours.map(hour => ({
                        id: shadowLayer(hour),
                        type: 'fill' as const,
                        source: shadowSource(hour),
                        // The layer name inside the tilesets, set by --layer in
                        // the export script that also wrote this manifest.
                        'source-layer': manifest.layer,
                        layout: {
                            visibility: (hour === initialHourRef.current ? 'visible' : 'none') as 'visible' | 'none',
                        },
                        paint: {
                            'fill-color': SHADOW_COLOUR,
                            'fill-opacity': SHADOW_OPACITY,
                        },
                    })),
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
            zoom: INITIAL_ZOOM,
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
    }, [manifest]) // Runs once: the manifest is fetched once and never refetched.

    return (
        <div style={{ position: 'relative', height: '100vh' }}>
            <div style={{ height: '100%' }} ref={containerRef} />

            <RouteSummary plan={plan} loading={loading} error={error} pointCount={points.length} alpha={alpha} />

            <div style={{
                position: 'absolute', zIndex: 1, bottom: 16, left: 16, width: 260,
                padding: '12px 14px', borderRadius: 8, background: 'rgba(255,255,255,0.94)',
                boxShadow: '0 1px 4px rgba(0,0,0,0.25)', fontSize: 13, lineHeight: 1.4,
                display: 'flex', flexDirection: 'column', gap: 10,
            }}>
                <TimeSlider labels={HOUR_LABELS} value={hour} onChange={setHour} />
                {/* Past dusk every shadow layer hides and the map goes bare. That is
                    the honest picture, but an empty map reads as a failure unless
                    something says why it is empty. */}
                {!isDaylight(hour, manifest) && (
                    <div style={{ color: '#6b7280' }}>
                        The sun is down over Astana. Nothing casts a shadow at this hour.
                    </div>
                )}
                {/* Without a manifest there is no map at all, so say so rather
                    than leaving an empty page to be read as a slow load. */}
                {manifestError && (
                    <div style={{ color: '#b91c1c' }}>{manifestError}</div>
                )}
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
