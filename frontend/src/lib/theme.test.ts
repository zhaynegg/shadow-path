import { describe, expect, it } from 'vitest'
import { pickTheme } from './theme'

describe('pickTheme', () => {
    it('follows the device when the reader has not chosen', () => {
        expect(pickTheme(null, false)).toBe('light')
        expect(pickTheme(null, true)).toBe('night')
    })

    it('keeps a choice even when the device disagrees', () => {
        expect(pickTheme('night', false)).toBe('night')
        expect(pickTheme('light', true)).toBe('light')
    })

    // Storage outlives the code that wrote it. A value this version does not
    // know is no choice, not a crash and not a third theme.
    it('treats an unknown stored value as no choice', () => {
        expect(pickTheme('dark', false)).toBe('light')
        expect(pickTheme('', true)).toBe('night')
    })
})
