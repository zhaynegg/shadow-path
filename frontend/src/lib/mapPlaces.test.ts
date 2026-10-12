import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import type { SearchEntry, SearchIndex } from '../api/geocode'
import { mapPlaces } from './mapPlaces'

const real = JSON.parse(readFileSync('public/search-index.json', 'utf8')) as SearchIndex
const index = (...entries: SearchEntry[]): SearchIndex => ({ ...real, entries })
const entry = (name: string, detail: string, point: SearchEntry['p'] = [51.13, 71.43]): SearchEntry => ({
    n: name, d: detail, p: point, c: name.toLowerCase(), l: name.toLowerCase(),
})

const BAITEREK: SearchEntry['p'] = [51.12829, 71.43046]
const KHAN_SHATYR: SearchEntry['p'] = [51.1325, 71.40386]

describe('mapPlaces', () => {
    it('uses GeoJSON coordinate order and preserves search IDs after removing streets', () => {
        const collection = mapPlaces(index(
            entry('Dostyq', 'Street'),
            entry('Coffee House', 'Cafe · Dostyq', [51.12, 71.44]),
        ))

        expect(collection.type).toBe('FeatureCollection')
        expect(collection.features).toHaveLength(1)
        expect(collection.features[0]).toMatchObject({
            type: 'Feature',
            id: 1,
            properties: { id: 1, name: 'Coffee House', detail: 'Cafe · Dostyq', category: 'cafe' },
            geometry: { type: 'Point', coordinates: [71.44, 51.12] },
        })
    })

    it('reserves landmark identities for the monument and mall, even at a shared location', () => {
        const places = mapPlaces(index(
            entry('Бәйтерек', 'Monument · Нұржол', BAITEREK),
            entry('Бәйтерек', 'Station', BAITEREK),
            entry('Монумент Байтерек', 'Platform', BAITEREK),
            entry('Байтерек', 'Building', BAITEREK),
            entry('Хан Шатыр', 'Mall · Тұран', KHAN_SHATYR),
            entry('Хан Шатыр', 'Platform', KHAN_SHATYR),
            entry('Хан Шатыр', 'Cafe', KHAN_SHATYR),
        )).features

        expect(places[0].properties).toMatchObject({
            name: 'Baiterek', icon: 'landmark-baiterek', category: 'landmark', priority: 0, minZoom: 11.5,
        })
        expect(places[4].properties).toMatchObject({
            name: 'Khan Shatyr', icon: 'landmark-khan-shatyr', priority: 0, minZoom: 11.5,
        })
        for (const at of [1, 2, 3, 5, 6]) {
            expect(places[at].properties.icon).not.toMatch(/^landmark-/)
            expect(places[at].properties.priority).toBeGreaterThan(0)
        }
        expect(places[1].properties.name).toBe('Бәйтерек')
        expect(places[5].properties).toMatchObject({ category: 'transit', minZoom: 16 })
    })

    it('normalizes known landmark aliases but rejects namesakes elsewhere in the city', () => {
        const places = mapPlaces(index(
            entry('BAYTEREK', 'Monument', BAITEREK),
            entry('KHAN-SHATYR', 'Mall', KHAN_SHATYR),
            entry('Baiterek', 'Monument', [51.18, 71.45]),
            entry('Khan Shatyr', 'Mall', [51.2, 71.5]),
        )).features

        expect(places[0].properties.icon).toBe('landmark-baiterek')
        expect(places[1].properties.icon).toBe('landmark-khan-shatyr')
        expect(places[2].properties.icon).toBe('poi-landmark')
        expect(places[3].properties.icon).toBe('poi-mall')
    })

    it('groups by the cached kind and gives unknown kinds a quiet building fallback', () => {
        const places = mapPlaces(index(
            entry('Mega Mall', 'Cafe · Main street'),
            entry('Medical centre', 'Clinic'),
            entry('Academy', 'University'),
            entry('New business', 'Coworking · Main street'),
        )).features

        expect(places[0].properties).toMatchObject({ category: 'cafe', icon: 'poi-cafe' })
        expect(places[1].properties).toMatchObject({ category: 'hospital', minZoom: 14 })
        expect(places[2].properties.category).toBe('school')
        expect(places[3].properties).toMatchObject({
            name: 'New business', detail: 'Coworking · Main street',
            category: 'building', icon: 'poi-building', minZoom: 17,
        })
        expect(places[3].properties.priority).toBeGreaterThan(places[0].properties.priority)
    })

    it('surfaces cafes at the initial zoom and keeps smaller medical and childcare points quieter', () => {
        const places = mapPlaces(index(
            entry('Coffee House', 'Cafe'),
            entry('Lunch', 'Restaurant'),
            entry('City hospital', 'Hospital'),
            entry('Local clinic', 'Clinic'),
            entry('Dental practice', 'Dentist'),
            entry('Nursery', 'Kindergarten'),
            entry('School', 'School'),
            entry('University', 'University'),
        )).features

        expect(places[0].properties.minZoom).toBeLessThanOrEqual(14)
        expect(places[1].properties.minZoom).toBeLessThanOrEqual(14)
        expect(places[2].properties).toMatchObject({ minZoom: 13, priority: 1 })
        expect(places[3].properties).toMatchObject({ minZoom: 14, priority: 3 })
        for (const at of [4, 5]) {
            expect(places[at].properties).toMatchObject({ minZoom: 15, priority: 5 })
            expect(places[at].properties.priority).toBeGreaterThan(places[0].properties.priority)
        }
        expect(places[4].properties.icon).toBe(places[2].properties.icon)
        expect(places[6].properties.minZoom).toBe(14)
        expect(places[7].properties.minZoom).toBe(14)
    })

    it('keeps distinct branches of a chain with their original IDs', () => {
        const places = mapPlaces(index(
            entry('Magnum', 'Supermarket', [51.13, 71.43]),
            entry('Magnum', 'Supermarket', [51.16, 71.48]),
        )).features

        expect(places.map(place => place.id)).toEqual([0, 1])
        expect(places[0].geometry.coordinates).not.toEqual(places[1].geometry.coordinates)
    })

    it('omits unnamed or invalid coordinates instead of emitting unusable map points', () => {
        const places = mapPlaces(index(
            entry('  ', 'Cafe'),
            entry('Broken latitude', 'Shop', [Number.NaN, 71.43]),
            entry('Outside coordinate range', 'Shop', [51.13, 200]),
            entry('Valid', 'Shop'),
        )).features

        expect(places.map(place => place.id)).toEqual([3])
    })
})

describe('the shipped places index', () => {
    const places = mapPlaces(real).features

    it('provides thousands of named destinations without leaking streets or losing IDs', () => {
        const expected = real.entries.filter(row => row.d.split('·')[0].trim().toLowerCase() !== 'street')
        expect(places).toHaveLength(expected.length)
        expect(places.length).toBeGreaterThan(5000)
        expect(new Set(places.map(place => place.id)).size).toBe(places.length)
        expect(new Set(places.map(place => place.properties.category)).size).toBeGreaterThan(10)
        for (const place of places) {
            expect(place.geometry.coordinates).toEqual([
                real.entries[place.properties.id].p[1], real.entries[place.properties.id].p[0],
            ])
        }
    })

    it('has exactly the two intended special landmarks and leaves their transit namesakes ordinary', () => {
        const landmarks = places.filter(place => place.properties.icon.startsWith('landmark-'))
        expect(landmarks.map(place => place.properties.name).sort()).toEqual(['Baiterek', 'Khan Shatyr'])
        expect(landmarks.find(place => place.properties.name === 'Baiterek')?.geometry.coordinates)
            .toEqual([BAITEREK[1], BAITEREK[0]])
        expect(landmarks.find(place => place.properties.name === 'Khan Shatyr')?.geometry.coordinates)
            .toEqual([KHAN_SHATYR[1], KHAN_SHATYR[0]])

        const transitNamesakes = places.filter(place => place.properties.category === 'transit'
            && /байтерек|әйтерек|хан шатыр/i.test(place.properties.name))
        expect(transitNamesakes.length).toBeGreaterThanOrEqual(4)
        expect(transitNamesakes.every(place => place.properties.icon === 'poi-transit')).toBe(true)
    })
})
