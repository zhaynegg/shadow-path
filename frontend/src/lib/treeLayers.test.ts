import { statSync } from 'node:fs'
import { open } from 'node:fs/promises'
import { validateStyleMin } from '@maplibre/maplibre-gl-style-spec'
import { PMTiles, TileType, type Source } from 'pmtiles'
import { describe, expect, it } from 'vitest'
import { TREE_SOURCE, treeLayers } from './treeLayers'

const asset = new URL('../../public/tree-canopy.pmtiles', import.meta.url)

// Read the same byte ranges the browser's PMTiles protocol requests, without
// loading the entire city archive into the test process.
const source: Source = {
    getKey: () => asset.href,
    async getBytes(offset, length) {
        const file = await open(asset, 'r')
        try {
            const bytes = new Uint8Array(length)
            const { bytesRead } = await file.read(bytes, 0, length, offset)
            return { data: bytes.slice(0, bytesRead).buffer }
        } finally {
            await file.close()
        }
    },
}

const archive = new PMTiles(source)

describe('permanent tree canopy asset', () => {
    it('ships a nonempty vector archive covering Astana at the initial map zoom', async () => {
        const file = statSync(asset)
        expect(file.isFile()).toBe(true)
        expect(file.size).toBeGreaterThan(127)

        const header = await archive.getHeader()
        expect(header.specVersion).toBe(3)
        expect(header.tileType).toBe(TileType.Mvt)
        expect(header.numAddressedTiles).toBeGreaterThan(0)
        expect(header.numTileContents).toBeGreaterThan(0)
        expect(header.minZoom).toBeLessThanOrEqual(14)
        expect(header.maxZoom).toBeGreaterThanOrEqual(14)
        expect(header.maxZoom).toBeLessThanOrEqual(24)

        // Broad geographic bounds catch swapped axes and unrelated exports,
        // while leaving the exporter free to trim unused edges of the city.
        expect(header.minLon).toBeGreaterThan(70)
        expect(header.minLon).toBeLessThan(71.4704)
        expect(header.maxLon).toBeGreaterThan(71.4704)
        expect(header.maxLon).toBeLessThan(73)
        expect(header.minLat).toBeGreaterThan(50)
        expect(header.minLat).toBeLessThan(51.1605)
        expect(header.maxLat).toBeGreaterThan(51.1605)
        expect(header.maxLat).toBeLessThan(52)

        expect(header.jsonMetadataOffset + header.jsonMetadataLength).toBeLessThanOrEqual(file.size)
        expect(header.tileDataOffset + (header.tileDataLength ?? Infinity)).toBeLessThanOrEqual(file.size)
    })

    it('contains every source layer requested by the map', async () => {
        const metadata = await archive.getMetadata() as { vector_layers?: { id: string }[] }
        expect(Array.isArray(metadata.vector_layers)).toBe(true)
        const available = metadata.vector_layers?.map(layer => layer.id) ?? []
        const layers = treeLayers()
        expect(layers.length).toBeGreaterThan(0)
        for (const layer of layers) {
            expect('source' in layer && layer.source).toBe(TREE_SOURCE)
            expect('source-layer' in layer && layer['source-layer']).toBeTypeOf('string')
            if ('source-layer' in layer) expect(available).toContain(layer['source-layer'])
        }
    })

    it('defines a valid MapLibre vector style', () => {
        const errors = validateStyleMin({
            version: 8,
            sources: {
                [TREE_SOURCE]: { type: 'vector', url: 'pmtiles:///tree-canopy.pmtiles' },
            },
            layers: treeLayers(),
        })
        expect(errors.map(error => error.message)).toEqual([])
    })
})
