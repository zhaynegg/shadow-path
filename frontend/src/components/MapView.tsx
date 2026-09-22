import 'maplibre-gl/dist/maplibre-gl.css'
import * as maplibregl from 'maplibre-gl'
import { useRef, useEffect, useState } from 'react'
import { Protocol } from 'pmtiles'
import { layers, GRAYSCALE } from '@protomaps/basemaps'
import TimeSlider from './controls/TimeSlider'
import ShadeSlider from './controls/ShadeSlider'
import SearchBox from './controls/SearchBox'
import LocateButton from './controls/LocateButton'
import RouteSummary from './RouteSummary'
import DeparturePlanner from './DeparturePlanner'
import type { Place } from '../api/geocode'
import {
    fetchDayScan, fetchRoute, fetchShadowManifest,
    type DayPlan, type LatLon, type LineGeometry, type RoutePlan, type ShadowManifest,
} from '../api/client'
import {
    WHOLE_HOURS, cityMinutes, isDaylight, nearestStamp, prettyDate, sliderTimes,
} from '../lib/stamps'
import { footprintAt } from '../lib/footprint'
import { COARSE_M, outOfReach, roughly, type Fix } from '../lib/locate'

// What the backend last said, and which request it was saying it about. Either
// a plan or a message, never both -- a failed request has no route to draw.
type Answer = { key: string, plan?: RoutePlan, error?: string }

// The same idea for the whole-day scan, tagged the same way and for the same
// reason: a scan takes seconds, and the pins can move while it is running.
type DayAnswer = { key: string, plan?: DayPlan, error?: string }

// What the locate button last had to say. `point` is what it is about, and it
// is the same tagging idea once more: a note that the fix was only good to a
// kilometre is about one pin, and has to leave when that pin does. An error
// has no point to be about, and stays until the next attempt.
type Located = { tone: 'error' | 'info', text: string, point?: LatLon }

const protocol = new Protocol({ metadata: true })

// Vite proxies /api to the backend in dev (see vite.config.ts), so this stays
// same-origin.
const INITIAL_ALPHA = 6
const INITIAL_ZOOM = 14

// Dragging a slider crosses many values on the way to the one you want. Each
// would re-plan a route on the server and re-point the shadow layer. Wait for
// the drag to settle.
const DEBOUNCE_MS = 150

const ROUTE_COLOUR = '#2563eb'
const BASELINE_COLOUR = '#9ca3af'

// The two ends of the walk, and the same two colours the A and B pins are drawn
// in -- --start and --end in index.css. Deliberately not the route's blue: the
// building you are leaving from is not part of the line you walk along, and
// giving them one colour would say that it was.
const START_COLOUR = '#15803d'
const END_COLOUR = '#b91c1c'

const EMPTY = { type: 'FeatureCollection' as const, features: [] }

// First pick starts the walk, second finishes it, third starts over. Clicking
// the map and searching for an address both go through here, so the two ways of
// setting a point cannot drift into two different rules for what a pick means.
const nextPoints = (points: LatLon[], point: LatLon): LatLon[] =>
    points.length >= 2 ? [point] : [...points, point]

// Close enough to read the street you searched for, but never a zoom out: if
// the map is already closer than this, whoever put it there meant it.
const SEARCH_ZOOM = 15

// Every stamp gets its own source and layer, and changing the clock only flips
// which one is visible. Pointing one source at a new file means asking maplibre
// to reload it -- which raced itself when the slider was dragged and left the
// wrong stamp on screen. A visibility flip is local and cannot race.
// A licence term, not a courtesy. The canopy in these tiles is cut from Meta
// and WRI's 1 m canopy height map, which is CC-BY-4.0 and requires the credit
// wherever the derived work is shown -- and this is the only place it is shown.
// maplibre de-duplicates identical strings, so naming it on every stamp's source
// puts it in the corner exactly once.
const CANOPY_CREDIT =
    'Canopy <a href="https://registry.opendata.aws/dataforgood-fb-forests/" '
    + 'target="_blank" rel="noreferrer">Meta/WRI</a> CC BY 4.0, imagery \u00a9 Maxar'

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

    // What the locate button last did, or null if it has nothing to say.
    const [located, setLocated] = useState<Located | null>(null)

    // One slot for whatever the backend last said, tagged with the request it
    // was an answer to. Tagging is the whole mechanism: it lets the plan, the
    // error and the spinner all be worked out during render, instead of being
    // three states that an effect has to remember to null out every time the
    // clicks or the clock move underneath them.
    const [answer, setAnswer] = useState<Answer | null>(null)

    // The day scan, and which walk the reader asked for one about. Two slots
    // rather than one: a scan is two dozen searches, so it runs when it is
    // asked for and not when the inputs happen to change.
    const [dayAnswer, setDayAnswer] = useState<DayAnswer | null>(null)
    const [scanKey, setScanKey] = useState<string | null>(null)

    const times = sliderTimes(manifest)

    // A point picked anywhere but on the map is one you have not seen yet, so
    // the map has to go to it -- unlike a clicked one, which is already under
    // the cursor. Both the search box and the locate button arrive here. With
    // both ends set, show the whole walk rather than only its far end.
    const pickPoint = (point: LatLon) => {
        // Plain state, not the updater form: this is called from a handler that
        // is rebuilt every render, so `points` is already current -- and moving
        // the map is a side effect, which an updater is no place for. React may
        // call one twice.
        const picked = nextPoints(points, point)
        setPoints(picked)

        const map = mapRef.current
        if (!map) return

        if (picked.length === 2) {
            // Room for the panels standing over the map on three sides, but
            // never more than the map can spare. Asking for 330px of clearance
            // on each side of an 800px window leaves 140 for the walk, and
            // maplibre answers by zooming out to the whole oblast -- a 2 km
            // walk drawn at 5 km to the centimetre.
            const box = map.getContainer()
            const side = Math.min(340, box.clientWidth * 0.26)
            const vert = Math.min(90, box.clientHeight * 0.15)

            // Extended rather than written as a pair. fitBounds reads a bare
            // array as [south-west, north-east], so handing it the two points
            // in the order they were picked inverts the box whenever the
            // destination lies west or south of the start -- and an inverted
            // box is one that wraps the planet, which maplibre duly fits by
            // zooming out to the whole oblast.
            const bounds = new maplibregl.LngLatBounds()
            for (const [lat, lon] of picked) bounds.extend([lon, lat])

            map.fitBounds(bounds, {
                padding: { top: vert, bottom: vert, left: side, right: side },
                maxZoom: SEARCH_ZOOM,
            })
        } else {
            map.flyTo({
                center: [point[1], point[0]],
                zoom: Math.max(map.getZoom(), SEARCH_ZOOM),
            })
        }
    }

    const pickPlace = (place: Place) => pickPoint(place.point)

    // A fix is a point like any other once it is in range -- so it goes through
    // pickPoint, and becomes the start or the destination by the same rule a
    // click does. What is not like any other point is how well it is known: the
    // pin is drawn at full confidence whether the device was sure to ten metres
    // or to two kilometres, so where it was not sure, the dock says so.
    const takeFix = ({ point, accuracy }: Fix) => {
        // Checked here rather than left to the router. The backend would refuse
        // this too -- snap() in core/routing.py, beyond 300 m from a street --
        // but it would refuse it as a failed route two clicks later, and a
        // reader in Almaty would have no way to know that being in Almaty was
        // the problem.
        const problem = outOfReach(point)
        if (problem) {
            setLocated({ tone: 'error', text: problem })
            return
        }

        pickPoint(point)
        setLocated(accuracy > COARSE_M
            ? {
                tone: 'info', point,
                text: `Your device places you within about ${roughly(accuracy)}.`
                    + ' Click the map to put the pin somewhere better.',
            }
            : null)
    }

    // What the controls currently describe. Two points and a clock make a
    // request; anything less makes none.
    const requestKey = manifest && points.length >= 2
        ? JSON.stringify([points[0], points[1], manifest.date, time, alpha])
        : null

    // An answer to some earlier question is not an answer to this one.
    const answered = answer?.key === requestKey ? answer : null
    const plan = answered?.plan ?? null
    const error = answered?.error ?? null

    // A question with nothing answering it yet. True from the moment the second
    // point lands rather than from the moment the fetch starts, so the debounce
    // is part of the wait the panel admits to -- it used to spend that 150ms
    // showing the previous stamp's numbers as though they were these.
    const loading = requestKey !== null && answered === null

    // The map is the exception: it keeps the line it last drew while the next
    // one is computed, because blanking it on every step of the time slider
    // would flicker. It clears only when no walk is being asked about at all.
    const drawnPlan = requestKey ? answer?.plan ?? null : null

    // The same walk, minus the clock. A scan spans the whole day, so moving the
    // time slider does not invalidate one -- which is the point: the chart is
    // what the reader moves the slider *by*.
    const dayKey = manifest && points.length >= 2
        ? JSON.stringify([points[0], points[1], manifest.date, alpha])
        : null

    const dayAnswered = dayAnswer?.key === dayKey ? dayAnswer : null
    const scanning = scanKey !== null && scanKey === dayKey && dayAnswered === null

    // A note about how well a fix was known outlives the fix only while the pin
    // it was about is still on the map -- cleared, or pushed off by a third
    // pick, and it is a caveat about nothing. Compared by value: two points are
    // the same point, never the same array. A note with no point is an error,
    // which was never about a pin and stays until the next attempt.
    const about = located?.point
    const locateNote = located && (!about || points.some(([lat, lon]) => lat === about[0] && lon === about[1]))
        ? located
        : null

    // --- what the tiles are, before any of them can be drawn ---------------
    useEffect(() => {
        const controller = new AbortController()

        fetchShadowManifest(controller.signal)
            .then(loaded => {
                // Every stop the slider has, not only the lit ones, so the map
                // opens on the hour it actually is. After dusk that means no
                // shadow layer matches and the map opens bare -- which is the
                // true picture of Astana at 21:00, and the panel says so.
                const start = nearestStamp(cityMinutes(), sliderTimes(loaded))
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
        // requestKey is null in exactly the cases these guards cover; they are
        // spelled out again so TypeScript can narrow manifest and the points.
        // Nothing is reset here -- there is no longer anything to reset.
        if (!requestKey || !manifest || points.length < 2) return

        const controller = new AbortController()
        const timer = setTimeout(() => {
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
                // Tagged with the question it answers. A slow answer for an
                // old stamp can still land here, and is then simply not the
                // answer to what is on screen -- the render above drops it.
                .then(result => setAnswer({ key: requestKey, plan: result }))
                .catch((err: Error) => {
                    // An abort is us cancelling on purpose, not a failure.
                    if (err.name !== 'AbortError') setAnswer({ key: requestKey, error: err.message })
                })
        }, DEBOUNCE_MS)

        // abort() cancels a request already in flight, so a slow answer for an
        // old stamp can never arrive after a fast answer for the current one.
        return () => {
            clearTimeout(timer)
            controller.abort()
        }
    }, [requestKey, points, time, alpha, manifest])

    // --- the whole day, when asked for it ----------------------------------
    useEffect(() => {
        // Only for the walk currently on screen. A scan started before the pins
        // moved is an answer to a question nobody is asking any more, and the
        // render above has already stopped showing it.
        if (!scanKey || scanKey !== dayKey || !manifest || points.length < 2) return

        const controller = new AbortController()

        // No debounce: this runs because a button was pressed, not because a
        // slider passed through a value on its way somewhere.
        fetchDayScan(
            {
                origin: points[0], destination: points[1],
                date: manifest.date, alpha,
            },
            controller.signal)
            .then(result => setDayAnswer({ key: scanKey, plan: result }))
            .catch((err: Error) => {
                if (err.name !== 'AbortError') setDayAnswer({ key: scanKey, error: err.message })
            })

        return () => controller.abort()
    }, [scanKey, dayKey, points, alpha, manifest])

    // --- draw whatever came back -------------------------------------------
    useEffect(() => {
        const map = mapRef.current
        if (!map) return
        ;(map.getSource('route') as maplibregl.GeoJSONSource | undefined)?.setData(asFeature(drawnPlan?.route.geometry))
        ;(map.getSource('baseline') as maplibregl.GeoJSONSource | undefined)?.setData(asFeature(drawnPlan?.baseline.geometry))
    }, [drawnPlan])

    // --- the building each end of the walk stands on -----------------------
    useEffect(() => {
        const map = mapRef.current
        if (!map) return

        // Only what is on screen and drawn can be asked about: this reads the
        // basemap's own building tiles rather than any data of ours, so a
        // footprint outside the viewport, or in a tile still loading, is not
        // there to be found yet. Hence the re-run on idle below.
        const underneath = () => ({
            type: 'FeatureCollection' as const,
            features: points.flatMap(([lat, lon], index) => {
                const [hit] = map.queryRenderedFeatures(map.project([lon, lat]),
                    { layers: ['buildings'] })

                // Not hit.geometry. A feature in these tiles is not always one
                // building -- zoomed out they arrive packed many to a
                // MultiPolygon, and drawing the whole of what was returned
                // tinted every footprint in the district.
                const shape = footprintAt(hit?.geometry, lon, lat)
                return shape ? [{
                    type: 'Feature' as const,
                    geometry: shape,
                    properties: { role: index === 0 ? 'start' : 'end' },
                }] : []
            }),
        })

        // What was last drawn, so that setting the same shape again can be
        // skipped. It is not an optimisation: setData re-renders, a re-render
        // fires idle, and an idle that always sets data would spin forever.
        let painted = ''

        const paint = () => {
            const data = underneath()
            const signature = JSON.stringify(data)
            if (signature === painted) return
            painted = signature
            ;(map.getSource('endpoints') as maplibregl.GeoJSONSource | undefined)?.setData(data)
        }

        paint()

        // Every settle, not just the first: the geometry a tile hands back is
        // clipped and simplified for the zoom it was asked at, so a highlight
        // queried at z12 is the wrong shape once you have zoomed to z17.
        map.on('idle', paint)
        return () => { map.off('idle', paint) }
    }, [points])

    // --- markers for the two clicked points --------------------------------
    useEffect(() => {
        const map = mapRef.current
        if (!map) return

        const markers = points.map(([lat, lon], index) => {
            // maplibre's default pin is a 27px SVG that matches nothing else on
            // screen. A custom element costs one line and lets the two ends of
            // the walk be labelled A and B, which the summary panel then names.
            const element = document.createElement('div')
            element.className = `marker ${index === 0 ? 'marker-a' : 'marker-b'}`
            element.innerHTML = `<span>${index === 0 ? 'A' : 'B'}</span>`
            element.title = index === 0 ? 'Start' : 'Destination'

            // The element is a square rotated -45deg, so its point is the
            // bottom corner -- that corner is what has to sit on the coordinate.
            return new maplibregl.Marker({ element, anchor: 'bottom' })
                .setLngLat([lon, lat])
                .addTo(map)
        })

        return () => markers.forEach(marker => marker.remove())
    }, [points])

    // --- build the map once it is known what to build --------------------
    useEffect(() => {
        // The shadow sources are part of the style, and the style is built
        // once, so this waits to be told what stamps exist. Waiting costs one
        // small same-origin fetch; guessing costs a map that asks for tilesets
        // the current date has no sun for.
        //
        // It waits for an *answer*, though, not for success. A failed manifest
        // used to leave this effect returning forever, and with it the whole
        // map: no basemap, no buildings, no city -- a blank page whose only
        // content was a sentence about a missing file. That is the state a
        // failed nightly build leaves the live site in, and a map of Astana
        // with no shadows on it is worth far more than nothing.
        if (!containerRef.current || (!manifest && !manifestError)) return

        // Empty when the manifest never arrived: no sources, no layers, and
        // the clock below finds no stamp to show -- which is the same code
        // path as a night-time hour, already handled everywhere downstream.
        const stamps = manifest?.times ?? []

        // Only ever read inside the loop over `stamps`, which is empty in
        // exactly the case this fallback covers -- it is here so the type says
        // what the code already guarantees.
        const tileLayer = manifest?.layer ?? ''

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
                    ...Object.fromEntries(stamps.map(stamp => [
                        shadowSource(stamp),
                        {
                            type: 'vector' as const,
                            url: shadowTiles(stamp),
                            attribution: CANOPY_CREDIT,
                        },
                    ])),
                    baseline: { type: 'geojson', data: EMPTY },
                    route: { type: 'geojson', data: EMPTY },
                    endpoints: { type: 'geojson', data: EMPTY },
                },
                layers: [
                    // Its buildings layer is dropped and redrawn above the
                    // shadows instead -- see buildingLayers.
                    ...layers('protomaps', GRAYSCALE)
                        .filter(layer => layer.id !== 'buildings')
                        .map(blueWater),
                    ...stamps.map(stamp => ({
                        id: shadowLayer(stamp),
                        type: 'fill' as const,
                        source: shadowSource(stamp),
                        // The layer name inside the tilesets, set by --layer in
                        // the export script that also wrote this manifest.
                        'source-layer': tileLayer,
                        layout: {
                            visibility: (stamp === initialTimeRef.current ? 'visible' : 'none') as 'visible' | 'none',
                        },
                        paint: {
                            'fill-color': FILL_COLOUR,
                            'fill-opacity': FILL_OPACITY,
                        },
                    })),
                    ...buildingLayers,
                    // Over the buildings, under the route. A pin says where you
                    // are to within a few metres; tinting the footprint it
                    // stands on says which door, which is the thing somebody
                    // walking actually has to recognise.
                    {
                        id: 'endpoint-fill',
                        type: 'fill',
                        source: 'endpoints',
                        paint: {
                            'fill-color': ['case', ['==', ['get', 'role'], 'start'],
                                START_COLOUR, END_COLOUR] as maplibregl.ExpressionSpecification,
                            // Enough to read as deliberate, little enough that
                            // the footprint underneath is still a building and
                            // not a swatch.
                            'fill-opacity': 0.45,
                        },
                    },
                    {
                        id: 'endpoint-outline',
                        type: 'line',
                        source: 'endpoints',
                        paint: {
                            'line-color': ['case', ['==', ['get', 'role'], 'start'],
                                START_COLOUR, END_COLOUR] as maplibregl.ExpressionSpecification,
                            'line-width': 1.5,
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
            zoom: INITIAL_ZOOM,
            maxBounds: [[70.37, 50.77], [72.39, 51.48]],
        })
        mapRef.current = map

        // Bottom right is the only corner no panel is standing in. Both are
        // restyled in index.css -- maplibre ships them square and opaque.
        map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
        map.addControl(new maplibregl.ScaleControl({ maxWidth: 92, unit: 'metric' }), 'bottom-right')

        // The updater form is essential: this handler is registered once and
        // would otherwise capture the empty array it saw at mount forever.
        map.on('click', event => {
            const { lat, lng } = event.lngLat
            setPoints(previous => nextPoints(previous, [lat, lng]))
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
    // Runs once: the manifest is fetched once, and neither outcome changes after.
    }, [manifest, manifestError])

    return (
        <div className="app">
            <div className="map-canvas" ref={containerRef} />

            <header className="panel brand">
                <span className="brand-mark" aria-hidden="true">
                    <svg width="17" height="17" viewBox="0 0 24 24" fill="none"
                        stroke="#fff" strokeWidth="2" strokeLinecap="round">
                        <circle cx="12" cy="12" r="4.2" fill="#fff" stroke="none" />
                        <path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2
                            M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6" />
                    </svg>
                </span>
                <div>
                    <div className="brand-title">Shadow Path</div>
                    {/* The date is the tiles' date, not today's. They are the
                        same until a rebuild fails, and that is exactly when a
                        reader deserves to be told which day they are looking
                        at. */}
                    <div className="brand-sub">Astana{manifest && ` \u00b7 ${prettyDate(manifest.date)}`}</div>
                </div>
            </header>

            {/* Clicking the map only works if you can already find the place on
                it. This is the other way in, and it is the one anybody who does
                not know Astana by sight has to use. */}
            <div className="panel search">
                <SearchBox onPick={pickPlace} next={points.length === 1 ? 'destination' : 'start'} />
            </div>

            <RouteSummary plan={plan} loading={loading} error={error}
                pointCount={points.length} alpha={alpha} time={time}
                stamps={manifest?.times ?? []}>
                <DeparturePlanner
                    day={dayAnswered?.plan ?? null}
                    loading={scanning}
                    error={dayAnswered?.error ?? null}
                    alpha={alpha}
                    time={time}
                    onScan={() => setScanKey(dayKey)}
                    onPick={setTime} />
            </RouteSummary>

            <div className="panel dock">
                <TimeSlider labels={times} value={Math.max(0, times.indexOf(time))}
                    onChange={index => setTime(times[index])} />

                {/* Past dusk every shadow layer hides and the map goes bare. That is
                    the honest picture, but an empty map reads as a failure unless
                    something says why it is empty. */}
                {!isDaylight(time, manifest) && (
                    <div className="note note-night">
                        <svg className="note-icon" width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M20.7 14.6A8.6 8.6 0 0 1 9.4 3.3a8.6 8.6 0 1 0 11.3 11.3z" />
                        </svg>
                        <span>The sun is down over Astana. Nothing casts a shadow at this hour.</span>
                    </div>
                )}

                {/* Without a manifest there is no map at all, so say so rather
                    than leaving an empty page to be read as a slow load. */}
                {manifestError && (
                    <div className="note note-error">
                        <svg className="note-icon" width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M12 2 1.5 20.5h21L12 2zm0 6.5a1 1 0 0 1 1 1v4.8a1 1 0 1 1-2 0V9.5a1 1
                                0 0 1 1-1zm0 9.2a1.2 1.2 0 1 1 0-2.4 1.2 1.2 0 0 1 0 2.4z" />
                        </svg>
                        <span>{manifestError}</span>
                    </div>
                )}

                <ShadeSlider value={alpha} onChange={setAlpha} />

                {/* Directly above the button that caused it. The same crosshair
                    on both, so a red panel at the bottom of the dock is tied to
                    the thing that was just pressed rather than being one more
                    message about the map in general. */}
                {locateNote && (
                    <div className={`note note-${locateNote.tone}`} role="status">
                        <svg className="note-icon" width="13" height="13" viewBox="0 0 24 24" fill="none"
                            stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                            <circle cx="12" cy="12" r="7" />
                            <circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none" />
                            <path d="M12 1.6v3.1M12 19.3v3.1M1.6 12h3.1M19.3 12h3.1" />
                        </svg>
                        <span>{locateNote.text}</span>
                    </div>
                )}

                <div className="dock-foot">
                    {/* The map draws two kinds of shade in two colours and never
                        says so anywhere else. */}
                    <div className="legend">
                        <span className="legend-item">
                            <span className="swatch" style={{ background: SHADOW_COLOUR }} />building
                        </span>
                        <span className="legend-item">
                            <span className="swatch" style={{ background: CANOPY_COLOUR }} />tree
                        </span>
                    </div>
                    <div className="dock-actions">
                        <LocateButton
                            onFix={takeFix}
                            onFail={text => setLocated({ tone: 'error', text })}
                            next={points.length === 1 ? 'destination' : 'start'} />

                        {/* Clears the note too. It is either a caveat about a
                            pin that is going, or an error about an attempt
                            nobody is still making. */}
                        <button className="btn"
                            onClick={() => { setPoints([]); setLocated(null) }}
                            disabled={points.length === 0 && !locateNote}>
                            Clear
                        </button>
                    </div>
                </div>
            </div>
        </div>
    )
}

export default MapView
