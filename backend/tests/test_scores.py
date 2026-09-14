"""The precomputed-score cache: it must be exact, or absent."""

import datetime as dt

import pandas as pd
import pytest

from backend.core import scores

DATE = dt.date(2026, 9, 13)
AT = dt.time(8, 20)
RADIUS = 1700


def frame(index, values):
    """What score_edges_layered hands to scores.save: a frame with the column."""
    return pd.DataFrame({scores.COLUMN: values}, index=index)


def index(n, start=0):
    """An osmnx-shaped (u, v, key) MultiIndex."""
    return pd.MultiIndex.from_tuples(
        [(i, i + 1, 0) for i in range(start, start + n)], names=["u", "v", "key"]
    )


def test_round_trip_preserves_the_fractions(tmp_path):
    idx = index(4)
    saved = frame(idx, [0.0, 0.25, 0.5, 1.0])

    scores.save(saved, tmp_path, RADIUS, DATE, AT)
    loaded = scores.load(tmp_path, RADIUS, DATE, AT, idx)

    # float32 on disk, so this is rounding rather than exactness.
    assert loaded is not None
    assert loaded.to_numpy() == pytest.approx([0.0, 0.25, 0.5, 1.0], abs=1e-6)


def test_missing_file_is_a_miss_not_an_error(tmp_path):
    """A checkout that has never run the export must still route."""
    assert scores.load(tmp_path, RADIUS, DATE, AT, index(2)) is None


def test_another_date_is_not_borrowed(tmp_path):
    """13:00 in June and 13:00 in December are different suns."""
    idx = index(2)
    scores.save(frame(idx, [0.5, 0.5]), tmp_path, RADIUS, DATE, AT)

    assert scores.load(tmp_path, RADIUS, dt.date(2026, 1, 5), AT, idx) is None


def test_another_radius_is_not_borrowed(tmp_path):
    """The edge index *is* the graph, so a different disc is a different file."""
    idx = index(2)
    scores.save(frame(idx, [0.5, 0.5]), tmp_path, RADIUS, DATE, AT)

    assert scores.load(tmp_path, 5000, DATE, AT, idx) is None


def test_a_file_for_a_different_graph_is_refused(tmp_path):
    """The failure this guard exists for.

    Zero-filling the edges a stale file does not mention would route as though
    those streets were in full sun -- a wrong answer, delivered confidently and
    silently. Refusing sends main.py down the slow path that is still right.
    """
    scores.save(frame(index(2), [0.5, 0.5]), tmp_path, RADIUS, DATE, AT)

    # A graph that shares almost nothing with the one the file was written for.
    assert scores.load(tmp_path, RADIUS, DATE, AT, index(200, start=1000)) is None


def test_mostly_matching_graph_is_still_used(tmp_path):
    """Above MIN_COVERAGE the file is good; the few strays read as full sun."""
    idx = index(200)
    scores.save(frame(idx, [1.0] * 200), tmp_path, RADIUS, DATE, AT)

    # 199 of 201 edges present: just over the threshold.
    loaded = scores.load(tmp_path, RADIUS, DATE, AT, index(201))

    assert loaded is not None
    assert loaded.iloc[-1] == 0.0


def test_prune_keeps_only_the_date_asked_for(tmp_path):
    idx = index(2)
    for date in (DATE, dt.date(2026, 1, 5), dt.date(2025, 7, 1)):
        scores.save(frame(idx, [0.5, 0.5]), tmp_path, RADIUS, date, AT)

    removed = scores.prune(tmp_path, RADIUS, DATE)

    assert [p.name for p in removed] == ["2025-07-01", "2026-01-05"]
    assert scores.load(tmp_path, RADIUS, DATE, AT, idx) is not None


def test_prune_on_a_cold_cache_is_not_an_error(tmp_path):
    assert scores.prune(tmp_path, RADIUS, DATE) == []


def test_an_index_of_a_different_shape_is_a_miss(tmp_path):
    """Not every mismatch is a near-miss.

    A caller holding a plain index -- a test fixture, or a graph built some
    other way -- is not asking for something this file can answer. Reindexing
    a (u, v, key) frame onto it raises inside pandas, and an exception here
    would take down a request that could simply have computed the field.
    """
    scores.save(frame(index(2), [0.5, 0.5]), tmp_path, RADIUS, DATE, AT)

    assert scores.load(tmp_path, RADIUS, DATE, AT, pd.RangeIndex(2)) is None


def test_missing_names_the_stamps_with_no_file(tmp_path):
    """What /api/day checks before it commits to twenty-four stamps.

    One missing stamp is a cache miss the API absorbs by computing the field
    itself. A day of them is twelve minutes of one request holding the process,
    which is why the scan asks first rather than finding out per stamp.
    """
    idx = index(2)
    present = [dt.time(6, 0), dt.time(7, 0)]
    for at in present:
        scores.save(frame(idx, [0.5, 0.5]), tmp_path, RADIUS, DATE, at)

    asked = [*present, dt.time(8, 0), dt.time(9, 0)]

    assert scores.missing(tmp_path, RADIUS, DATE, asked) == [dt.time(8, 0), dt.time(9, 0)]


def test_missing_is_empty_when_every_stamp_is_there(tmp_path):
    """The control: a check that always found something missing would pass the
    test above and decline every scan on a perfectly warm cache.
    """
    idx = index(2)
    asked = [dt.time(6, 0), dt.time(7, 0)]
    for at in asked:
        scores.save(frame(idx, [0.5, 0.5]), tmp_path, RADIUS, DATE, at)

    assert scores.missing(tmp_path, RADIUS, DATE, asked) == []


def test_missing_reads_the_same_radius_as_load(tmp_path):
    """The edge index *is* the graph, and scores_dir keys on the radius for it.
    A check that ignored the radius would wave through a day of files written
    for a different disc, and every one of them would then miss on load.
    """
    idx = index(2)
    scores.save(frame(idx, [0.5, 0.5]), tmp_path, RADIUS, DATE, AT)

    assert scores.missing(tmp_path, RADIUS * 2, DATE, [AT]) == [AT]
