"""Export the untagged buildings worth surveying, highest shadow-mass first.

A third of Astana's shadow-mass sits on buildings with no height tag, but that
gap is concentrated: roughly 2,500 buildings carry half of it. This writes them
out in priority order, with a JOSM/iD-ready OSM link per row, so the storeys can
be counted off imagery and either contributed back to OSM or dropped into a
local height-override table.

    uv run python scripts/export_survey_queue.py --top 2500
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from height_coverage import load_buildings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--place", default="Astana, Kazakhstan")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    parser.add_argument("--top", type=int, default=2500)
    parser.add_argument("--out", type=Path, default=Path("data/survey_queue.csv"))
    args = parser.parse_args()

    gdf = load_buildings(args.place, args.cache_dir)
    untagged = gdf[gdf["levels"].isna()].sort_values("shadow_mass", ascending=False)
    queue = untagged.head(args.top).to_crs(4326)

    centroids = queue.geometry.representative_point()
    out = pd.DataFrame(
        {
            "osm_element": queue["osm_element"].to_numpy(),
            "osm_id": queue["osm_id"].to_numpy(),
            "building": queue["building"].to_numpy(),
            "area_m2": queue["area_m2"].round(0).to_numpy(),
            "lat": centroids.y.round(6).to_numpy(),
            "lon": centroids.x.round(6).to_numpy(),
            "shadow_mass": queue["shadow_mass"].round(1).to_numpy(),
            "levels": "",  # <- fill this in
            "height_source": "manual",
        }
    )
    out["osm_url"] = [
        f"https://www.openstreetmap.org/{el}/{oid}"
        for el, oid in zip(out["osm_element"], out["osm_id"])
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)

    covered = queue["shadow_mass"].sum() / untagged["shadow_mass"].sum()
    print(f"wrote {len(out):,} rows -> {args.out}")
    print(f"covers {100 * covered:.1f}% of untagged shadow-mass")
    print(f"top types: {dict(out['building'].value_counts().head(5))}")


if __name__ == "__main__":
    main()
