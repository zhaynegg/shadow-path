"""The walking graph flattened for search, and an A* that knows the sun moves.

Nothing here knows what a shadow is. It walks a graph whose edge costs are a
table of (stamp, edge) numbers and a rule for which stamp a given minute falls
in -- routing.py builds both out of sun and preference, and this decides where
to put your feet.

It replaces nx.astar_path, which could not express the problem. A* labels each
node once, which is only valid when "the cheapest way to node X" is a single
fact. Under a moving sun it is not: reaching X after two kilometres and reaching
X after four are different times of day, and everything downstream of them is
priced differently. So the state here is (node, stamp), not node.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Network:
    """The graph as flat arrays, which is the shape a search actually wants.

    osmnx hands out a MultiDiGraph of dicts, and walking it costs more in
    attribute lookups than the search costs in arithmetic -- the same A*, over
    the same graph, runs in 2 ms here against networkx's 9 ms. Built once per
    process: it is a property of the graph alone, with no date and no sun in it.
    """

    # Per node, the edges leaving it as (destination, edge, metres). Tuples
    # rather than a frame because this is read once per relaxation and the
    # inner loop is the only thing here that is allowed to be ugly.
    out: tuple[tuple[tuple[int, int, float], ...], ...]
    xs: np.ndarray
    ys: np.ndarray
    length: np.ndarray
    # osmnx's node ids are arbitrary integers; the search wants offsets.
    at: dict[int, int]


def flatten(edges, nodes) -> Network:
    """A Network from the two frames osmnx already hands out."""
    at = {node: index for index, node in enumerate(nodes.index)}
    length = edges["length"].to_numpy(dtype="float64")

    leaving: list[list[tuple[int, int, float]]] = [[] for _ in at]
    for position, (u, v, _) in enumerate(edges.index):
        leaving[at[u]].append((at[v], position, length[position]))

    return Network(
        out=tuple(tuple(row) for row in leaving),
        xs=nodes["x"].to_numpy(dtype="float64"),
        ys=nodes["y"].to_numpy(dtype="float64"),
        length=length,
        at=at,
    )


def trace(came: dict, state: tuple[int, int]) -> list[tuple[int, int]]:
    """The path back out of the search, as (edge, the stamp it was walked in).

    The stamp is the one the walker *set off* along the edge in, which is the
    one its cost was taken from. An edge is tens of metres -- twenty seconds of
    walking -- so which end of it the stamp is read at makes no difference worth
    the arithmetic.
    """
    path = []
    while state in came:
        previous, edge = came[state]
        path.append((edge, previous[1]))
        state = previous
    path.reverse()
    return path


def walk(network: Network, weights: np.ndarray, stamp_of_minute: np.ndarray,
         start: int, goal: int, depart: float, minutes_per_m: float) -> list[tuple[int, int]]:
    """The cheapest walk from start to goal, priced as the sun finds it.

    `weights[stamp][edge]` is what that edge costs at that stamp, and
    `stamp_of_minute[m]` is which stamp the minute m of the day belongs to. The
    walker leaves at `depart` minutes past midnight and covers `minutes_per_m`
    minutes of clock per metre, so where they are in the table is a function of
    how far they have walked -- which is the property that keeps this cheap.
    Walking pace does not depend on shade, so there is no feedback loop of the
    kind traffic has, and no node is reachable at more than a stamp or two.

    The straight-line heuristic survives the move unchanged. Every weight is at
    least the edge's own length at every stamp -- see edge_weights -- so it can
    never overestimate, whichever stamp ends up pricing the edge.

    Settling on (node, stamp) does approximate: two paths that arrive in the
    same stamp are treated as the same state even if one walked further to get
    there, and the cheaper one wins. The error that hides is bounded by how much
    the shade moves between neighbouring stamps -- which is exactly what the
    stamps are spaced by, finest at dawn and dusk where an hour would be far too
    coarse. Where the approximation would hurt most, the buckets are narrowest.
    """
    out, xs, ys = network.out, network.xs, network.ys
    goal_x, goal_y = xs[goal], ys[goal]
    dusk = len(stamp_of_minute) - 1

    first = int(stamp_of_minute[min(int(depart), dusk)])
    best = {(start, first): 0.0}
    came: dict[tuple[int, int], tuple[tuple[int, int], int]] = {}
    heap = [(0.0, 0.0, 0.0, start, first)]
    settled = set()

    while heap:
        _, cost, walked, node, stamp = heapq.heappop(heap)

        # h is zero at the goal, so the first goal state to come off the heap is
        # the cheapest one there is -- whichever stamp it arrives in.
        if node == goal:
            return trace(came, (node, stamp))

        here = (node, stamp)
        if here in settled:
            continue
        settled.add(here)

        row = weights[stamp]
        for onward, edge, span in out[node]:
            step = cost + float(row[edge])
            far = walked + span

            # Where the clock is by the time this edge is behind them. Past the
            # end of the table it stays there: after dusk nothing casts a
            # shadow, and the last row of the table says so.
            clock = depart + far * minutes_per_m
            into = int(stamp_of_minute[int(clock) if clock < dusk else dusk])

            ahead = (onward, into)
            if step < best.get(ahead, float("inf")):
                best[ahead] = step
                came[ahead] = (here, edge)
                gap = ((xs[onward] - goal_x) ** 2 + (ys[onward] - goal_y) ** 2) ** 0.5
                heapq.heappush(heap, (step + gap, step, far, onward, into))

    raise ValueError("No walking route connects those two points.")
