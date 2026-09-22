"""The walk network as two tables, so the API never opens a graph.

The router needs three things from OSM's street graph: how long each edge is,
what shape it is (to draw the answer), and where each junction is (to snap a
click to one). None of that is a graph -- it is two tables, and `search.flatten`
rebuilds the adjacency from them in about a second.

The graph itself is expensive in exactly the way a server cannot afford.
Measured on the 15 km disc:

    ox.load_graphml("walk-15000m.graphml")          +887 MB
    these two parquet tables                         +120 MB
    the osmnx import alone, before reading anything  +226 MB

A networkx MultiDiGraph of 153k edges is a few million small Python objects,
and every one of them is a PyObject header before it is a number. The tables
are arrow buffers. Nothing is lost: the graph is still what `scripts/` work
from, and this is what it reduces to once nobody needs to traverse it as a
graph any more.

Tracked in git, unlike the graphml, for the same reason the buildings are:
a fresh checkout has no cache, and rebuilding this one means a 65 MB graphml
that is itself built from an Overpass fetch. Two tables of 8 MB pin the
snapshot and make a cold deploy possible at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import shapely


# Keyed by radius for the reason core/scores.py keys its files that way: the
# edge index *is* the graph. A table written for a 5 km disc numbers edges the
# 15 km one has renumbered nothing of, and the scores are indexed by that
# numbering. Separate directories mean changing GRAPH_RADIUS can never quietly
# read the wrong one.
def tables_dir(cache_dir: Path, radius: float) -> Path:
    return cache_dir / "streets" / f"{radius:.0f}m"


def edges_path(cache_dir: Path, radius: float) -> Path:
    return tables_dir(cache_dir, radius) / "edges.parquet"


def nodes_path(cache_dir: Path, radius: float) -> Path:
    return tables_dir(cache_dir, radius) / "nodes.parquet"


def shapes_path(cache_dir: Path, radius: float) -> Path:
    return tables_dir(cache_dir, radius) / "shapes.npz"


@dataclass(frozen=True)
class Shapes:
    """Every edge's line, as one coordinate buffer and the offsets into it.

    The edges used to arrive as a geopandas geometry column, which is 152,734
    shapely LineStrings -- 84 MB of GEOS objects to hold 490,368 coordinate
    pairs that weigh 7.8 MB. Each one carries a C struct, a Python wrapper and
    an allocation header to say the same thing twice.

    Nothing routes on the shapes. The search runs on lengths and adjacency, and
    the only reader is `routing.geometry`, drawing the fifty-odd edges a walk
    actually used -- which is a slice of this buffer. So they are kept flat and
    turned back into lines one route at a time.
    """

    xy: np.ndarray       # (coordinates, 2) float64, every edge end to end
    start: np.ndarray    # (edges + 1,) where each edge begins in xy

    def __len__(self) -> int:
        return len(self.start) - 1

    def line(self, edge: int) -> np.ndarray:
        """One edge's coordinates, as a view rather than a copy."""
        return self.xy[self.start[edge]:self.start[edge + 1]]


def flatten_shapes(geoms) -> Shapes:
    """A geometry column, reduced to the two arrays that describe it."""
    xy, index = shapely.get_coordinates(geoms, return_index=True)
    counts = np.bincount(index, minlength=len(geoms))
    start = np.zeros(len(geoms) + 1, dtype="int64")
    np.cumsum(counts, out=start[1:])
    return Shapes(xy=xy, start=start)


# What routing reads off an edge. osmnx hands back sixteen columns; the search
# takes length and the (u, v, key) index, drawing the answer takes the shape,
# and nothing anywhere reads the other thirteen -- 55 MB of street names and
# lane counts that no request touches. The shape leaves in shapes.npz rather
# than in this table, so the frame stays free of geometry entirely.
EDGE_COLUMNS = ["length", "geometry"]
TABLE_COLUMNS = ["length"]

# And off a node: where it is. Deliberately not the geometry column -- `snap`
# measures to a point, which is two floats and a subtraction, and 52,772
# shapely Points cost 20 MB to say the same thing more slowly.
NODE_COLUMNS = ["x", "y"]


def save(cache_dir: Path, radius: float, edges, nodes) -> list[Path]:
    """Write the tables, trimmed to what the router actually reads."""
    out = tables_dir(cache_dir, radius)
    out.mkdir(parents=True, exist_ok=True)

    files = [edges_path(cache_dir, radius), nodes_path(cache_dir, radius),
             shapes_path(cache_dir, radius)]

    # Plain frames, both: with the geometry gone there is nothing geographic
    # left to carry, and pandas will not then look for a CRS that is not there.
    pd.DataFrame(edges[TABLE_COLUMNS]).to_parquet(files[0])
    pd.DataFrame(nodes[NODE_COLUMNS]).to_parquet(files[1])

    shapes = flatten_shapes(edges["geometry"].to_numpy())
    np.savez_compressed(files[2], xy=shapes.xy, start=shapes.start)
    return files


def load(cache_dir: Path, radius: float) -> tuple[pd.DataFrame, pd.DataFrame, Shapes]:
    """The three files, or a refusal naming the script that writes them.

    Deliberately not falling back to the graphml. A fallback that quietly costs
    887 MB is worse than a stop on a box with 512 MB: the process would not
    fail here, it would be killed several seconds later somewhere else, and the
    log would say nothing about graphs.
    """
    files = [edges_path(cache_dir, radius), nodes_path(cache_dir, radius),
             shapes_path(cache_dir, radius)]
    missing = [p for p in files if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"{', '.join(str(p) for p in missing)} is missing -- "
            f"run scripts/export_street_tables.py"
        )

    with np.load(files[2]) as stored:
        shapes = Shapes(xy=stored["xy"], start=stored["start"])
    return pd.read_parquet(files[0]), pd.read_parquet(files[1]), shapes
