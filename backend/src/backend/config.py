import datetime as dt
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
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