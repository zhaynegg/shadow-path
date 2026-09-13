import datetime as dt
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Regenerable and large: .gitignore carves this out entirely.
CACHE_DIR = DATA_DIR / "cache"

# Hand-entered storey counts, the opposite of regenerable -- no script can
# rebuild somebody counting floors off imagery. Lives outside the cache so it
# is tracked, and so nothing that clears the cache can take it with it.
HEIGHT_OVERRIDES = DATA_DIR / "height_overrides.csv"

# The default disc for scripts/export_fixtures.py, and nothing else -- the API
# derives its own radii from GRAPH_RADIUS at the bottom of this file.
RADIUS = 2000
TZ = dt.timezone(dt.timedelta(hours=5))


def today() -> dt.date:
    """Today in Astana, not on whatever machine is running this.

    The city's day starts five hours before UTC's, so from 19:00 UTC it has
    already rolled over. Anything reading its own clock instead -- a CI runner,
    a server in another region -- spends every evening a day behind.
    """
    return dt.datetime.now(TZ).date()


# City centre. The load radius is measured from here, and it is the same point
# the sun position is computed for.
LAT, LON = 51.1605, 71.4704

# All our geometry is in metres, in this projection. Buildings already use it.
CRS = "EPSG:32642"

# How far from the centre a walk can be planned. This constant alone decides
# the routable area: everything else the router reads already covers the whole
# city -- the footprint cache is 34 x 52 km, and the shadow tiles the map draws
# reach about 17 km out. main.py widens it by MAX_SHADOW_M of its own accord to
# pick up the buildings just outside that can still shade a street inside.
#
# It is a straight trade against latency. Scoring costs roughly 0.25 ms per
# shadow caster and the caster count grows with the disc, so on this machine:
#
#     1700 m     9 km2    4% of the city's buildings
#     5000 m    79 km2   35%
#    12000 m   452 km2   88%
#    15000 m   707 km2   95%   <- here
#
# Scoring a disc this size takes about half a minute, which is why it is not
# done here: scripts/export_shadow_tiles.py computes the same field nightly to
# cut the tiles and now writes the scored graph beside them, so main.py reads
# the answer instead of recomputing it. See backend/core/scores.py. Without
# that file the API still works, by falling back to computing the field itself
# -- correctly, and slowly enough that you will notice.
#
# 15 km costs almost nothing over 12: the outer ring is steppe, so the graph
# grows by 5% (145k edges to 153k) while covering 7% more of the city.
GRAPH_RADIUS = 15000