"""The tail of the nightly export: what it deletes, and in what order.

The expensive nine tenths of export_shadow_tiles.py -- loading the city,
cutting tiles, scoring the graph -- is not the part that goes wrong. The
bookkeeping afterwards is, because every step of it deletes something, and
deleting in the wrong order leaves a checkout the API cannot serve. None of it
had a test until the order was changed, which is the worst moment to have none.
"""
import argparse
import datetime as dt
import importlib.util
from pathlib import Path

DATE = dt.date(2026, 9, 13)


def load_script():
    """The script is not a package -- no __init__.py, not on sys.path -- so it
    is loaded by location rather than imported by name.
    """
    path = Path(__file__).resolve().parents[1] / "scripts" / "export_shadow_tiles.py"
    spec = importlib.util.spec_from_file_location("export_shadow_tiles", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export = load_script()


def args_for(tmp_path: Path, *, date: dt.date = DATE, scores_only: bool = False):
    """What main() would have parsed, with both directories real.

    Real because finalise globs them, and a glob against a directory that does
    not exist is the cheapest way for a stub to quietly disagree with the thing
    it stands in for.
    """
    out_dir, cache_dir = tmp_path / "shadows", tmp_path / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return argparse.Namespace(
        out_dir=out_dir, cache_dir=cache_dir, date=date, scores_only=scores_only)


def record(monkeypatch) -> list[str]:
    """The publishing step and the destructive one, in the order they are called."""
    calls: list[str] = []
    monkeypatch.setattr(export, "write_manifest",
                        lambda out_dir, date, times: calls.append("manifest"))
    monkeypatch.setattr(export.scores, "prune",
                        lambda cache, radius, keep: calls.append("prune") or [])
    return calls


def test_scores_are_pruned_only_after_the_manifest_is_written(tmp_path, monkeypatch):
    """The window this ordering exists to close.

    The map asks for whatever date the manifest names. Prune first and there is
    a moment where the manifest still names yesterday while yesterday's scores
    are already gone -- every request landing in it a 503, and a crash anywhere
    inside it leaving the checkout that way. Prune second and the window holds
    both dates instead of neither.
    """
    calls = record(monkeypatch)

    export.finalise(args_for(tmp_path), written={}, scored=True)

    assert calls == ["manifest", "prune"]


def test_prune_keeps_exactly_the_date_the_manifest_names(tmp_path, monkeypatch):
    """Ordering is half of it; agreeing on the date is the other half. A prune
    keeping some other date would pass the test above and still delete the
    scores for the date it had just published.
    """
    seen: dict[str, dt.date] = {}
    monkeypatch.setattr(export, "write_manifest",
                        lambda out_dir, date, times: seen.update(published=date))
    monkeypatch.setattr(export.scores, "prune",
                        lambda cache, radius, keep: seen.update(kept=keep) or [])

    export.finalise(args_for(tmp_path, date=DATE), written={}, scored=True)

    assert seen["published"] == DATE
    assert seen["kept"] == DATE


def test_scores_only_leaves_the_scores_alone(tmp_path, monkeypatch):
    """--scores-only writes no manifest, so it must not prune either.

    The manifest left standing names whatever the last full run built. Pruning
    to today would delete the scores for that date and leave them deleted --
    the same window as above, except that nothing afterwards closes it.
    """
    calls = record(monkeypatch)

    export.finalise(args_for(tmp_path, scores_only=True), written={}, scored=True)

    assert calls == []


def test_a_tiles_only_run_publishes_but_does_not_prune(tmp_path, monkeypatch):
    """--no-scores never read the graph, so it has written nothing to put in
    the place of what it would be deleting.
    """
    calls = record(monkeypatch)

    export.finalise(args_for(tmp_path), written={}, scored=False)

    assert calls == ["manifest"]


def test_tilesets_the_new_date_has_no_sun_for_are_removed(tmp_path, monkeypatch):
    """December has eight hours of daylight where June has sixteen. Rebuilding
    for the shorter day leaves June's evening tilesets on disk, where they ship
    as dead weight and, worse, still answer when the map asks for them.
    """
    monkeypatch.setattr(export, "write_manifest", lambda *args: None)
    args = args_for(tmp_path)
    kept, stale = args.out_dir / "1300.pmtiles", args.out_dir / "2100.pmtiles"
    for tileset in (kept, stale):
        tileset.write_bytes(b"x")

    export.finalise(args, written={dt.time(13, 0): kept}, scored=False)

    assert kept.exists()
    assert not stale.exists()


def test_scores_only_does_not_delete_the_tilesets_it_never_cut(tmp_path, monkeypatch):
    """The guard the comment in finalise is about, and it is not hypothetical.

    A --scores-only run cuts no tiles, so `written` is empty -- and the stale
    set is every tileset on disk minus `written`. Unguarded, that reads an
    empty `written` as "all of them are stale" and removes the entire map.
    """
    monkeypatch.setattr(export, "write_manifest", lambda *args: None)
    args = args_for(tmp_path, scores_only=True)
    tilesets = [args.out_dir / f"{hour:02d}00.pmtiles" for hour in (9, 13, 17)]
    for tileset in tilesets:
        tileset.write_bytes(b"x")

    export.finalise(args, written={}, scored=True)

    assert all(tileset.exists() for tileset in tilesets)
