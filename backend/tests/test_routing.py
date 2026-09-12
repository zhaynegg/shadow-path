import datetime as dt

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pytest
from pydantic import ValidationError
from shapely.geometry import Point

from backend.config import CRS, LAT, LON, today
from backend.core.routing import MAX_ALPHA, edge_weights, route
from backend.main import RouteRequest

# The three ways across the test graph, told apart by how shaded they are.
DIRECT, SUNLIT, SHADED = 0.5, 0.0, 1.0
DIRECT_M = 300.0


def three_ways():
    """A graph with a short mixed street and two longer flanks either side.

    Nodes, in metres east and north of A:

        N (150, 100)      fully shaded flank
        A (0, 0) ---------------------------- C (300, 0)   half shaded, direct
        S (150, -100)     fully sunlit flank

    Each flank is about 361 m against the direct route's 300, so no preference
    reaches one by accident: a walker only goes that way if the detour bought
    something. Returns the graph, its scored edges, and the two endpoints as
    lat/lon, which is what `route` takes.
    """
    here = gpd.GeoSeries([Point(LON, LAT)], crs=4326).to_crs(CRS).iloc[0]
    coords = {"A": (0, 0), "C": (300, 0), "N": (150, 100), "S": (150, -100)}

    graph = nx.MultiDiGraph(crs=CRS)
    for name, (dx, dy) in coords.items():
        graph.add_node(name, x=here.x + dx, y=here.y + dy)

    shade = {}
    for u, v, shade_fraction in [
        ("A", "C", DIRECT),
        ("A", "N", SHADED), ("N", "C", SHADED),
        ("A", "S", SUNLIT), ("S", "C", SUNLIT),
    ]:
        span = Point(coords[u]).distance(Point(coords[v]))
        # Both directions: osmnx graphs are directed, and a walker may use a
        # street either way round.
        graph.add_edge(u, v, 0, length=span)
        graph.add_edge(v, u, 0, length=span)
        shade[(u, v, 0)] = shade[(v, u, 0)] = shade_fraction

    scored = ox.graph_to_gdfs(graph, nodes=False)
    scored["shade_fraction"] = [shade[key] for key in scored.index]

    ends = gpd.GeoSeries(
        [Point(here.x, here.y), Point(here.x + 300, here.y)], crs=CRS
    ).to_crs(4326)
    return graph, scored, [(point.y, point.x) for point in ends]


def test_zero_alpha_takes_the_short_way():
    """The baseline every other route is sold against: plain shortest path."""
    graph, scored, (origin, destination) = three_ways()

    result = route(graph, scored, origin, destination, 0.0)

    assert result["distance_m"] == pytest.approx(DIRECT_M)
    assert result["shade_fraction"] == pytest.approx(DIRECT)


def test_positive_alpha_detours_into_shade():
    graph, scored, (origin, destination) = three_ways()

    result = route(graph, scored, origin, destination, 3.0)

    assert result["shade_fraction"] == pytest.approx(SHADED)
    assert result["distance_m"] > DIRECT_M


def test_negative_alpha_detours_into_sun():
    """Sun-seeking: the winter product, and the whole point of a signed alpha.

    The sunlit flank is the *longer* way, so picking it is not the search
    falling back on distance -- it is the walker paying for sun, exactly as a
    positive alpha pays for shade.
    """
    graph, scored, (origin, destination) = three_ways()

    result = route(graph, scored, origin, destination, -3.0)

    assert result["shade_fraction"] == pytest.approx(SUNLIT)
    assert result["distance_m"] > DIRECT_M


def test_opposite_alphas_are_mirror_images():
    """-a costs shade what +a costs sun, and neither ever costs less than zero.

    The second half is the invariant A* actually depends on. A negative weight
    does not raise in networkx; it comes back as a path that is simply wrong.
    """
    _, scored, _ = three_ways()

    seeking_shade = edge_weights(scored, 4.0)
    seeking_sun = edge_weights(scored, -4.0)

    assert (seeking_sun >= 0).all()
    # A fully sunlit edge is free to a sun-seeker and dearest to a shade-seeker.
    sunlit = scored["shade_fraction"] == SUNLIT
    assert seeking_sun[sunlit].tolist() == pytest.approx(scored["length"][sunlit].tolist())
    assert seeking_shade[sunlit].tolist() == pytest.approx((scored["length"][sunlit] * 5).tolist())


def test_request_model_takes_signed_alpha_within_bounds():
    here = {"origin": (0, 0), "destination": (1, 1), "date": today()}
    assert RouteRequest(**here, alpha=-3.0).alpha == -3.0

    for rejected in (-MAX_ALPHA - 1, MAX_ALPHA + 1, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            RouteRequest(**here, alpha=rejected)


def test_request_model_requires_a_date_near_today():
    """The date is what keeps the router and the map on the same sun.

    Required rather than defaulted: a default is the server guessing, which is
    the thing sending it was meant to stop. Bounded because every distinct date
    is a fresh shadow field, so an open range is unbounded work to ask for.
    """
    here = {"origin": (0, 0), "destination": (1, 1)}
    assert RouteRequest(**here, date=today()).date == today()

    with pytest.raises(ValidationError):
        RouteRequest(**here)

    for far in (today() + dt.timedelta(days=400), today() - dt.timedelta(days=400)):
        with pytest.raises(ValidationError):
            RouteRequest(**here, date=far)
