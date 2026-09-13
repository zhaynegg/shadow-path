"""Build the search index the map's search box reads, and stop asking Nominatim.

    uv run python scripts/export_search_index.py

Three sources, because measuring showed that fewer will not do:

  streets   from the routing graph's own name tags. Coverage is total by
            construction -- the thing being searched is the thing being routed
            over. Building addr:street tags were tried first and reach only 36%
            of named walkable metres: those tags exist where buildings carry
            addresses, which is the dense core, so whole outer districts and
            every industrial road go missing.
  buildings from astana_buildings.parquet, already tracked.
  places    from astana_places.parquet -- cafes, schools, stops, parks. The
            4,000 named things that are where a walk ends but are not buildings.

Two keys ride with every entry so that the frontend needs no transliteration
table of its own: `c` is the name normalised as written, `l` is it folded into
Latin. A query is matched against both, so Cyrillic finds Cyrillic and Latin
finds either.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import re
import warnings
from pathlib import Path

import geopandas as gpd
import osmnx as ox

from backend.config import CACHE_DIR, GRAPH_RADIUS, LAT, LON, today
from backend.core.graph import load_graph

warnings.filterwarnings("ignore")

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "search-index.json"

# Kazakh and Russian Cyrillic, folded towards how a person actually types a name
# into a Latin keyboard rather than towards any official romanisation. қ becomes
# k and not q, ә becomes a and not ä: the goal is that "Kabanbay" reaches
# Қабанбай, not that the result could be transliterated back.
TRANSLIT = {
    "а": "a", "ә": "a", "б": "b", "в": "v", "г": "g", "ғ": "g", "д": "d",
    "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
    "қ": "k", "л": "l", "м": "m", "н": "n", "ң": "n", "о": "o", "ө": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ұ": "u", "ү": "u",
    "ф": "f", "х": "h", "һ": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "", "ы": "i", "і": "i", "ь": "", "э": "e", "ю": "iu", "я": "ia",
}

# The street-type words, which nobody types and which differ between the tag
# that named the road and the tag that addressed the building beside it.
TYPES = ["проспект", "улица", "переулок", "шоссе", "бульвар", "набережная",
         "көшесі", "даңғылы", "алаңы", "тас жолы", "street", "avenue", "road"]


# Kazakh Cyrillic folded onto the Russian letters it is nearest. The city is
# tagged in Kazakh and half of it is typed in Russian: Қабанбай is what OSM
# holds and Кабанбай is what somebody types, and without this they are simply
# different words. Costs nothing and fixes every Kazakh-specific letter at once.
KAZAKH_MAP = {"қ": "к", "ә": "а", "ғ": "г", "ң": "н", "ө": "о",
              "ұ": "у", "ү": "у", "һ": "х", "і": "и", "ё": "е"}
KAZAKH = str.maketrans(KAZAKH_MAP)

# What makes the Latin fold forgiving: y and i are one vowel to a reader, kh and
# h one consonant. Applied to the index keys and to a query alike.
COLLAPSE = [["kh", "h"], ["y", "i"]]


def fold(text: str) -> str:
    """A name reduced to letters, in whatever script it was written in."""
    s = text.lower().translate(KAZAKH)
    for word in TYPES:
        s = s.replace(word, " ")
    return " ".join(re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE).split())


def latinise(text: str) -> str:
    """The same, folded into Latin so the two scripts meet in one place.

    The collapses at the end are what make it forgiving. Someone types Bayterek
    for Байтерек and Khan Shatyr for Хан Шатыр: y and i are the same vowel to a
    reader, and kh and h the same consonant, so both are flattened rather than
    guessed at.
    """
    s = "".join(TRANSLIT.get(ch, ch) for ch in fold(text))
    for a, b in COLLAPSE:
        s = s.replace(a, b)
    return " ".join(s.split())


# Every way OSM has of writing the same name. Measuring the fold against the
# 277 features that carry a human-written name:en showed where its ceiling is:
# a distinctive word of the English name reaches the Cyrillic entry 45% of the
# time, and nearly every remaining miss is a translation rather than a spelling
# -- "Жоғарғы сот" against "Supreme Court", "мешіті" against "mosque". No
# transliteration crosses that, and none needs to: somebody has already written
# the English down in the tag next door. So the keys are built from all of them
# and only the display name comes from `name`.
ALIASES = ["name:en", "name:kk", "name:ru", "alt_name", "short_name",
           "official_name", "int_name", "loc_name", "brand"]


def entry(name: str, point, detail: str, aliases: tuple[str, ...] = ()) -> dict:
    spellings = [name, *aliases]
    return {"n": name, "d": detail, "p": [round(point.y, 5), round(point.x, 5)],
            # Joined rather than kept apart: the frontend asks "is the query in
            # here", and one string answers that for every spelling at once.
            "c": " ".join(dict.fromkeys(fold(s) for s in spellings)),
            "l": " ".join(dict.fromkeys(latinise(s) for s in spellings))}


def street_entries(cache_dir: Path, radius: float) -> list[dict]:
    edges = ox.graph_to_gdfs(load_graph(cache_dir, radius), nodes=False)
    edges["nm"] = edges["name"].apply(lambda v: v[0] if isinstance(v, list) else v)
    named = edges[edges["nm"].notna()]

    rows = []
    for name, part in named.to_crs(4326).groupby("nm"):
        # The midpoint of the longest segment, not the centroid of the whole
        # street: a ring road's centroid is the middle of the city, nowhere near
        # any part of the road itself.
        longest = part.loc[part.to_crs(edges.crs).length.idxmax()].geometry
        rows.append(entry(str(name), longest.interpolate(0.5, normalized=True), "Street"))
    return rows


def feature_entries(gdf: gpd.GeoDataFrame, fallback: str) -> list[dict]:
    """Buildings or places, both of which arrive as a name and a geometry."""
    kinds = [c for c in ("amenity", "shop", "leisure", "tourism", "public_transport",
                         "railway", "office", "healthcare", "historic", "place")
             if c in gdf.columns]

    rows = []
    for _, r in gdf.to_crs(4326).iterrows():
        # What kind of thing it is, or the street it stands on -- something to
        # tell two shops of the same name apart, which in a city of chains is
        # most of them.
        # "yes" is how OSM says a feature *is* an office or a shop without
        # saying what kind, and "Yes" is not a useful thing to print beside a
        # name. Fall back to what the source was instead.
        kind = next((str(r[c]).replace("_", " ").capitalize() for c in kinds
                     if isinstance(r.get(c), str) and r[c] not in ("yes", "no")), fallback)
        street = r.get("addr:street")
        detail = f"{kind} · {street}" if isinstance(street, str) else kind

        spellings = tuple(str(r[c]) for c in ALIASES
                          if c in gdf.columns and isinstance(r.get(c), str))
        rows.append(entry(str(r["name"]), r.geometry.representative_point(),
                          detail, spellings))
    return rows


def within(gdf: gpd.GeoDataFrame, radius: float) -> gpd.GeoDataFrame:
    centre = gpd.GeoSeries(gpd.points_from_xy([LON], [LAT]), crs=4326).to_crs(gdf.crs).iloc[0]
    return gdf[gdf.geometry.distance(centre) <= radius]


def build(cache_dir: Path, radius: float) -> list[dict]:
    rows = street_entries(cache_dir, radius)
    print(f"  {len(rows):>6,} streets, off the routing graph")

    buildings = within(gpd.read_parquet(cache_dir / "astana_buildings.parquet"), radius)
    named = buildings[buildings["name"].notna()]
    rows += feature_entries(named, "Building")
    print(f"  {len(named):>6,} named buildings")

    places_path = cache_dir / "astana_places.parquet"
    if places_path.exists():
        places = within(gpd.read_parquet(places_path), radius)
        rows += feature_entries(places, "Place")
        print(f"  {len(places):>6,} named places")
    else:
        print(f"  no {places_path.name} -- run scripts/fetch_places.py; "
              "the index will find streets and buildings but not what is in them")

    return dedupe(rows)


# Two entries are the same thing if they read the same and stand in the same
# place. Name alone is far too strong a claim in a city of chains -- twenty
# Magnums are twenty destinations -- and an exact point is far too weak, since
# the courthouse arrives once as a building outline and again as amenity=
# courthouse, a few metres apart and listed twice in the panel.
SAME_PLACE_M = 80.0
M_PER_DEG_LAT = 111_320.0


def dedupe(rows: list[dict]) -> list[dict]:
    """First one wins, and the order of the sources is the order of preference:
    a street over a building, a building over a place -- the earlier source
    carries the better detail line."""
    import math

    kept: dict[str, list[dict]] = {}
    unique = []
    for row in rows:
        lat, lon = row["p"]
        near = False
        for other in kept.get(row["c"], ()):
            dlat = (lat - other["p"][0]) * M_PER_DEG_LAT
            dlon = (lon - other["p"][1]) * M_PER_DEG_LAT * math.cos(math.radians(lat))
            if math.hypot(dlat, dlon) <= SAME_PLACE_M:
                near = True
                break
        if near:
            continue
        kept.setdefault(row["c"], []).append(row)
        unique.append(row)
    return unique


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--radius", type=float, default=GRAPH_RADIUS)
    args = parser.parse_args()

    print(f"building the search index for {args.radius / 1000:.0f} km around Astana")
    entries = build(args.cache_dir, args.radius)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "date": today().isoformat(),
        "centre": [LAT, LON],
        "radius_m": args.radius,
        # The rules travel with the data they were applied to. A query has to be
        # folded exactly as the keys were or it matches nothing -- and a copy of
        # these tables in TypeScript would be correct only until one side was
        # edited. Shipping them means regenerating the index is the only way to
        # change the matching, and both halves move together by construction.
        "fold": {
            "chars": KAZAKH_MAP,
            "strip": TYPES,
            "translit": TRANSLIT,
            "collapse": COLLAPSE,
        },
        "entries": entries,
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    args.out.write_text(text, encoding="utf-8")

    raw = len(text.encode())
    print(f"\n{len(entries):,} entries -> {args.out}")
    print(f"  {raw / 1024:.0f} KB raw, {len(gzip.compress(text.encode())) / 1024:.0f} KB gzipped")


if __name__ == "__main__":
    main()
