"""Shade fractions computed ahead of time, so routing does no geometry.

Scoring a stamp means unioning every shadow in the city and intersecting the
whole walking graph against it. At the radius that actually covers Astana that
is about half a minute, and it is the same half-minute for every caller, every
restart, forever -- while `scripts/export_shadow_tiles.py` is already computing
that exact field each night to draw the tiles, and throwing it away afterwards.

So the nightly run writes the answer down and the API reads it. What is left at
request time is a parquet read and A*.

The files are a cache, not a source: `load` returning None is an ordinary
outcome, and main.py falls back to computing the field itself. That keeps a
checkout with no precomputed scores working exactly as it did before -- slowly,
and correctly.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

# Below this share of the graph present in the file, it is not a cache miss but
# a mismatch -- a file written for a different graph than the one now loaded.
# Filling the gaps with zeroes would route as though those streets were in full
# sun, which is a wrong answer delivered confidently. Recomputing is slow and
# right, so the threshold is deliberately high.
MIN_COVERAGE = 0.99

COLUMN = "shade_fraction"


def scores_dir(cache_dir: Path, radius: float, date: dt.date) -> Path:
    """Where one date's scores live.

    Keyed by radius as well as date because the edge index *is* the graph: a
    file written for a 5 km disc indexes edges a 15 km one has renumbered
    nothing of, but covers a fraction of. Keeping them in separate directories
    means changing GRAPH_RADIUS can never silently read the wrong one.
    """
    return cache_dir / "scored" / f"{radius:.0f}m" / date.isoformat()


def scores_path(cache_dir: Path, radius: float, date: dt.date, at: dt.time) -> Path:
    return scores_dir(cache_dir, radius, date) / f"{at.hour:02d}{at.minute:02d}.parquet"


def save(scored, cache_dir: Path, radius: float, date: dt.date, at: dt.time) -> Path:
    """Write one stamp's shade fractions, indexed by osmnx's (u, v, key)."""
    path = scores_path(cache_dir, radius, date, at)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Geometry and length are already in the graph; storing them again would
    # multiply the file size for columns the reader throws away. float32 is far
    # finer than the quantity deserves -- this is a fraction of a street.
    frame = pd.DataFrame({COLUMN: scored[COLUMN].astype("float32")})
    frame.to_parquet(path, compression="zstd")
    return path


def load(cache_dir: Path, radius: float, date: dt.date, at: dt.time, index) -> pd.Series | None:
    """One stamp's shade fractions, aligned to `index`, or None to compute it.

    None means "no usable file": absent, unreadable, or written for a different
    graph. Every one of those is a reason to fall back rather than to fail --
    the field can always be recomputed, it is only expensive.
    """
    path = scores_path(cache_dir, radius, date, at)
    if not path.exists():
        return None

    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError):
        # A half-written file from an interrupted export is a cache miss, not a
        # crash -- the next run overwrites it. pyarrow raises ArrowInvalid for
        # a truncated or corrupt file, and that is a ValueError.
        return None

    # Reindexing across two differently shaped indexes raises rather than
    # reporting misses, so the shape is checked before it can. Everything this
    # function rejects has to leave by the same door: main.py reads None as
    # "compute it yourself", and an exception instead would take the API down
    # over a stale file it was perfectly able to ignore.
    if frame.index.nlevels != index.nlevels:
        return None

    try:
        aligned = frame[COLUMN].reindex(index)
    except (TypeError, ValueError):
        return None

    if aligned.notna().mean() < MIN_COVERAGE:
        return None
    return aligned.astype("float64").fillna(0.0)


def missing(cache_dir: Path, radius: float, date: dt.date,
            times: list[dt.time]) -> list[dt.time]:
    """Which of these stamps have no scores written for them.

    `load` returning None is an ordinary outcome for one stamp -- main.py
    computes that field itself and the caller waits half a minute. It is not an
    ordinary outcome for a whole day: twenty-four fallbacks is twelve minutes
    of one request holding the process, which is not a slow answer but an
    outage one caller can cause. So the day scan asks first and declines.

    Existence only. Reading two dozen files to find out whether they are worth
    reading costs more than the check is worth, and every other way a file can
    be unusable still leaves `load` free to reject it on the way past.
    """
    return [at for at in times if not scores_path(cache_dir, radius, date, at).exists()]


def prune(cache_dir: Path, radius: float, keep: dt.date) -> list[Path]:
    """Drop scores for every date but this one.

    Each date is tens of megabytes and only the one the tiles were built for is
    ever asked about -- the map sends the date it is showing, and that comes
    from the manifest this same run writes.
    """
    root = cache_dir / "scored" / f"{radius:.0f}m"
    if not root.exists():
        return []

    removed = []
    for old in sorted(root.iterdir()):
        if old.is_dir() and old.name != keep.isoformat():
            for file in old.iterdir():
                file.unlink()
            old.rmdir()
            removed.append(old)
    return removed
