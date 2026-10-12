import type { Map } from 'maplibre-gl'

type PlaceIcon = {
    label: string
    color: string
    size: number
    artwork: string
}

// A white badge keeps each small drawing readable over streets and shadows.
// The SVG artwork is local: the map needs no icon font or remote sprite service.
export const PLACE_ICONS = {
    'poi-cafe': {
        label: 'Café', color: '#b5793f', size: 28,
        artwork: '<path d="M7.5 10.5h11v5a4 4 0 0 1-4 4h-3a4 4 0 0 1-4-4z"/>'
            + '<path d="M18.5 11.5h1a2.5 2.5 0 0 1 0 5h-1M6.5 21h14M10 6v2m4-2v2"/>',
    },
    'poi-restaurant': {
        label: 'Restaurant', color: '#b76c38', size: 28,
        artwork: '<path d="M8 6.5v5m3-5v5m-6-5v5a3 3 0 0 0 6 0M8 14.5v7"/>'
            + '<path d="M21 6.5c-4 1-5 4-5 8h5M21 6.5v15"/>',
    },
    'poi-shop': {
        label: 'Shop', color: '#906182', size: 28,
        artwork: '<path d="M7 10.5h14l1 11H6z"/>'
            + '<path d="M10.5 11V9a3.5 3.5 0 0 1 7 0v2"/>',
    },
    'poi-mall': {
        label: 'Shopping mall', color: '#7b5d88', size: 28,
        artwork: '<path d="M6 21.5V9.5l8-4 8 4v12H6zM11 21.5v-5h6v5M5 11h18"/>'
            + '<path d="M9 13.5h1m3.5 0h1m3.5 0h1"/>',
    },
    'poi-hospital': {
        label: 'Hospital', color: '#328575', size: 28,
        artwork: '<path d="M11 6.5h6V11h4.5v6H17v4.5h-6V17H6.5v-6H11z"'
            + ' fill="currentColor" stroke="none"/>',
    },
    'poi-pharmacy': {
        label: 'Pharmacy', color: '#3a8e87', size: 28,
        artwork: '<path d="m10 18 8-8m-6 10-4-4a4 4 0 0 1 0-6l2-2a4 4 0 0 1 6 0l4 4a4 4 0 0 1 0 6l-2 2a4 4 0 0 1-6 0z"/>',
    },
    'poi-school': {
        label: 'School', color: '#667db5', size: 28,
        artwork: '<path d="m5 10 9-4 9 4-9 4-9-4zM8.5 12v5c3.5 2.5 7.5 2.5 11 0v-5M23 10v8"/>'
            + '<path d="M21.5 19.5h3"/>',
    },
    'poi-hotel': {
        label: 'Hotel', color: '#886694', size: 28,
        artwork: '<path d="M6 8v14m0-5h16v5M22 17v-5h-8v5M6 20h16"/>'
            + '<rect x="8" y="11" width="4" height="4" rx="1"/>',
    },
    'poi-park': {
        label: 'Park', color: '#4d8b60', size: 28,
        artwork: '<path d="m14 5-6 7h3l-5 6h16l-5-6h3l-6-7zM14 18v5"/>'
            + '<path d="M10 23h8"/>',
    },
    'poi-bank': {
        label: 'Bank', color: '#697e96', size: 28,
        artwork: '<path d="m5 10 9-5 9 5H5zM7 13v6m4.5-6v6m5-6v6m4.5-6v6M5 22h18M7 19.5h14"/>',
    },
    'poi-transit': {
        label: 'Transit', color: '#477dc0', size: 28,
        artwork: '<rect x="7" y="5.5" width="14" height="16" rx="3"/>'
            + '<path d="M7 14h14M10 21.5v2m8-2v2M11 8.5h6"/>'
            + '<circle cx="10.5" cy="17.5" r="1" fill="currentColor" stroke="none"/>'
            + '<circle cx="17.5" cy="17.5" r="1" fill="currentColor" stroke="none"/>',
    },
    'poi-landmark': {
        label: 'Landmark', color: '#8d73a3', size: 28,
        artwork: '<path d="M6 22V11l8-5 8 5v11M10 22v-7a4 4 0 0 1 8 0v7M5 22h18"/>'
            + '<circle cx="14" cy="10" r="1.5" fill="#d0a748" stroke="none"/>',
    },
    'poi-building': {
        label: 'Building', color: '#718198', size: 28,
        artwork: '<path d="M7 22V6h10v16M17 12h5v10H6M10 9h1m2.5 0h1M10 13h1m2.5 0h1M10 17h1m2.5 0h1M11 22v-2h3v2"/>',
    },
    'landmark-baiterek': {
        label: 'Baiterek', color: '#8d7bb1', size: 40,
        artwork: '<path d="M15.5 16c1.5 6 3 11 1.5 17h6c-1.5-6 0-11 1.5-17M18 17l2 14 2-14M15 34h10"/>'
            + '<circle cx="20" cy="11.5" r="5.5" fill="#e6c465" stroke="#b28d32"/>'
            + '<path d="M17 9.5a3.5 3.5 0 0 1 3-1.5" stroke="#fff5cf"/>',
    },
    'landmark-khan-shatyr': {
        label: 'Khan Shatyr', color: '#7b6ca3', size: 40,
        artwork: '<path d="M7 31.5C14 22 19 18 25 9c.5 9 4 15 8 22.5H7z"/>'
            + '<path d="m25 5-.5 6M9 33h23M25 11c-2 8-4 14-5 20" stroke="#c4a043"/>'
            + '<path d="M12 30c5-6 9-11 12-17M27 19l2 11" stroke-width="1.1"/>',
    },
} satisfies Record<string, PlaceIcon>

export type PlaceIconId = keyof typeof PLACE_ICONS

const PIXEL_RATIO = 2

const badgeSvg = ({ color, size, artwork }: PlaceIcon) => {
    const centre = size / 2
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${size * PIXEL_RATIO}"`
        + ` height="${size * PIXEL_RATIO}" viewBox="0 0 ${size} ${size}">`
        + `<circle cx="${centre}" cy="${centre}" r="${centre - 1}" fill="#fff"`
        + ` stroke="${color}" stroke-opacity=".24" stroke-width="1.2"/>`
        + `<g fill="none" color="${color}" stroke="${color}" stroke-width="1.7"`
        + ` stroke-linecap="round" stroke-linejoin="round">${artwork}</g></svg>`
}

function rasterizeBadge(icon: PlaceIcon, signal: AbortSignal): Promise<ImageData | null> {
    if (signal.aborted) return Promise.resolve(null)

    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(new Blob([badgeSvg(icon)], { type: 'image/svg+xml' }))
        const image = new Image()
        let finished = false

        const cleanup = () => {
            image.onload = null
            image.onerror = null
            signal.removeEventListener('abort', abort)
            URL.revokeObjectURL(url)
        }
        const abort = () => {
            if (finished) return
            finished = true
            cleanup()
            image.src = ''
            resolve(null)
        }
        const fail = (error: unknown) => {
            if (finished) return
            finished = true
            cleanup()
            reject(error)
        }

        image.onload = () => {
            if (signal.aborted) { abort(); return }
            try {
                const canvas = document.createElement('canvas')
                canvas.width = canvas.height = icon.size * PIXEL_RATIO
                const context = canvas.getContext('2d')
                if (!context) throw new Error('A canvas is required to render map place icons.')
                context.drawImage(image, 0, 0, canvas.width, canvas.height)
                const pixels = context.getImageData(0, 0, canvas.width, canvas.height)
                finished = true
                cleanup()
                resolve(pixels)
            } catch (error) {
                fail(error)
            }
        }
        image.onerror = () => fail(new Error(`Could not render the ${icon.label} map icon.`))
        signal.addEventListener('abort', abort, { once: true })
        image.src = url
    })
}

/** Install after style.load; abort the signal before removing the map. */
export async function installPlaceIcons(map: Map, signal: AbortSignal): Promise<void> {
    if (signal.aborted || !map.getStyle()) return

    await Promise.all(Object.entries(PLACE_ICONS).map(async ([id, icon]) => {
        if (signal.aborted || map.hasImage(id)) return
        const pixels = await rasterizeBadge(icon, signal)
        // Loading an SVG is asynchronous. The map may have unmounted meanwhile.
        if (!pixels || signal.aborted || !map.getStyle()) return
        if (!map.hasImage(id)) map.addImage(id, pixels, { pixelRatio: PIXEL_RATIO })
    }))
}
