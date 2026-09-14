import datetime as dt

import geopandas as gpd
import networkx as nx
import osmnx as ox
import pytest
from pydantic import ValidationError
from shapely.geometry import Point

from backend.config import CRS, LAT, LON, today
from backend.core.routing import (
    MAX_ALPHA,
    WALK_SPEED_MS,
    departures,
    edge_weights,
    graph_nodes,
    nearest_node,
    route,
    snap,
)
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


# One day over the three ways, as (direct, north flank, south flank) shade.
# Morning puts the shade on the long way round, which is the only time
# detouring is worth anything; noon has none to find on any of them; dusk
# shades the whole city at once, which is the real reason a shade curve peaks
# at the end of the day and why the scan reports the direct route beside it.
A_DAY = {
    dt.time(8, 0): (0.10, 0.80, 0.00),
    dt.time(12, 0): (0.05, 0.05, 0.00),
    dt.time(18, 0): (1.00, 1.00, 1.00),
}


def a_day():
    """The three ways, scored once per stamp -- what a day scan is handed.

    Only the fractions differ between stamps. The geometry, the lengths and the
    index belong to the graph, not to the hour, which is exactly the property
    `departures` leans on when it searches the direct route once.
    """
    graph, scored, ends = three_ways()

    def field(direct, north, south):
        shade = {}
        for (u, v), fraction in {
            ("A", "C"): direct,
            ("A", "N"): north, ("N", "C"): north,
            ("A", "S"): south, ("S", "C"): south,
        }.items():
            shade[(u, v, 0)] = shade[(v, u, 0)] = fraction

        stamp = scored.copy()
        stamp["shade_fraction"] = [shade[key] for key in stamp.index]
        return stamp

    return graph, ends, {at: field(*values) for at, values in A_DAY.items()}


def test_departures_answers_for_every_stamp_in_order():
    graph, (origin, destination), day = a_day()

    scan = departures(graph, day, origin, destination, 3.0)

    assert [row["time"] for row in scan["departures"]] == ["08:00", "12:00", "18:00"]


def test_departures_finds_the_hour_worth_leaving_at():
    """The whole feature in one assertion: the same walk is a different walk
    depending on when you start it, and the scan is what makes that visible.
    """
    graph, (origin, destination), day = a_day()

    scan = departures(graph, day, origin, destination, 3.0)
    shade = {row["time"]: row["shade_fraction"] for row in scan["departures"]}

    # Morning detours onto the shaded flank; noon has nowhere better to go.
    assert shade["08:00"] == pytest.approx(0.80)
    assert shade["12:00"] == pytest.approx(0.05)
    assert max(shade, key=shade.get) == "18:00"


def test_departures_reports_one_direct_route_priced_at_every_stamp():
    """At alpha 0 the weight is the edge's own length, and a length does not
    depend on where the sun is -- so the direct path is one path all day. What
    changes is how much of it happens to be shaded, which is the second line on
    the chart and the reason the first one is worth reading: a shade curve
    peaking at dusk is about the sun, not about the route.
    """
    graph, (origin, destination), day = a_day()

    scan = departures(graph, day, origin, destination, 3.0)
    rows = scan["departures"]

    assert scan["baseline_distance_m"] == pytest.approx(DIRECT_M)
    assert [row["baseline_shade_fraction"] for row in rows] == pytest.approx([0.10, 0.05, 1.00])
    # Detouring buys 70 points in the morning and nothing at all by dusk.
    gains = [row["shade_fraction"] - row["baseline_shade_fraction"] for row in rows]
    assert gains == pytest.approx([0.70, 0.0, 0.0])


def test_departures_detours_only_when_the_hour_pays_for_it():
    """Noon and dusk are both walked the short way, for opposite reasons: at
    noon there is no shade to reach, at dusk there is no shade to reach *for*.
    """
    graph, (origin, destination), day = a_day()

    rows = departures(graph, day, origin, destination, 3.0)["departures"]
    distance = {row["time"]: row["distance_m"] for row in rows}

    assert distance["08:00"] > DIRECT_M
    assert distance["12:00"] == pytest.approx(DIRECT_M)
    assert distance["18:00"] == pytest.approx(DIRECT_M)


def test_departures_follows_a_negative_alpha_into_the_sun():
    """Sun-seeking is the winter product, and the scan has to answer for it
    too: a chart that always recommended the shadiest hour would send somebody
    who came looking for a warm walk out at dusk.
    """
    graph, (origin, destination), day = a_day()

    rows = departures(graph, day, origin, destination, -3.0)["departures"]
    shade = {row["time"]: row["shade_fraction"] for row in rows}

    assert shade["08:00"] == pytest.approx(SUNLIT)   # detours onto the sunlit flank
    assert min(shade, key=shade.get) == "08:00"


def test_departures_rejects_two_clicks_on_one_corner():
    """Raised once, before any stamp is planned. The cost of a scan is the
    reason to check first: twenty-four searches is a poor way to find out that
    there was never a walk to plan.
    """
    graph, _, day = a_day()
    here = (LAT, LON)

    with pytest.raises(ValueError, match="same street corner"):
        departures(graph, day, here, here, 3.0)


def test_snap_refuses_a_point_off_the_network():
    """The bound that stops a click across town being answered with the nearest
    corner of Astana. One degree of latitude is 111 km; MAX_SNAP_M is 300.
    """
    graph, _, _ = three_ways()

    with pytest.raises(ValueError, match="from the nearest mapped"):
        snap(graph_nodes(graph), LAT + 1.0, LON)


def test_snap_is_the_same_answer_as_nearest_node():
    """The refactor's invariant. nearest_node now builds the node frame and
    hands it to snap; a caller that builds it once and snaps many times -- which
    is every scan -- has to land on the same corner as one that does not.
    """
    graph, _, (origin, _) = three_ways()

    assert snap(graph_nodes(graph), *origin) == nearest_node(graph, *origin)


def test_a_leg_carries_how_long_it_takes_to_walk_it():
    """Derived from the distance, in the one place that measures a distance.

    The browser could divide by a constant just as well, and then there would
    be two constants -- one of them in a file nobody edits when the other one
    moves. Every leg the API returns comes through `measure`, so both endpoints
    quote the same pace or neither does.
    """
    graph, scored, (origin, destination) = three_ways()

    result = route(graph, scored, origin, destination, 0.0)

    assert result["duration_s"] == pytest.approx(DIRECT_M / WALK_SPEED_MS)
    # 300 m at a walk is about three and a half minutes.
    assert result["duration_s"] == pytest.approx(222.2, abs=0.5)


def test_a_detour_for_shade_costs_minutes_as_well_as_metres():
    """What the number is for. "10% longer" is a ratio a reader has to convert
    before it means anything; "four minutes" is the thing they are deciding
    about.
    """
    graph, scored, (origin, destination) = three_ways()

    shady = route(graph, scored, origin, destination, 3.0)
    direct = route(graph, scored, origin, destination, 0.0)

    assert shady["duration_s"] > direct["duration_s"]
    # The same ratio as the distances, because the pace is one number: the
    # flanks are about 361 m against the direct route's 300.
    assert shady["duration_s"] / direct["duration_s"] == pytest.approx(
        shady["distance_m"] / direct["distance_m"])


def test_the_day_scan_times_every_departure_and_the_direct_route_once():
    """The walk is not the same length at every hour -- the detour the shade is
    worth changes with the sun -- so the minutes move with it. The direct route
    is one path all day, so it is timed once beside the rows rather than in
    each of them.
    """
    graph, (origin, destination), day = a_day()

    scan = departures(graph, day, origin, destination, 3.0)
    rows = scan["departures"]

    assert scan["baseline_duration_s"] == pytest.approx(DIRECT_M / WALK_SPEED_MS)
    for row in rows:
        assert row["duration_s"] == pytest.approx(row["distance_m"] / WALK_SPEED_MS)
    # 08:00 detours onto the flank; noon and dusk take the short way.
    assert rows[0]["duration_s"] > rows[1]["duration_s"]
