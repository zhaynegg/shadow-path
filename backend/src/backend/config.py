import datetime as dt
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# Regenerable and large: .gitignore carves this out entirely.
CACHE_DIR = DATA_DIR / "cache"

# Hand-entered storey counts, the opposite of regenerable -- no script can
# rebuild somebody counting floors off imagery. Lives outside the cache so it
# is tracked, and so nothing that clears the cache can take it with it.
HEIGHT_OVERRIDES = DATA_DIR / "height_overrides.csv"
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

# Buildings load out to 2000 m. The graph stops 300 m short of that, so every
# street in the graph still has all the buildings that could shade it.
GRAPH_RADIUS = RADIUS - 300