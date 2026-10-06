"""A build must fail before releasing scores the deployed router cannot use."""

import datetime as dt
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from backend.config import GRAPH_RADIUS
from backend.core import scores, streets


def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "validate_runtime_assets.py"
    spec = importlib.util.spec_from_file_location("validate_runtime_assets", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime_assets = load_script()
DATE = dt.date(2026, 9, 23)
TIMES = [dt.time(8, 0), dt.time(12, 0)]


def edge_index(count=2, start=0):
    return pd.MultiIndex.from_tuples(
        [(i, i + 1, 0) for i in range(start, start + count)], names=["u", "v", "key"]
    )


def bundle(tmp_path, monkeypatch, *, score_index=None):
    monkeypatch.setattr(runtime_assets, "daylight_times", lambda *args: TIMES)
    index = edge_index()
    path = streets.edges_path(tmp_path, GRAPH_RADIUS)
    path.parent.mkdir(parents=True)
    pd.DataFrame({"length": [100.0, 200.0]}, index=index).to_parquet(path)
    if score_index is None:
        score_index = index
    for at in TIMES:
        frame = pd.DataFrame({scores.COLUMN: [0.5] * len(score_index)}, index=score_index)
        scores.save(frame, tmp_path, GRAPH_RADIUS, DATE, at)
    return index


def test_valid_release_matches_the_pinned_network(tmp_path, monkeypatch):
    bundle(tmp_path, monkeypatch)

    assert runtime_assets.validate(tmp_path) == (DATE, len(TIMES), 2)


def test_present_scores_for_a_different_graph_fail_the_build(tmp_path, monkeypatch):
    bundle(tmp_path, monkeypatch, score_index=edge_index(start=100))

    with pytest.raises(ValueError, match="do not match the street network"):
        runtime_assets.validate(tmp_path)


def test_every_daylight_stamp_is_required(tmp_path, monkeypatch):
    bundle(tmp_path, monkeypatch)
    scores.scores_path(tmp_path, GRAPH_RADIUS, DATE, TIMES[-1]).unlink()

    with pytest.raises(ValueError, match="Missing routing scores.*12:00"):
        runtime_assets.validate(tmp_path)


def test_missing_scores_fail_before_the_street_index_is_read(tmp_path, monkeypatch):
    bundle(tmp_path, monkeypatch)
    scores.scores_path(tmp_path, GRAPH_RADIUS, DATE, TIMES[0]).unlink()

    def no_read(*args, **kwargs):
        raise AssertionError("no street table needs to be loaded for missing scores")

    monkeypatch.setattr(runtime_assets.pd, "read_parquet", no_read)
    with pytest.raises(ValueError, match="Missing routing scores"):
        runtime_assets.validate(tmp_path)


def test_a_bundle_with_no_date_cannot_pass(tmp_path):
    with pytest.raises(ValueError, match="found 0"):
        runtime_assets.validate(tmp_path)


def test_multiple_dates_need_an_explicit_selection(tmp_path, monkeypatch):
    bundle(tmp_path, monkeypatch)
    scores.scores_dir(tmp_path, GRAPH_RADIUS, dt.date(2026, 9, 22)).mkdir()

    with pytest.raises(ValueError, match="found 2"):
        runtime_assets.validate(tmp_path)
    assert runtime_assets.validate(tmp_path, DATE) == (DATE, len(TIMES), 2)


def test_corrupt_score_file_fails_the_build(tmp_path, monkeypatch):
    bundle(tmp_path, monkeypatch)
    scores.scores_path(tmp_path, GRAPH_RADIUS, DATE, TIMES[0]).write_bytes(b"truncated")

    with pytest.raises(ValueError, match="unreadable"):
        runtime_assets.validate(tmp_path)


def test_missing_score_column_fails_with_a_useful_error(tmp_path, monkeypatch):
    index = bundle(tmp_path, monkeypatch)
    pd.DataFrame({"wrong_column": [0.5, 0.5]}, index=index).to_parquet(
        scores.scores_path(tmp_path, GRAPH_RADIUS, DATE, TIMES[0]))

    with pytest.raises(ValueError, match="Invalid routing scores"):
        runtime_assets.validate(tmp_path)


@pytest.mark.parametrize("fraction", [-0.1, 1.1, float("inf")])
def test_invalid_shade_fractions_fail_the_build(tmp_path, monkeypatch, fraction):
    index = bundle(tmp_path, monkeypatch)
    scores.save(pd.DataFrame({scores.COLUMN: [fraction, 0.5]}, index=index),
                tmp_path, GRAPH_RADIUS, DATE, TIMES[0])

    with pytest.raises(ValueError, match="Invalid shade fractions"):
        runtime_assets.validate(tmp_path)


def test_validation_reads_no_graph_geometry(tmp_path, monkeypatch):
    bundle(tmp_path, monkeypatch)

    def no_shapes(*args, **kwargs):
        raise AssertionError("validation needs the edge index alone")

    monkeypatch.setattr(runtime_assets.streets, "load", no_shapes)
    assert runtime_assets.validate(tmp_path) == (DATE, len(TIMES), 2)
