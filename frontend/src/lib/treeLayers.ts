import type { LayerSpecification } from 'maplibre-gl'

export const TREE_SOURCE = 'tree-canopy'

/** Actual canopy footprints, visible independently of the season or shadow clock. */
export function treeLayers(): LayerSpecification[] {
    return [
        {
            id: 'tree-canopy-fill',
            type: 'fill',
            source: TREE_SOURCE,
            'source-layer': TREE_SOURCE,
            minzoom: 11,
            paint: {
                'fill-color': '#76a17c',
                'fill-opacity': ['interpolate', ['linear'], ['zoom'],
                    11, 0.45, 14, 0.55, 16, 0.65, 18, 0.7],
                'fill-antialias': true,
            },
        },
        {
            id: 'tree-canopy-outline',
            type: 'line',
            source: TREE_SOURCE,
            'source-layer': TREE_SOURCE,
            minzoom: 11,
            layout: { 'line-join': 'round' },
            paint: {
                'line-color': '#4f825b',
                // Fine crown edges appear as the map gets close enough to read them.
                'line-opacity': ['interpolate', ['linear'], ['zoom'],
                    11, 0, 14, 0.45, 16, 0.65, 18, 0.7],
                'line-width': ['interpolate', ['linear'], ['zoom'],
                    11, 0.2, 14, 0.4, 16, 0.65, 18, 0.9],
            },
        },
    ]
}
