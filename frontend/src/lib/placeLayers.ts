import type { LayerSpecification } from 'maplibre-gl'

export const PLACE_SOURCE = 'map-places'
export const PLACE_LAYER_IDS = ['map-place-icons', 'map-landmarks']

// Collision detection and category-specific zoom levels keep the city readable.
// The two landmark illustrations stay visible while ordinary destinations
// appear as the reader moves closer to the neighbourhood.
export function placeLayers(): LayerSpecification[] {
    return [
        {
            id: PLACE_LAYER_IDS[0],
            type: 'symbol',
            source: PLACE_SOURCE,
            minzoom: 13,
            filter: ['all', ['>', ['get', 'priority'], 0], ['>=', ['zoom'], ['get', 'minZoom']]],
            layout: {
                'icon-image': ['get', 'icon'],
                'icon-size': ['interpolate', ['linear'], ['zoom'], 13, 0.85, 16, 1],
                'icon-padding': 5,
                'symbol-sort-key': ['get', 'priority'],
                'text-field': ['step', ['zoom'], '', 15, ['get', 'name']],
                'text-font': ['Noto Sans Regular'],
                'text-size': 11,
                'text-max-width': 9,
                'text-anchor': 'top',
                'text-offset': [0, 1.5],
                'text-padding': 4,
                'text-optional': true,
            },
            paint: {
                'text-color': '#52606d',
                'text-halo-color': '#ffffff',
                'text-halo-width': 1.5,
            },
        },
        {
            id: PLACE_LAYER_IDS[1],
            type: 'symbol',
            source: PLACE_SOURCE,
            minzoom: 11.5,
            filter: ['==', ['get', 'priority'], 0],
            layout: {
                'icon-image': ['get', 'icon'],
                'icon-size': 1,
                'icon-allow-overlap': true,
                'icon-ignore-placement': false,
                'text-field': ['get', 'name'],
                'text-font': ['Noto Sans Medium'],
                'text-size': 12,
                'text-max-width': 10,
                'text-anchor': 'top',
                'text-offset': [0, 1.9],
                'text-padding': 5,
                'text-optional': true,
            },
            paint: {
                'text-color': '#66567b',
                'text-halo-color': '#ffffff',
                'text-halo-width': 2,
            },
        },
    ]
}
