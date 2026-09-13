"""Cache the named places somebody might walk to, the way the trees are cached.

    uv run python scripts/fetch_places.py

The search index is built from three sources and this is the one that has to be
fetched. The other two are already on disk: street names come off the routing
graph, and named buildings out of astana_buildings.parquet.

This is the source that turned out to matter most. Measured inside the routing
disc, 4,112 named features here are *not* named buildings -- 327 bus platforms,
308 convenience shops, 283 cafes, 152 schools, 148 restaurants, 130 banks, 88
hotels, 36 parks. They are what a person walks to. An index of streets and
building names alone finds the street and not the destination.

Geometry is reduced to one point per feature on the way out. Nothing downstream
wants the shape of a cafe -- the router snaps a point to the nearest node, and
300 m is as close as it needs to be (MAX_SNAP_M in core/routing.py).
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path

import geopandas as gpd
import osmnx as ox

from backend.config import CACHE_DIR, CRS, GRAPH_RADIUS, LAT, LON

warnings.filterwarnings("ignore")

PLACES = "astana_places.parquet"

# Everything that answers "where are you walking to?". Deliberately wide: the
# cost of a row here is a few dozen bytes in a file already measured in tens of
# kilobytes, and the cost of a missing row is a search that finds nothing.
#
# `building` is not among them -- those are already in the footprints cache with
# their heights, and taking them twice would put every mall in the list twice.
TAGS = {
    "amenity": True,
    "leisure": True,
    "tourism": True,
    "shop": True,
    "office": True,
    "healthcare": True,
    "public_transport": True,
    "railway": ["station", "halt", "tram_stop"],
    "aeroway": ["terminal", "aerodrome"],
    "place": True,
    "natural": ["water", "beach"],
    "historic": True,
}

# The columns worth keeping: a name in any of the three languages the city signs
# itself in, and enough tagging to say what kind of thing a result is.
KEEP = ["element", "id", "name", "name:kk", "name:ru", "name:en", "alt_name",
        "amenity", "leisure", "tourism", "shop", "office", "healthcare",
        "public_transport", "railway", "aeroway", "place", "historic"]


def fetch(cache_dir: Path, radius: float) -> gpd.GeoDataFrame:
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir / "osmnx")

    gdf = ox.features_from_point((LAT, LON), tags=TAGS, dist=radius)

    # A row with no name cannot be searched for, and three quarters of what
    # Overpass returns for these tags has none.
    gdf = gdf[gdf["name"].notna()].reset_index()

    keep = [c for c in KEEP if c in gdf.columns]
    out = gdf[[*keep, "geometry"]].to_crs(CRS)

    # One point per feature. representative_point stays inside a concave shape,
    # which a centroid does not -- the centroid of a C-shaped park is outside it.
    out["geometry"] = out.geometry.representative_point()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--radius", type=float, default=GRAPH_RADIUS,
                        help="metres from the city centre; defaults to the routable disc")
    args = parser.parse_args()

    gdf = fetch(args.cache_dir, args.radius)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    out = args.cache_dir / PLACES
    gdf.to_parquet(out)

    print(f"{len(gdf):,} named places -> {out}")
    for col in ("amenity", "shop", "public_transport", "leisure", "tourism"):
        if col in gdf.columns:
            top = gdf[col].value_counts().head(3)
            if len(top):
                print(f"  {col}: " + ", ".join(f"{k} ({v})" for k, v in top.items()))


if __name__ == "__main__":
    main()
