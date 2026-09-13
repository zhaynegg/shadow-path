import { readFileSync } from 'node:fs'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fold, latinise, loadIndex, rank, searchPlaces } from './geocode'

// A miniature of the rules the exporter ships inside the index: enough Kazakh
// letters, enough of the alphabet and both collapses to exercise the folding
// without restating all fifty rows.
const FOLD = {
    chars: { 'қ': 'к', 'ә': 'а', 'ө': 'о', 'ұ': 'у', 'і': 'и', 'ё': 'е' },
    strip: ['көшесі', 'даңғылы', 'проспект', 'улица'],
    translit: {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ж': 'zh',
        'з': 'z', 'и': 'i', 'й': 'i', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
        'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
        'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'ы': 'i', 'ь': '', 'я': 'ia',
    },
    collapse: [['kh', 'h'], ['y', 'i']] as [string, string][],
}

const entry = (n: string, d = 'Street') =>
    ({ n, d, p: [51.16, 71.47] as [number, number], c: fold(n, FOLD), l: latinise(n, FOLD) })

const index = (names: string[]) =>
    ({ entries: names.map(n => entry(n)), fold: FOLD, generated_at: '2026-09-14T00:00:00Z' })

describe('fold', () => {
    it('folds Kazakh letters onto the Russian ones people type', () => {
        // OSM holds Қабанбай; half the city types Кабанбай. Without this they
        // are simply different words.
        expect(fold('Қабанбай', FOLD)).toBe(fold('Кабанбай', FOLD))
        expect(fold('Нұржол', FOLD)).toBe(fold('Нуржол', FOLD))
    })

    it('drops the street-type words nobody types', () => {
        expect(fold('Қабанбай Батыр даңғылы', FOLD)).toBe('кабанбай батыр')
        expect(fold('проспект Кабанбай Батыра', FOLD)).toBe('кабанбай батыра')
    })

    it('throws away punctuation and doubled spaces', () => {
        expect(fold('  Әл-Фараби,  ', FOLD)).toBe('ал фараби')
    })
})

describe('latinise', () => {
    it('brings a Cyrillic name into the script it might be typed in', () => {
        expect(latinise('Байтерек', FOLD)).toBe('baiterek')
    })

    it('treats y and i as one vowel, kh and h as one consonant', () => {
        // Somebody types Bayterek for Байтерек and Khan for Хан. Both are
        // flattened rather than guessed at.
        expect(latinise('Bayterek', FOLD)).toBe(latinise('Baiterek', FOLD))
        expect(latinise('Khan Shatyr', FOLD)).toBe(latinise('Хан Шатыр', FOLD))
    })

    it('leaves a name already written in Latin alone', () => {
        expect(latinise('Mega', FOLD)).toBe('mega')
    })
})

describe('rank', () => {
    it('finds a name by either script', () => {
        const idx = index(['Байтерек', 'Хан Шатыр'])
        expect(rank(idx, 'Байтерек').map(p => p.label)).toEqual(['Байтерек'])
        expect(rank(idx, 'Baiterek').map(p => p.label)).toEqual(['Байтерек'])
    })

    it('matches on a fragment, the way a search box is used', () => {
        expect(rank(index(['Қабанбай Батыр даңғылы']), 'кабанб')).toHaveLength(1)
    })

    it('puts a match at the start of a word ahead of one in the middle', () => {
        // Typing "аб" should offer Абай before Кабанбай.
        const idx = index(['Кабанбай Батыр', 'Абай'])
        expect(rank(idx, 'аб')[0].label).toBe('Абай')
    })

    it('prefers the shorter name when both lead', () => {
        const idx = index(['Мега Силк Вей', 'Мега'])
        expect(rank(idx, 'мега')[0].label).toBe('Мега')
    })

    it('returns nothing for a place that is not in the city', () => {
        expect(rank(index(['Байтерек', 'Хан Шатыр']), 'Абу Даби')).toEqual([])
    })

    it('is empty for a query that folds away to nothing', () => {
        expect(rank(index(['Байтерек']), '...')).toEqual([])
    })
})

// Against the file the exporter actually wrote. The unit tests above share one
// fixture between the keys and the query, so they would pass even if the two
// halves had drifted apart; only this notices when the index on disk was folded
// by rules the matcher no longer applies.
describe('the index as shipped', () => {
    const real = JSON.parse(readFileSync('public/search-index.json', 'utf8'))
    const find = (q: string) => rank(real, q).map(p => p.label)

    it('was built with its folding rules inside it', () => {
        expect(real.fold.chars['қ']).toBe('к')
        expect(real.entries.length).toBeGreaterThan(5000)
    })

    it('finds a Kazakh-tagged street typed in Russian', () => {
        expect(find('Кабанбай').join(' ')).toContain('Қабанбай')
    })

    it('finds a Cyrillic name typed in Latin', () => {
        expect(find('Baiterek').join(' ')).toMatch(/[Бб]әйтерек/)
        expect(find('Khan Shatyr').join(' ')).toContain('Хан Шатыр')
    })

    it('finds a translated name through the OSM alias, not by transliteration', () => {
        // No fold turns "Жоғарғы сот" into "Supreme Court". OSM's own name:en
        // is indexed as a search key, which is why this works at all.
        expect(find('Supreme Court').join(' ')).toContain('Жоғарғы сот')
    })

    it('holds nothing outside the routable disc', () => {
        expect(find('Абу Даби')).toEqual([])
        expect(find('Алматы облысы')).toEqual([])
    })
})

describe('searchPlaces', () => {
    afterEach(() => vi.unstubAllGlobals())

    it('reports a missing index as something you can act on', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: false, status: 404, headers: { get: () => 'text/html' },
        }))
        await expect(searchPlaces('Байтерек')).rejects.toThrow('export_search_index.py')
    })

    it('does not remember that failure', async () => {
        // The fix is to run the exporter, not to reload the page.
        const body = JSON.parse(readFileSync('public/search-index.json', 'utf8'))
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            ok: true, status: 200,
            headers: { get: () => 'application/json' },
            json: () => Promise.resolve(body),
        }))
        await expect(loadIndex()).resolves.toHaveProperty('entries')
    })
})
