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
DATE = dt.date(2026, 6, 21)

# City centre. The load radius is measured from here, and it is the same point
# the sun position is computed for.
LAT, LON = 51.1605, 71.4704

# All our geometry is in metres, in this projection. Buildings already use it.
CRS = "EPSG:32642"

# Buildings load out to 2000 m. The graph stops 300 m short of that, so every
# street in the graph still has all the buildings that could shade it.
GRAPH_RADIUS = RADIUS - 300