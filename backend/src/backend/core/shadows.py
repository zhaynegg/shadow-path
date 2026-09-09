import datetime as dt

import geopandas as gpd
import numpy as np
import shapely
from shapely.affinity import translate
from shapely.geometry import Polygon

# Low sun makes h/tan(altitude) run away; past this the polygon is meaningless
# and the union gets expensive. See the latitude table in the README.
MAX_SHADOW_M = 300.0

# Geometry tolerance for the exported file. 1 m is well below one screen pixel
# at any zoom the shadow layer is drawn at, and cuts the file size sharply.
SIMPLIFY_M = 1.0


def cast_shadow(geom, height_m: float, altitude: float, azimuth: float):
    """Project one footprint's shadow as a single polygon.

    The shadow is the ground the footprint sweeps as it slides away from the
    sun: the building, its translated copy, and the band each edge drags
    between the two. Sweeping rather than hulling is what keeps courtyards
    and notches open -- a convex hull fills them in at every sun angle.

    Returns None when the sun is at or below the horizon: the shadow is
    unbounded there, and everything is in shade anyway.
    """
    if altitude <= 0:
        return None

    length = min(height_m / np.tan(np.radians(altitude)), MAX_SHADOW_M)

    # Compass bearing of the shadow: directly away from the sun. Bearings run
    # clockwise from north, so north is the cosine axis and east the sine one.
    bearing = np.radians(azimuth + 180.0)
    dx = length * np.sin(bearing)
    dy = length * np.cos(bearing)

    parts = [geom, translate(geom, xoff=dx, yoff=dy)]
    polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    for poly in polys:
        for ring in (poly.exterior, *poly.interiors):
            coords = list(ring.coords)
            parts.extend(
                Polygon([a, b, (b[0] + dx, b[1] + dy), (a[0] + dx, a[1] + dy)])
                for a, b in zip(coords, coords[1:])
            )
    return shapely.union_all(parts)


def shadow_field(gdf: gpd.GeoDataFrame, altitude: float, azimuth: float):
    """Every building's shadow, merged into one geometry.

    Overlaps are dissolved: two buildings shading the same street is one
    shaded area, not two polygons stacked on top of each other.
    """
    shadows = gdf.apply(
        lambda row: cast_shadow(row.geometry, row["height_m"], altitude, azimuth),
        axis=1,
    ).dropna()
    if shadows.empty:
        return None
    return gpd.GeoSeries(shadows, crs=gdf.crs).union_all()


def shadow_frame(geom, crs, when: dt.datetime, altitude, azimuth) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "time": [when.isoformat()],
            "altitude_deg": [round(altitude, 2)],
            "azimuth_deg": [round(azimuth, 2)],
        },
        geometry=[geom.simplify(SIMPLIFY_M)],
        crs=crs,
    ).to_crs(4326)
