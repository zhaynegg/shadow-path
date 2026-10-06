"""Reject missing or incompatible routing scores before publishing a build.

    uv run python scripts/validate_runtime_assets.py [--cache-dir PATH] [--date YYYY-MM-DD]

Only the street edge index and one score column are read. No graph, node table,
geometry, or citywide shadow calculation is needed.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import pandas as pd

from backend.config import CACHE_DIR, GRAPH_RADIUS, LAT, LON, TZ
from backend.core import scores, streets
from backend.core.solar import daylight_times


def release_date(cache_dir: Path) -> dt.date:
    """Infer the date only when the downloaded score bundle names one date."""
    root = cache_dir / "scored" / f"{GRAPH_RADIUS:.0f}m"
    dates = []
    if root.exists():
        for entry in sorted(root.iterdir()):
            if entry.is_dir():
                try:
                    dates.append(dt.date.fromisoformat(entry.name))
                except ValueError:
                    continue
    if len(dates) != 1:
        raise ValueError(
            f"Expected one routing-score date in {root}, found {len(dates)}. "
            "Fetch a complete scored.tar.gz release, or pass --date YYYY-MM-DD."
        )
    return dates[0]


def validate(cache_dir: Path, date: dt.date | None = None) -> tuple[dt.date, int, int]:
    """Return (date, stamps, edges), or explain why the API would refuse it."""
    date = date or release_date(cache_dir)
    times = daylight_times(LAT, LON, date, TZ)
    if not times:
        raise ValueError(f"No daylight stamps exist for {date}.")

    absent = scores.missing(cache_dir, GRAPH_RADIUS, date, times)
    if absent:
        stamps = ", ".join(at.strftime("%H:%M") for at in absent)
        raise ValueError(f"Missing routing scores for {date}: {stamps}.")

    # An empty column selection still reads the saved (u, v, key) index, without
    # loading lengths or shapes. This is the same index graph_edges() routes on.
    path = streets.edges_path(cache_dir, GRAPH_RADIUS)
    try:
        index = pd.read_parquet(path, columns=[]).index
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read the street edge index at {path}: {exc}") from exc
    if not len(index):
        raise ValueError(f"The street edge index at {path} is empty.")

    for at in times:
        try:
            shade = scores.load(cache_dir, GRAPH_RADIUS, date, at, index)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid routing scores for {date} at {at:%H:%M}: {exc}") from exc
        if shade is None:
            raise ValueError(
                f"Routing scores for {date} at {at:%H:%M} are unreadable or do not "
                f"match the street network in {path} (requires at least "
                f"{scores.MIN_COVERAGE:.0%} edge coverage). Rebuild with "
                "backend/scripts/export_shadow_tiles.py from this checkout."
            )
        if not shade.between(0.0, 1.0).all():
            raise ValueError(f"Invalid shade fractions for {date} at {at:%H:%M}; expected 0 to 1.")

    return date, len(times), len(index)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--date", type=dt.date.fromisoformat)
    args = parser.parse_args()
    try:
        date, stamps, edges = validate(args.cache_dir, args.date)
    except ValueError as exc:
        parser.exit(1, f"Runtime asset validation failed: {exc}\n")
    print(f"Validated routing scores: {date}, {stamps} stamps, {edges:,} street edges.")


if __name__ == "__main__":
    main()
