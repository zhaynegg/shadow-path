import type { Feature, FeatureCollection, Point } from 'geojson'
import { latinise, type SearchEntry, type SearchIndex } from '../api/geocode'

type Category = 'cafe' | 'restaurant' | 'shop' | 'mall' | 'hospital' | 'pharmacy'
    | 'school' | 'hotel' | 'park' | 'bank' | 'transit' | 'landmark' | 'building'

export type MapPlaceProperties = {
    id: number,
    name: string,
    detail: string,
    icon: string,
    category: Category,
    priority: number,
    minZoom: number,
}

export type MapPlaces = FeatureCollection<Point, MapPlaceProperties>

type Visibility = { priority: number, minZoom: number }
const VISIBILITY: Record<Category, Visibility> = {
    mall: { priority: 1, minZoom: 13 },
    hospital: { priority: 1, minZoom: 13 },
    landmark: { priority: 2, minZoom: 13 },
    hotel: { priority: 2, minZoom: 13 },
    park: { priority: 2, minZoom: 13 },
    school: { priority: 3, minZoom: 14 },
    pharmacy: { priority: 3, minZoom: 15 },
    restaurant: { priority: 3, minZoom: 14 },
    cafe: { priority: 4, minZoom: 14 },
    bank: { priority: 4, minZoom: 15 },
    shop: { priority: 5, minZoom: 15 },
    transit: { priority: 6, minZoom: 16 },
    building: { priority: 7, minZoom: 17 },
}

// Major facilities belong on the first view. Smaller practices and nurseries
// share their category's pictogram, but appear only after zooming closer.
const KIND_VISIBILITY: Partial<Record<string, Visibility>> = {
    clinic: { priority: 3, minZoom: 14 },
    doctors: { priority: 5, minZoom: 15 },
    dentist: { priority: 5, minZoom: 15 },
    'blood donation': { priority: 5, minZoom: 15 },
    laboratory: { priority: 5, minZoom: 15 },
    rehabilitation: { priority: 5, minZoom: 15 },
    kindergarten: { priority: 5, minZoom: 15 },
}

// The exporter puts the OSM kind before an optional street address in `d`.
// Group those kinds, rather than guessing a business from its name.
const KINDS: Partial<Record<Category, readonly string[]>> = {
    cafe: ['cafe', 'coffee', 'internet cafe', 'ice cream', 'tea'],
    restaurant: ['restaurant', 'fast food', 'food court', 'bar', 'pub', 'biergarten', 'hookah lounge'],
    mall: ['mall', 'department store', 'marketplace'],
    hospital: ['hospital', 'clinic', 'doctors', 'dentist', 'blood donation', 'laboratory', 'rehabilitation'],
    pharmacy: ['pharmacy', 'chemist', 'medical supply'],
    school: ['school', 'kindergarten', 'college', 'university', 'language school', 'music school',
        'driving school', 'prep school', 'educational institution', 'childcare'],
    hotel: ['hotel', 'hostel', 'guest house', 'chalet', 'apartment', 'motel'],
    park: ['park', 'garden', 'square', 'theme park', 'nature reserve', 'playground'],
    bank: ['bank', 'atm', 'bureau de change', 'money lender', 'financial advisor'],
    transit: ['platform', 'station', 'stop position', 'bus station', 'bus stop', 'halt', 'bicycle rental'],
    landmark: ['monument', 'memorial', 'attraction', 'museum', 'artwork', 'theatre', 'cinema',
        'gallery', 'arts centre', 'place of worship', 'fountain', 'information'],
    shop: ['shop', 'convenience', 'supermarket', 'clothes', 'beauty', 'pawnbroker', 'electronics',
        'florist', 'confectionery', 'shoes', 'mobile phone', 'furniture', 'computer', 'alcohol',
        'hardware', 'doityourself', 'houseware', 'cosmetics', 'bakery', 'dry cleaning', 'toys',
        'tobacco', 'optician', 'erotic', 'sports', 'pet', 'doors', 'baby goods', 'jewelry', 'butcher',
        'books', 'bed', 'stationery', 'greengrocer', 'perfumery', 'outdoor', 'appliance', 'gift',
        'kitchen', 'photo', 'copyshop', 'seafood', 'electrical', 'interior decoration', 'fabric',
        'bicycle', 'variety store', 'health food', 'carpet', 'fashion accessories', 'e-cigarette',
        'lighting', 'fishing', 'grocery', 'chocolate', 'garden centre', 'beverages', 'paint', 'farm',
        'water filter', 'tiles', 'garden furniture', 'flooring', 'hifi', 'watches', 'honey',
        'pottery', 'general', 'pastry', 'sewing', 'tailor', 'agrarian', 'musical instrument',
        'nutrition supplements', 'kiosk', 'mobile phone accessories'],
}

const CATEGORIES = new Map<string, Category>(
    Object.entries(KINDS).flatMap(([category, kinds]) =>
        kinds.map(kind => [kind, category as Category] as const)),
)

type Landmark = {
    kind: string,
    aliases: readonly string[],
    point: [number, number],
    name: string,
    icon: string,
}

const LANDMARKS: readonly Landmark[] = [
    {
        kind: 'monument',
        aliases: ['Baiterek', 'Bayterek', 'Бәйтерек', 'Байтерек', 'Baiterek Monument', 'Монумент Байтерек'],
        point: [51.12829, 71.43046],
        name: 'Baiterek',
        icon: 'landmark-baiterek',
    },
    {
        kind: 'mall',
        aliases: ['Khan Shatyr', 'Хан Шатыр', 'Khan Shatyr Mall'],
        point: [51.1325, 71.40386],
        name: 'Khan Shatyr',
        icon: 'landmark-khan-shatyr',
    },
]

const nearby = (point: SearchEntry['p'], reference: Landmark['point']) => {
    const metresPerDegree = 111_320
    const north = (point[0] - reference[0]) * metresPerDegree
    const east = (point[1] - reference[1]) * metresPerDegree
        * Math.cos(reference[0] * Math.PI / 180)
    return Math.hypot(north, east) <= 75
}

/** Named cached destinations as map points; search IDs stay stable after filtering. */
export function mapPlaces(index: SearchIndex): MapPlaces {
    const features: Feature<Point, MapPlaceProperties>[] = []
    const landmarks = LANDMARKS.map(landmark => ({
        ...landmark,
        keys: new Set(landmark.aliases.map(alias => latinise(alias, index.fold))),
    }))

    index.entries.forEach((entry, id) => {
        const kind = entry.d.split('·')[0].trim().toLowerCase()
        const name = entry.n.trim()
        const [latitude, longitude] = entry.p
        if (kind === 'street' || !name || !Number.isFinite(latitude) || !Number.isFinite(longitude)
            || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return

        // A shared name is insufficient: the index also has Baiterek station,
        // Khan Shatyr bus platforms, and businesses named after landmarks.
        const keys = [entry.n, entry.c, entry.l].map(value => latinise(value, index.fold))
        const landmark = landmarks.find(candidate => candidate.kind === kind
            && nearby(entry.p, candidate.point) && keys.some(key => candidate.keys.has(key)))
        const category = landmark ? 'landmark' : CATEGORIES.get(kind) ?? 'building'
        const visibility = landmark ? { priority: 0, minZoom: 11.5 }
            : KIND_VISIBILITY[kind] ?? VISIBILITY[category]

        features.push({
            type: 'Feature',
            id,
            properties: {
                id,
                name: landmark?.name ?? name,
                detail: entry.d,
                icon: landmark?.icon ?? `poi-${category}`,
                category,
                ...visibility,
            },
            geometry: { type: 'Point', coordinates: [longitude, latitude] },
        })
    })

    return { type: 'FeatureCollection', features }
}
