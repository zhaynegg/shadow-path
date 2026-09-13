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

// Vite proxies /api to the backend in dev (see vite.config.ts), so this stays
// same-origin.
//
// Every whole hour of the Astana day. The manifest adds the sub-hour stamps on
// top; these are here so the clock can always be moved into the night, which is
// a real state of the world rather than an error.
const WHOLE_HOURS = Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, '0')}:00`)
const INITIAL_ALPHA = 6
const INITIAL_ZOOM = 14

// Dragging a slider crosses many values on the way to the one you want. Each
// would re-plan a route on the server and re-point the shadow layer. Wait for
// the drag to settle.
const DEBOUNCE_MS = 150

const ROUTE_COLOUR = '#2563eb'
const BASELINE_COLOUR = '#9ca3af'

const EMPTY = { type: 'FeatureCollection' as const, features: [] }

// Which stamps have a tileset comes from the manifest, because it is a property
// of the date the tiles were built for -- both how long daylight is and how
// finely each hour is cut.
const isDaylight = (time: string, manifest: ShadowManifest | null) =>
    manifest ? manifest.times.includes(time) : true

// Where the slider can stop. Not a uniform scale, deliberately: the stamps are
// twenty minutes apart at dawn and dusk and an hour apart in the middle of the
// day, so the clock moves in finer steps exactly where the picture changes
// faster. Sorting "HH:MM" as text is chronological.
const sliderTimes = (manifest: ShadowManifest | null) =>
    manifest ? [...new Set([...WHOLE_HOURS, ...manifest.times])].sort() : WHOLE_HOURS

const toMinutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3))

// The city's own clock, not the viewer's -- the shadows are Astana's whoever is
// looking at them. To the minute, because the stamps are now finer than an hour.
const cityMinutes = () => {
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Almaty', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(new Date())
    const part = (type: string) => Number(parts.find(p => p.type === type)?.value ?? 0)
    return part('hour') * 60 + part('minute')
}

// Open on the stamp nearest the city's clock. Nearest rather than the top of
// the hour, because sub-hour stamps exist exactly where an hour is too coarse
// -- rounding down to one there would throw away the resolution they were built
// for. Landing outside daylight would open the map bare, which reads as broken
// rather than as nightfall, and picking from the manifest cannot do that.
const nearestStamp = (minutes: number, times: string[]) =>
    times.length
        ? times.reduce((best, time) =>
            Math.abs(toMinutes(time) - minutes) < Math.abs(toMinutes(best) - minutes) ? time : best)
        : WHOLE_HOURS[12]

// Every stamp gets its own source and layer, and changing the clock only flips
// which one is visible. Pointing one source at a new file means asking maplibre
// to reload it -- which raced itself when the slider was dragged and left the
// wrong stamp on screen. A visibility flip is local and cannot race.
const stem = (time: string) => time.replace(':', '')
const shadowTiles = (time: string) => `pmtiles:///shadows/${stem(time)}.pmtiles`
const shadowSource = (time: string) => `shadows-${stem(time)}`
const shadowLayer = (time: string) => `shadow-${stem(time)}`

// Hue carries what kind of shade it is, opacity carries how much of it there
// is. Keeping those on separate channels is what lets a tree read as a tree
// without overstating how dark it is.
const SHADOW_COLOUR = '#3b3b6d'
const CANOPY_COLOUR = '#2f6e46'
const SHADOW_OPACITY = 0.38

// A crown is not a wall. Tiles tag each blob 'solid' or 'canopy', and canopy is
// drawn through the same factor the router weights it by -- CANOPY_OPACITY in
// core/trees.py. If you change one, change the other: a map that shades a
// tree-lined street darker than the route thinks it is, is lying to the reader.
// The green above is free of that: it changes which shade you are looking at,
// never how much of it there is.
const CANOPY_OPACITY = 0.7
const FILL_OPACITY: maplibregl.ExpressionSpecification = [
    'case', ['==', ['get', 'kind'], 'canopy'],
    SHADOW_OPACITY * CANOPY_OPACITY,
    SHADOW_OPACITY,
]
const FILL_COLOUR: maplibregl.ExpressionSpecification = [
    'case', ['==', ['get', 'kind'], 'canopy'],
    CANOPY_COLOUR,
    SHADOW_COLOUR,
]

// Warm, and lighter than the #cccccc ground, because everything shaded on this
// map is cool and darker. Warm against cool separates a building from a shadow
// before any difference in value has to, which matters at the zoom where a
// footprint is only a few pixels across.
const BUILDING_COLOUR = '#f4f1ea'
const BUILDING_OUTLINE = '#b3a897'
const BUILDING_FILTER: maplibregl.ExpressionSpecification =
    ['in', ['get', 'kind'], ['literal', ['building', 'building_part']]]

// The only thing on this map that is neither shade, structure, nor route.
// Muted on purpose: the route is #2563eb, and a saturated lake would compete
// with the one line the reader is actually meant to follow.
const WATER_COLOUR = '#a6c6da'
const WATER_LINE_COLOUR = '#8cb0c6'
const WATER_LAYERS = new Set(['water', 'water_stream', 'water_river'])

const blueWater = (layer: maplibregl.LayerSpecification): maplibregl.LayerSpecification => {
    if (!WATER_LAYERS.has(layer.id)) return layer
    if (layer.type === 'line') return { ...layer, paint: { ...layer.paint, 'line-color': WATER_LINE_COLOUR } }
    if (layer.type === 'fill') return { ...layer, paint: { ...layer.paint, 'fill-color': WATER_COLOUR } }
    return layer
}

// Buildings are drawn after the shadows, not before, and that is the whole
// reason a building used to be the same colour as one. cast_shadow unions the
// footprint with its translated copy, so every blob contains the building that
// threw it -- draw the field on top and every building in the city is filled
// with shadow colour by its own shadow. It is also the wrong claim to make:
// this is a ground-plane model, and shade on a roof is not somewhere anybody
// walks. Shadows now land on the streets, which is the only place they are
// about.
const buildingLayers = [
    {
        id: 'buildings',
        type: 'fill' as const,
        source: 'protomaps',
        'source-layer': 'buildings',
        filter: BUILDING_FILTER,
        paint: { 'fill-color': BUILDING_COLOUR },
    },
    {
        // An outline only once footprints are big enough to have a shape worth
        // seeing. Below that it is a grey haze over the whole city.
        id: 'buildings-outline',
        type: 'line' as const,
        source: 'protomaps',
        'source-layer': 'buildings',
        filter: BUILDING_FILTER,
        minzoom: 15,
        paint: {
            'line-color': BUILDING_OUTLINE,
            'line-width': ['interpolate', ['linear'], ['zoom'], 15, 0.3, 18, 1] as maplibregl.ExpressionSpecification,
        },
    },
]

// A source wants a FeatureCollection; a route is a bare geometry until wrapped.
const asFeature = (geometry: LineGeometry | undefined) =>
    geometry
        ? { type: 'FeatureCollection' as const, features: [{ type: 'Feature' as const, geometry, properties: {} }] }
        : EMPTY

maplibregl.addProtocol('pmtiles', protocol.tile)

function MapView() {
    const containerRef = useRef<HTMLDivElement>(null)
    const mapRef = useRef<maplibregl.Map | null>(null)

    // The stamp the style was built around. The style is built once, from the
    // manifest, so this is read there rather than recomputed -- a clock that
    // ticked over in between would leave the slider and the map disagreeing.
    const initialTimeRef = useRef<string | null>(null)

    const [manifest, setManifest] = useState<ShadowManifest | null>(null)
    const [manifestError, setManifestError] = useState<string | null>(null)
    const [time, setTime] = useState(() => nearestStamp(cityMinutes(), WHOLE_HOURS))
    const [alpha, setAlpha] = useState(INITIAL_ALPHA)
    const [points, setPoints] = useState<LatLon[]>([])
    const [plan, setPlan] = useState<RoutePlan | null>(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)

    const times = sliderTimes(manifest)

    // --- what the tiles are, before any of them can be drawn ---------------
    useEffect(() => {
        const controller = new AbortController()

        fetchShadowManifest(controller.signal)
            .then(loaded => {
                const start = nearestStamp(cityMinutes(), loaded.times)
                initialTimeRef.current = start
                setManifest(loaded)
                setTime(start)
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
        // already showing the stamp it was built around -- nothing to correct.
        if (!map || !manifest?.times.length) return
        if (!map.getLayer(shadowLayer(manifest.times[0]))) return

        const timer = setTimeout(() => {
            // At night no stamp matches, every layer hides, and the map is bare
            // -- which is the right picture when the sun is down.
            for (const candidate of manifest.times) {
                map.setLayoutProperty(shadowLayer(candidate), 'visibility',
                    candidate === time ? 'visible' : 'none')
            }
        }, DEBOUNCE_MS)

        // A drag across the slider passes through stamps nobody stops on.
        return () => clearTimeout(timer)
    }, [time, manifest])

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
                {
                    origin: points[0], destination: points[1], date: manifest.date,
                    // Split here rather than sent as a string: the backend keys
                    // its scored graph on the pair, and this is the one place
                    // that knows the stamp the map is actually showing.
                    hour: Number(time.slice(0, 2)), minute: Number(time.slice(3)),
                    alpha,
                },
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
        // old stamp can never arrive after a fast answer for the current one.
        return () => {
            clearTimeout(timer)
            controller.abort()
        }
    }, [points, time, alpha, manifest])

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
                    ...Object.fromEntries(manifest.times.map(stamp => [
                        shadowSource(stamp),
                        { type: 'vector' as const, url: shadowTiles(stamp) },
                    ])),
                    baseline: { type: 'geojson', data: EMPTY },
                    route: { type: 'geojson', data: EMPTY },
                },
                layers: [
                    // Its buildings layer is dropped and redrawn above the
                    // shadows instead -- see buildingLayers.
                    ...layers('protomaps', GRAYSCALE)
                        .filter(layer => layer.id !== 'buildings')
                        .map(blueWater),
                    ...manifest.times.map(stamp => ({
                        id: shadowLayer(stamp),
                        type: 'fill' as const,
                        source: shadowSource(stamp),
                        // The layer name inside the tilesets, set by --layer in
                        // the export script that also wrote this manifest.
                        'source-layer': manifest.layer,
                        layout: {
                            visibility: (stamp === initialTimeRef.current ? 'visible' : 'none') as 'visible' | 'none',
                        },
                        paint: {
                            'fill-color': FILL_COLOUR,
                            'fill-opacity': FILL_OPACITY,
                        },
                    })),
                    ...buildingLayers,
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
                <TimeSlider labels={times} value={Math.max(0, times.indexOf(time))}
                    onChange={index => setTime(times[index])} />
                {/* Past dusk every shadow layer hides and the map goes bare. That is
                    the honest picture, but an empty map reads as a failure unless
                    something says why it is empty. */}
                {!isDaylight(time, manifest) && (
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
