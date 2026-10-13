"""The permanent tree layer keeps crowns independently of seasonal shadows."""

import importlib.util
import json
import subprocess
from pathlib import Path

import geopandas as gpd
import pytest
import shapely

from backend.config import CRS, LAT, LON
from backend.core import trees

spec = importlib.util.spec_from_file_location(
    "export_tree_tiles",
    Path(__file__).resolve().parents[1] / "scripts" / "export_tree_tiles.py",
)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)

CENTRE = gpd.GeoSeries([shapely.Point(LON, LAT)], crs=4326).to_crs(CRS).iloc[0]


def crowns(geometry, crs=CRS):
    return gpd.GeoDataFrame(
        {"height_m": [4.0] * len(geometry),
         "height_source": [trees.MEASURED_SOURCE] * len(geometry)},
        geometry=geometry, crs=crs,
    )


def write_osm(cache_dir):
    gpd.GeoDataFrame(geometry=[CENTRE], crs=CRS).to_parquet(cache_dir / trees.TREES)


def test_crowns_keep_their_outline_holes_and_parts_in_longitude_latitude():
    outer = shapely.box(CENTRE.x, CENTRE.y, CENTRE.x + 20, CENTRE.y + 20)
    hole = shapely.box(CENTRE.x + 5, CENTRE.y + 5, CENTRE.x + 10, CENTRE.y + 10)
    measured = shapely.MultiPolygon([
        outer.difference(hole),
        shapely.box(CENTRE.x + 30, CENTRE.y, CENTRE.x + 35, CENTRE.y + 5),
    ])
    osm = crowns([CENTRE.buffer(3)]).to_crs(4326)

    frame = export.canopy_frame(osm, crowns([measured]))

    assert frame.crs.to_epsg() == 4326
    assert list(frame.columns) == ["geometry"]
    assert len(frame) == 2
    assert frame.geometry.is_valid.all()
    assert frame.geometry.geom_type.tolist() == ["Polygon", "MultiPolygon"]
    assert frame.geometry.iloc[0].equals_exact(osm.geometry.iloc[0], 1e-12)
    assert frame.to_crs(CRS).geometry.iloc[1].equals_exact(measured, 1e-7)
    west, south, east, north = frame.total_bounds
    assert 71 < west < east < 72
    assert 51 < south < north < 52


def test_loading_crowns_does_not_consult_leaf_on_or_shadow_geometry(tmp_path, monkeypatch):
    write_osm(tmp_path)
    measured = CENTRE.buffer(8)
    crowns([measured]).to_parquet(tmp_path / trees.CANOPY_HEIGHT)
    # A static export must work even when the shadow model excludes all trees.
    def seasonal_filter(*args, **kwargs):
        pytest.fail("The permanent layer must not consult the seasonal shadow model")
    monkeypatch.setattr(trees, "leaf_on", seasonal_filter)
    monkeypatch.setattr(trees, "shading_geometry", seasonal_filter)

    frame = export.load_frame(tmp_path)

    assert len(frame) == 2
    assert frame.to_crs(CRS).geometry.iloc[1].equals_exact(measured, 1e-7)


def test_osm_trees_still_export_when_optional_canopy_is_missing(tmp_path):
    write_osm(tmp_path)

    frame = export.load_frame(tmp_path)

    assert len(frame) == 1
    polygon = frame.to_crs(CRS).geometry.iloc[0]
    assert polygon.is_valid and polygon.geom_type == "Polygon"
    assert polygon.equals_exact(CENTRE.buffer(trees.CROWN_RADIUS_M), 1e-7)


def test_preferred_canopy_shapes_win_over_the_optional_lattice(tmp_path):
    write_osm(tmp_path)
    measured = CENTRE.buffer(8)
    crowns([measured]).to_parquet(tmp_path / trees.CANOPY_HEIGHT)
    gpd.GeoDataFrame(geometry=[CENTRE.buffer(100).envelope], crs=CRS).to_parquet(
        tmp_path / trees.CANOPY)

    frame = export.load_frame(tmp_path).to_crs(CRS)

    assert len(frame) == 2
    assert frame.geometry.iloc[1].equals_exact(measured, 1e-7)


@pytest.mark.parametrize("geometry", [
    shapely.Point(71, 51),
    shapely.Polygon(),
    shapely.Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]),
])
def test_malformed_crowns_fail_instead_of_silently_disappearing(geometry):
    with pytest.raises(ValueError, match="Tree crowns"):
        export.canopy_frame(crowns([geometry]), gpd.GeoDataFrame(geometry=[]))


def test_nonempty_crowns_require_a_crs():
    with pytest.raises(ValueError, match="coordinate reference system"):
        export.canopy_frame(crowns([CENTRE.buffer(3)], crs=None),
                            gpd.GeoDataFrame(geometry=[]))


def test_wrong_longitude_latitude_coordinates_fail_before_export():
    with pytest.raises(ValueError, match="longitude/latitude"):
        export.canopy_frame(crowns([shapely.box(700, 500, 701, 501)], crs=4326),
                            gpd.GeoDataFrame(geometry=[]))


def test_tile_export_uses_the_static_layer_and_preserves_crowns_in_geojson(tmp_path, monkeypatch):
    frame = export.canopy_frame(crowns([CENTRE.buffer(3)]),
                                gpd.GeoDataFrame(geometry=[]))
    output = tmp_path / "public" / "tree-canopy.pmtiles"
    output.parent.mkdir()
    output.write_bytes(b"old")

    def compile_tiles(command, *, check):
        assert check is True
        assert command[command.index("--layer") + 1] == "tree-canopy"
        assert command[command.index("--minimum-zoom") + 1] == "11"
        assert command[command.index("--maximum-zoom") + 1] == "16"
        assert "--coalesce-densest-as-needed" in command
        source_file = Path(command[-1])
        # Vite must never copy intermediate geometry into its production build.
        assert not source_file.resolve().is_relative_to(output.parent.resolve())
        source = json.loads(source_file.read_text())
        assert len(source["features"]) == 1
        assert source["features"][0]["properties"] == {}
        assert shapely.geometry.shape(source["features"][0]["geometry"]).equals_exact(
            frame.geometry.iloc[0], 1e-12)
        # A running compiler must not overwrite the previous published asset.
        assert output.read_bytes() == b"old"
        Path(command[command.index("--output") + 1]).write_bytes(b"new")

    monkeypatch.setattr(export.subprocess, "run", compile_tiles)

    assert export.export_tiles(frame, output) == output
    assert output.read_bytes() == b"new"
    assert list(output.parent.iterdir()) == [output]


def test_failed_regeneration_keeps_existing_tiles_and_cleans_staging(tmp_path, monkeypatch):
    frame = export.canopy_frame(crowns([CENTRE.buffer(3)]),
                                gpd.GeoDataFrame(geometry=[]))
    output = tmp_path / "tree-canopy.pmtiles"
    output.write_bytes(b"last successful build")

    def fail(command, *, check):
        Path(command[command.index("--output") + 1]).write_bytes(b"partial tiles")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(export.subprocess, "run", fail)

    with pytest.raises(subprocess.CalledProcessError):
        export.export_tiles(frame, output)

    assert output.read_bytes() == b"last successful build"
    assert list(tmp_path.iterdir()) == [output]


def test_empty_crowns_cannot_replace_existing_tiles(tmp_path):
    frame = export.canopy_frame(gpd.GeoDataFrame(geometry=[]),
                                gpd.GeoDataFrame(geometry=[]))
    assert frame.empty and frame.crs.to_epsg() == 4326
    output = tmp_path / "tree-canopy.pmtiles"
    output.write_bytes(b"last successful build")

    with pytest.raises(ValueError, match="No tree crowns"):
        export.export_tiles(frame, output)

    assert output.read_bytes() == b"last successful build"
