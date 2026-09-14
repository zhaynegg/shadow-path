"""One date's shade, and the clock a moving walker reads it by."""

import datetime as dt

import numpy as np
import pytest

from backend.core.day import (
    MINUTES_IN_DAY,
    NIGHT_SHADE,
    across_the_day,
    at_one_stamp,
    clock_table,
)

# A September day, thinned: dawn cut into thirds the way daylight_times cuts a
# low sun, then whole hours once it is up.
TIMES = [dt.time(6, 0), dt.time(6, 20), dt.time(6, 40), dt.time(12, 0), dt.time(18, 0)]
EDGES = 4


def rows(*values):
    return [np.full(EDGES, v, dtype="float32") for v in values]


def test_a_minute_belongs_to_the_stamp_nearest_it():
    """The same rule the map picks a tileset by. What is on screen and what the
    router weighted the streets by have to be the same sun.
    """
    table = clock_table(TIMES)

    assert table[6 * 60] == 0        # 06:00 on the nose
    assert table[6 * 60 + 9] == 0    # 06:09 is nearer 06:00
    assert table[6 * 60 + 11] == 1   # 06:11 is nearer 06:20
    assert table[11 * 60 + 59] == 3  # noon's hour reaches back
    assert table[15 * 60] == 4       # and 15:00 is nearer 18:00 than 12:00


def test_the_day_ends_half_a_step_past_the_last_stamp():
    """Sunset is not the last stamp -- it is a little after it. Mirroring the
    neighbouring gap puts the edge of the day within ten minutes of the real
    one without doing any astronomy for it.
    """
    table = clock_table(TIMES)
    night = len(TIMES)

    # Dawn stamps are 20 minutes apart, so the day opens 10 minutes early.
    assert table[5 * 60 + 49] == night
    assert table[5 * 60 + 50] == 0

    # The last gap is six hours, so this end is coarse -- which is honest: a
    # day whose stamps stop at 18:00 does not know where sunset is.
    assert table[MINUTES_IN_DAY] == night


def test_a_walk_that_runs_past_dusk_lands_on_the_night_row():
    """Not padding. Below the horizon nothing casts a shadow and every street
    is equally unlit, which is what shade_fraction 1.0 already means everywhere
    else in the codebase.
    """
    day = across_the_day(rows(0.1, 0.2, 0.3, 0.4, 0.5), TIMES)

    assert day.shade.shape == (len(TIMES) + 1, EDGES)
    assert day.shade[-1].tolist() == [NIGHT_SHADE] * EDGES
    assert day.stamp_of_minute[23 * 60] == len(TIMES)


def test_the_stamps_keep_their_order_and_their_values():
    day = across_the_day(rows(0.1, 0.2, 0.3, 0.4, 0.5), TIMES)

    assert day.times == tuple(TIMES)
    assert [day.shade[i][0] for i in range(len(TIMES))] == pytest.approx(
        [0.1, 0.2, 0.3, 0.4, 0.5], abs=1e-6)


def test_one_stamp_covers_the_whole_day():
    """The cold-checkout fallback, and the old behaviour exactly: every minute
    points at the single row, so a walk over it is priced at the sun it set off
    under from end to end.
    """
    day = at_one_stamp(np.full(EDGES, 0.6, dtype="float32"), dt.time(13, 0))

    assert day.shade.shape == (1, EDGES)
    assert not day.crosses_stamps
    assert set(day.stamp_of_minute.tolist()) == {0}


def test_a_day_with_no_daylight_is_refused_rather_than_guessed():
    """A latitude this does not serve, or a bug upstream. Either way there is
    no sun to plan against and no honest row to invent.
    """
    with pytest.raises(ValueError, match="no daylight"):
        across_the_day([], [])
