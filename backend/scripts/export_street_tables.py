"""Reduce the walking graph to the two tables the API reads.

    uv run python scripts/export_street_tables.py

The API used to open walk-15000m.graphml at startup. That is 887 MB of
networkx on the 15 km disc, on top of 226 MB for the osmnx import, against a
server with 512 MB -- and the graph was never traversed as a graph anyway. It
was flattened into adjacency arrays once and then held, unread, for the life of
the process.

So the flattening happens here instead, in a script that is allowed to be
expensive, and what the API reads is two parquet tables: edges with their
length and shape, nodes with their coordinates. See core/streets.py for what is
kept and why.

Run this after anything that changes the graph -- a new GRAPH_RADIUS, a refetch
-- and commit the result. The output is tracked, because a fresh checkout has
no graphml to rebuild it from and building one means going back to Overpass.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import osmnx as ox

from backend.config import CACHE_DIR, GRAPH_RADIUS
from backend.core import streets
from backend.core.graph import load_graph


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--radius", type=float, default=GRAPH_RADIUS,
                        help="must match GRAPH_RADIUS, or the API will not find these")
    args = parser.parse_args()

    graph = load_graph(args.cache_dir, args.radius)
    nodes, edges = ox.graph_to_gdfs(graph)
    print(f"graph: {len(nodes):,} nodes, {len(edges):,} edges "
          f"within {args.radius / 1000:.0f} km")

    written = streets.save(args.cache_dir, args.radius, edges, nodes)

    # Named rather than summed: these are the files a deploy has to carry, and
    # the graphml they replace is 65 MB that a checkout does not have.
    for path in written:
        print(f"  {path.relative_to(args.cache_dir)}  {path.stat().st_size / 1e6:5.1f} MB")

    dropped = [c for c in edges.columns if c not in streets.EDGE_COLUMNS]
    print(f"\ndropped {len(dropped)} edge columns nothing reads: {', '.join(sorted(dropped))}")


if __name__ == "__main__":
    main()
