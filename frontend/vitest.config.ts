import { defineConfig } from 'vitest/config'

export default defineConfig({
    test: {
        // No DOM needed: what is tested here is arithmetic over "HH:MM" strings,
        // and keeping it in node is what makes the suite worth running on every
        // push rather than only before a release.
        environment: 'node',

        // Deliberately neither Astana nor UTC. Two things in lib/stamps.ts are
        // about timezones -- cityMinutes names Asia/Almaty explicitly, and
        // prettyDate pins noon so a date string cannot slip a day -- and on a
        // machine already set to Astana both would pass whether or not they
        // were right. New York is five hours the other side of UTC, so dropping
        // either precaution turns a passing test red. This is also the honest
        // case: the shadows are Astana's whoever is looking at them.
        env: { TZ: 'America/New_York' },
    },
})
