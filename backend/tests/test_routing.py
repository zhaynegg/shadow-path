"""Planning a walk, and planning it against a sun that moves while you walk."""

import datetime as dt

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pytest
from pydantic import ValidationError
from shapely.geometry import Point

from backend.config import CRS, LAT, LON, today
from backend.core import search
from backend.core.day import across_the_day, at_one_stamp
from backend.core.routing import (
    MAX_ALPHA,
    MINUTES_PER_M,
    WALK_SPEED_MS,
    departures,
    edge_weights,
    endpoints,
    graph_nodes,
    lay_out,
    measure,
    minutes_past_midnight,
    nearest_node,
    plan,
    restamp,
    shortest,
    snap,
)
from backend.main import RouteRequest

# The three ways across the test graph, told apart by how shaded they are.
DIRECT, SUNLIT, SHADED = 0.5, 0.0, 1.0
DIRECT_M = 300.0
NOON = dt.time(12, 0)


def street_grid(coords, streets):
    """A projected walk graph from plain coordinates, laid out for the search.

    Returns the Streets, the edge frame (for ordering a shade array), and the
    named nodes as lat/lon, which is what everything public here takes.
    """
    here = gpd.GeoSeries([Point(LON, LAT)], crs=4326).to_crs(CRS).iloc[0]

    graph = nx.MultiDiGraph(crs=CRS)
    for name, (dx, dy) in coords.items():
        graph.add_node(name, x=here.x + dx, y=here.y + dy)

    for u, v in streets:
        span = Point(coords[u]).distance(Point(coords[v]))
        # Both directions: osmnx graphs are directed, and a walker may use a
        # street either way round.
        graph.add_edge(u, v, 0, length=span)
        graph.add_edge(v, u, 0, length=span)

    edges = ox.graph_to_gdfs(graph, nodes=False)
    city = lay_out(edges, ox.graph_to_gdfs(graph, edges=False))

    def where(name):
        point = gpd.GeoSeries(
            [Point(here.x + coords[name][0], here.y + coords[name][1])], crs=CRS
        ).to_crs(4326).iloc[0]
        return (point.y, point.x)

    return city, edges, where


def three_ways():
    """A short mixed street with two longer flanks either side.

        N (150, 100)      fully shaded flank
        A (0, 0) ---------------------------- C (300, 0)   half shaded, direct
        S (150, -100)     fully sunlit flank

    Each flank is about 361 m against the direct route's 300, so no preference
    reaches one by accident: a walker only goes that way if the detour bought
    something. Returns the streets, a maker for one stamp's shade, and the two
    ends as lat/lon.
    """
    coords = {"A": (0, 0), "C": (300, 0), "N": (150, 100), "S": (150, -100)}
    city, edges, where = street_grid(
        coords, [("A", "C"), ("A", "N"), ("N", "C"), ("A", "S"), ("S", "C")])

    def field(direct, north, south):
        """Shade per edge, in the order the network numbered them."""
        by_street = {("A", "C"): direct, ("A", "N"): north, ("N", "C"): north,
                     ("A", "S"): south, ("S", "C"): south}
        both = {}
        for (u, v), fraction in by_street.items():
            both[(u, v, 0)] = both[(v, u, 0)] = fraction
        return np.array([both[key] for key in edges.index], dtype="float32")

    return city, field, [where("A"), where("C")]


def one_sun(field_values, at=NOON):
    """One stamp standing in for the day -- what the router did before it could
    follow a clock, and still what a checkout with no scores gets."""
    return at_one_stamp(field_values, at)


def walked_at(city, day, origin, destination, alpha, at=NOON):
    return plan(city, day, origin, destination, alpha, minutes_past_midnight(at))


def test_zero_alpha_takes_the_short_way():
    """The baseline every other route is sold against: plain shortest path."""
    city, field, (origin, destination) = three_ways()
    day = one_sun(field(DIRECT, SHADED, SUNLIT))

    result = walked_at(city, day, origin, destination, 0.0)["baseline"]

    assert result["distance_m"] == pytest.approx(DIRECT_M)
    assert result["shade_fraction"] == pytest.approx(DIRECT)


def test_positive_alpha_detours_into_shade():
    city, field, (origin, destination) = three_ways()
    day = one_sun(field(DIRECT, SHADED, SUNLIT))

    result = walked_at(city, day, origin, destination, 3.0)["route"]

    assert result["shade_fraction"] == pytest.approx(SHADED)
    assert result["distance_m"] > DIRECT_M


def test_negative_alpha_detours_into_sun():
    """Sun-seeking: the winter product, and the whole point of a signed alpha.

    The sunlit flank is the *longer* way, so picking it is not the search
    falling back on distance -- it is the walker paying for sun, exactly as a
    positive alpha pays for shade.
    """
    city, field, (origin, destination) = three_ways()
    day = one_sun(field(DIRECT, SHADED, SUNLIT))

    result = walked_at(city, day, origin, destination, -3.0)["route"]

    assert result["shade_fraction"] == pytest.approx(SUNLIT)
    assert result["distance_m"] > DIRECT_M


def test_opposite_alphas_are_mirror_images():
    """-a costs shade what +a costs sun, and neither ever costs less than zero.

    The second half is the invariant the search actually depends on, twice
    over: a negative weight comes back as a path that is simply wrong, and a
    weight below the edge's own length would break the straight-line heuristic
    that survives the move to a moving sun.
    """
    city, field, _ = three_ways()
    shade = one_sun(field(DIRECT, SHADED, SUNLIT)).shade
    length = city.network.length

    seeking_shade = edge_weights(shade, length, 4.0)
    seeking_sun = edge_weights(shade, length, -4.0)

    assert (seeking_sun >= 0).all()
    assert (seeking_sun >= length).all()
    assert (seeking_shade >= length).all()

    sunlit = shade[0] == SUNLIT
    assert seeking_sun[0][sunlit] == pytest.approx(length[sunlit], rel=1e-6)
    assert seeking_shade[0][sunlit] == pytest.approx(length[sunlit] * 5, rel=1e-6)


def test_a_leg_carries_how_long_it_takes_to_walk_it():
    """Derived from the distance, in the one place that measures a distance.

    The browser could divide by a constant just as well, and then there would
    be two constants -- one of them in a file nobody edits when the other one
    moves.
    """
    city, field, (origin, destination) = three_ways()
    day = one_sun(field(DIRECT, SHADED, SUNLIT))

    result = walked_at(city, day, origin, destination, 0.0)["baseline"]

    assert result["duration_s"] == pytest.approx(DIRECT_M / WALK_SPEED_MS)
    assert result["duration_s"] == pytest.approx(222.2, abs=0.5)


def test_a_detour_for_shade_costs_minutes_as_well_as_metres():
    """What the number is for. "10% longer" is a ratio a reader has to convert
    before it means anything; "four minutes" is the thing they are deciding
    about.
    """
    city, field, (origin, destination) = three_ways()
    day = one_sun(field(DIRECT, SHADED, SUNLIT))

    both = walked_at(city, day, origin, destination, 3.0)

    assert both["route"]["duration_s"] > both["baseline"]["duration_s"]
    assert (both["route"]["duration_s"] / both["baseline"]["duration_s"]
            == pytest.approx(both["route"]["distance_m"] / both["baseline"]["distance_m"]))


def test_two_clicks_on_one_corner_are_refused():
    city, field, _ = three_ways()
    day = one_sun(field(DIRECT, SHADED, SUNLIT))
    here = (LAT, LON)

    with pytest.raises(ValueError, match="same street corner"):
        walked_at(city, day, here, here, 3.0)


def test_snap_refuses_a_point_off_the_network():
    """The bound that stops a click across town being answered with the nearest
    corner of Astana. One degree of latitude is 111 km; MAX_SNAP_M is 300.
    """
    city, _, _ = three_ways()

    with pytest.raises(ValueError, match="from the nearest mapped"):
        snap(city.nodes, LAT + 1.0, LON)


def test_snap_is_the_same_answer_as_nearest_node():
    """nearest_node builds the node frame and hands it to snap; a caller that
    builds it once and snaps many times -- which is every scan -- has to land on
    the same corner as one that does not.
    """
    coords = {"A": (0, 0), "C": (300, 0)}
    here = gpd.GeoSeries([Point(LON, LAT)], crs=4326).to_crs(CRS).iloc[0]
    graph = nx.MultiDiGraph(crs=CRS)
    for name, (dx, dy) in coords.items():
        graph.add_node(name, x=here.x + dx, y=here.y + dy)
    graph.add_edge("A", "C", 0, length=300.0)

    assert snap(graph_nodes(graph), LAT, LON) == nearest_node(graph, LAT, LON)


# --- the sun moves while you are walking under it --------------------------

# Two ways round, the same length, and long enough that the second half of
# either is walked in the next stamp: 2.7 km a leg is 33 minutes at 1.35 m/s,
# so a walk leaving at 12:00 is past the 12:30 boundary before it turns the
# corner. Lengths come out of the coordinates, so the straight-line heuristic
# stays admissible.
LONG_WAY = {"A": (0, 0), "C": (5400, 0), "N": (2700, 400), "S": (2700, -400)}
LEG_M = Point(0, 0).distance(Point(2700, 400))


def two_ways_round():
    """A corridor with a north and a south way, equal in length.

    Returns the streets, a maker for one stamp's shade over (north, south), and
    the ends.
    """
    city, edges, where = street_grid(
        LONG_WAY, [("A", "N"), ("N", "C"), ("A", "S"), ("S", "C")])

    def field(north, south):
        by_street = {("A", "N"): north, ("N", "C"): north,
                     ("A", "S"): south, ("S", "C"): south}
        both = {}
        for (u, v), fraction in by_street.items():
            both[(u, v, 0)] = both[(v, u, 0)] = fraction
        return np.array([both[key] for key in edges.index], dtype="float32")

    return city, field, [where("A"), where("C")]


def a_moving_sun():
    """Noon and one o'clock, with the shade swapping sides between them.

    At noon the north way is fully shaded and the south way half. By one it has
    turned over: the north way is bare and the south way is complete shade.

    A walk leaving at noon spends its first leg under noon's sun and its second
    under one o'clock's, so the north way -- the one that looks best from a
    standing start -- is the worse walk. That is the whole feature in a fixture.
    """
    city, field, ends = two_ways_round()
    day = across_the_day([field(north=1.0, south=0.5), field(north=0.0, south=1.0)],
                         [dt.time(12, 0), dt.time(13, 0)])
    return city, day, ends


def test_a_walk_is_priced_at_the_sun_it_is_under_leg_by_leg():
    """The measurement the whole change exists for. Half this walk happens
    after the stamp turns over, and the number says so.
    """
    city, day, (origin, destination) = a_moving_sun()

    south = walked_at(city, day, origin, destination, 6.0, dt.time(12, 0))["route"]

    # First leg under noon (0.5), second under one o'clock (1.0).
    assert south["shade_fraction"] == pytest.approx(0.75, abs=0.01)


def test_the_search_picks_the_walk_rather_than_the_first_impression():
    """A router frozen at noon sees the north way fully shaded and takes it,
    then walks its second half through bare sun for a true 0.50. Knowing where
    the walker will be by then, the south way is the better walk at 0.75.
    """
    city, day, (origin, destination) = a_moving_sun()
    noon = minutes_past_midnight(dt.time(12, 0))

    moving = plan(city, day, origin, destination, 6.0, noon)["route"]

    frozen_day = at_one_stamp(day.shade[0], dt.time(12, 0))
    frozen = plan(city, frozen_day, origin, destination, 6.0, noon)["route"]

    # The frozen router believes its walk is perfect, and it is not.
    assert frozen["shade_fraction"] == pytest.approx(1.0, abs=0.01)
    assert measure(city, day, restamp(
        city, day, [edge for edge, _ in _edges_of(city, frozen_day, origin, destination, 6.0)],
        noon))["shade_fraction"] == pytest.approx(0.5, abs=0.01)

    assert moving["shade_fraction"] == pytest.approx(0.75, abs=0.01)
    assert not moving["geometry"].equals(frozen["geometry"])


def _edges_of(city, day, origin, destination, alpha, at=NOON):
    """The edge list behind a plan, which `plan` itself does not hand back."""
    start, goal = endpoints(city, origin, destination)
    weights = edge_weights(day.shade, city.network.length, alpha)
    return search.walk(city.network, weights, day.stamp_of_minute,
                       start, goal, minutes_past_midnight(at), MINUTES_PER_M)


def test_restamp_agrees_with_the_search_about_what_time_it_is():
    """The invariant that makes a fixed path comparable with a searched one.

    `departures` prices one direct route at two dozen departures without
    searching for it again; if its idea of which stamp an edge falls in ever
    drifted from the search's, the two curves on the chart would be measured
    against different clocks and the comparison would be meaningless.
    """
    city, day, (origin, destination) = a_moving_sun()
    noon = minutes_past_midnight(dt.time(12, 0))

    dated = _edges_of(city, day, origin, destination, 6.0)

    assert restamp(city, day, [edge for edge, _ in dated], noon) == dated


def test_a_walk_that_outlasts_the_daylight_finishes_in_the_dark():
    """Nothing casts a shadow once the sun is down, so those last streets are
    neither shaded nor sunlit -- they are unlit, which this codebase already
    spells as fully shaded. Carrying the last stamp's shadows forward instead
    would have the walker dodging buildings that stopped casting an hour ago.
    """
    city, day, (origin, destination) = a_moving_sun()

    # Leaving at 13:00 puts the second leg past 13:30, and the table's last
    # stamp is 13:00 -- so the back half of this walk is night.
    late = plan(city, day, origin, destination, 6.0, minutes_past_midnight(dt.time(13, 30)))

    assert late["route"]["shade_fraction"] == pytest.approx(1.0, abs=0.01)


def test_the_shortest_path_is_one_path_whatever_the_sun_does():
    """At alpha 0 the weight is the edge's own length, and a length does not
    depend on where the sun is. It is the only walk in the app a moving sun
    cannot change the shape of, which is why `departures` searches it once.
    """
    city, day, (origin, destination) = a_moving_sun()
    start, goal = endpoints(city, origin, destination)

    once = shortest(city, start, goal)

    for at in (dt.time(12, 0), dt.time(13, 0), dt.time(23, 0)):
        again = plan(city, day, origin, destination, 0.0, minutes_past_midnight(at))
        assert again["baseline"]["distance_m"] == pytest.approx(
            float(sum(city.network.length[edge] for edge in once)))


# --- the whole day at once --------------------------------------------------

def test_departures_answers_for_every_stamp_in_order():
    city, day, (origin, destination) = a_moving_sun()

    scan = departures(city, day, origin, destination, 6.0)

    assert [row["time"] for row in scan["departures"]] == ["12:00", "13:00"]


def test_departures_prices_each_row_as_that_departure_is_walked():
    """Every row is planned against the sun as it moves through *that* walk,
    so the hour a row recommends is one whose route was chosen knowing where
    the walker would be by the end of it.
    """
    city, day, (origin, destination) = a_moving_sun()

    rows = departures(city, day, origin, destination, 6.0)["departures"]
    shade = {row["time"]: row["shade_fraction"] for row in rows}

    # Noon: half at 0.5, half at 1.0. One o'clock: the second leg runs past the
    # last stamp, so it finishes in the dark -- 1.0 either way.
    assert shade["12:00"] == pytest.approx(0.75, abs=0.01)
    assert shade["13:00"] == pytest.approx(1.0, abs=0.01)


def test_departures_states_the_direct_route_once_and_its_shade_per_row():
    """The direct route is one path, so its length and duration are said once.
    Its shade is not constant -- that is the second line on the chart.
    """
    city, day, (origin, destination) = a_moving_sun()

    scan = departures(city, day, origin, destination, 6.0)

    assert scan["baseline_distance_m"] == pytest.approx(2 * LEG_M, rel=1e-3)
    assert scan["baseline_duration_s"] == pytest.approx(2 * LEG_M / WALK_SPEED_MS, rel=1e-3)
    for row in scan["departures"]:
        assert row["duration_s"] == pytest.approx(row["distance_m"] / WALK_SPEED_MS)


def test_departures_rejects_two_clicks_on_one_corner():
    """Raised once, before any stamp is planned. The cost of a scan is the
    reason to check first: two dozen searches is a poor way to find out that
    there was never a walk to plan.
    """
    city, day, _ = a_moving_sun()
    here = (LAT, LON)

    with pytest.raises(ValueError, match="same street corner"):
        departures(city, day, here, here, 6.0)


def test_departures_follows_a_negative_alpha_into_the_sun():
    """Sun-seeking is the winter product, and the scan has to answer for it
    too: a chart that always recommended the shadiest hour would send somebody
    who came looking for a warm walk out at dusk.
    """
    city, field, ends = two_ways_round()
    day = across_the_day([field(north=1.0, south=0.0), field(north=1.0, south=0.0)],
                         [dt.time(12, 0), dt.time(13, 0)])

    rows = departures(city, day, *ends, -6.0)["departures"]

    assert rows[0]["shade_fraction"] == pytest.approx(SUNLIT, abs=0.01)


# --- the request model ------------------------------------------------------

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
    is a fresh day of scores, so an open range is unbounded work to ask for.
    """
    here = {"origin": (0, 0), "destination": (1, 1)}
    assert RouteRequest(**here, date=today()).date == today()

    with pytest.raises(ValidationError):
        RouteRequest(**here)

    for far in (today() + dt.timedelta(days=400), today() - dt.timedelta(days=400)):
        with pytest.raises(ValidationError):
            RouteRequest(**here, date=far)
