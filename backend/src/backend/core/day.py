"""One date's shade, arranged so a walk can be priced as it crosses it.

`scores.py` reads one stamp off disk. This stacks a day of them and adds the
one thing a moving walker needs that a standing one does not: a rule for which
stamp a given minute of the day belongs to.

The night row at the bottom is not padding. Below the horizon nothing casts a
shadow and every street is equally unlit, which the rest of the codebase already
spells as shade_fraction 1.0 -- see score_edges with no geometry. A walk that
sets off at 17:40 and arrives after sunset runs off the end of the daylight
stamps, and the honest thing to price those last streets at is dusk, not the
shadows of an hour ago.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

MINUTES_IN_DAY = 1440

# What every edge is worth once the sun is down. Not a shadow -- the absence of
# a sun to cast one.
NIGHT_SHADE = 1.0


@dataclass(frozen=True)
class Day:
    """Shade per edge at every stamp of one date, and the clock to read it by."""

    # The daylight stamps, ascending. One shorter than `shade` has rows when
    # this is a whole day, because the last row is night and night is not a
    # stamp anybody built tiles for.
    times: tuple[dt.time, ...]

    # (rows, edges), float32. Finer than the quantity deserves -- this is a
    # fraction of a street -- and half the memory of the alternative.
    shade: np.ndarray

    # Minute of the day -> row of `shade`. A lookup rather than a search,
    # because the search asks this once per edge it relaxes.
    stamp_of_minute: np.ndarray

    @property
    def crosses_stamps(self) -> bool:
        """Whether a walk over this can be priced at more than one sun."""
        return len(self.shade) > 1


def clock_table(times: list[dt.time]) -> np.ndarray:
    """Which stamp each minute of the day belongs to, night for the rest.

    A minute belongs to the stamp nearest it, which is the same rule the map
    picks a tileset by -- what is on screen and what the router weighted by
    should be the same sun. The day ends half a step past the last stamp and
    begins half a step before the first, mirrored from their neighbours: on 13
    September that puts first light at 05:50 and dusk at 18:30, within ten
    minutes of the real ones, and it costs no astronomy to say so.
    """
    minutes = [at.hour * 60 + at.minute for at in times]
    night = len(minutes)

    table = np.full(MINUTES_IN_DAY + 1, night, dtype="int16")
    for index, minute in enumerate(minutes):
        # Mirror the neighbouring gap at each end, so the first and last stamps
        # reach as far outwards as they do inwards.
        step = 60 if len(minutes) == 1 else minutes[1] - minutes[0]
        before = minutes[index - 1] if index else minute - step

        step = 60 if len(minutes) == 1 else minutes[-1] - minutes[-2]
        after = minutes[index + 1] if index < night - 1 else minute + step

        first = max((minute + before) // 2, 0)
        last = min((minute + after) // 2, MINUTES_IN_DAY)
        table[first:last + 1] = index

    return table


def empty_shade(times: list[dt.time], edges: int) -> np.ndarray:
    """The day's table, allocated once, with the night row already written.

    For a caller that reads the stamps one at a time off disk. Building the
    same table by collecting the rows and stacking them costs three copies of
    the whole day at once -- the list, `asarray`, then `vstack` -- and the day
    is 14 MB on the 15 km graph. Filling a row at a time costs one.
    """
    shade = np.empty((len(times) + 1, edges), dtype="float32")
    shade[-1] = NIGHT_SHADE
    return shade


def day_of(shade: np.ndarray, times: list[dt.time]) -> Day:
    """A filled table and its stamps, as the thing the router walks over."""
    if not times:
        raise ValueError("a day with no daylight in it cannot be walked through")
    return Day(times=tuple(times), shade=shade, stamp_of_minute=clock_table(times))


def across_the_day(rows: list[np.ndarray], times: list[dt.time]) -> Day:
    """A day of stamps, with night underneath them.

    The collect-then-stack form, kept for callers that already hold every row
    -- the tile export scores them in memory rather than reading them back.
    """
    if not times:
        raise ValueError("a day with no daylight in it cannot be walked through")

    lit = np.asarray(rows, dtype="float32")
    night = np.full((1, lit.shape[1]), NIGHT_SHADE, dtype="float32")

    return Day(
        times=tuple(times),
        shade=np.vstack([lit, night]),
        stamp_of_minute=clock_table(times),
    )


def at_one_stamp(shade: np.ndarray, at: dt.time) -> Day:
    """One stamp, standing in for the whole day.

    The fallback for a checkout with no precomputed scores, where building a
    day would mean computing two dozen citywide shadow fields at half a minute
    each. Every minute maps to the single row, so a walk over this is priced at
    the sun it set off under from end to end -- which is exactly what the router
    did before it could do better, and is still a true answer to a slightly
    smaller question.
    """
    return Day(
        times=(at,),
        shade=np.asarray(shade, dtype="float32").reshape(1, -1),
        stamp_of_minute=np.zeros(MINUTES_IN_DAY + 1, dtype="int16"),
    )
