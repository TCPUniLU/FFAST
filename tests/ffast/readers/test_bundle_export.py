"""Export writes a complete portable bundle and nothing else (ADR 0110, step 3)."""
import os
import sys

import numpy as np
import pytest

from ffast.io import budget as resources
from ffast.io import frame_cache, frame_index
from ffast.readers import bundle, text
from ffast.readers.contract import ReaderError
from tests.ffast.readers.test_scan_naming import folder, forget_ram  # noqa: F401

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")


def write_xyz(path, n=3000, atoms=40, variable=False, predictions=False):
    """About 4 one-megabyte windows at the defaults. ``variable`` changes the
    atom count and the formula from frame to frame; ``predictions`` adds a
    second energy, force set and a per-atom field to every frame."""
    props = "species:S:1:pos:R:3:forces:R:3"
    if predictions:
        props += ":pred_forces:R:3:mag:R:1"
    with open(path, "w") as f:
        for i in range(n):
            k = atoms + (i % 7 if variable else 0)
            head = f"Properties={props} energy={i * 0.5}"
            if predictions:
                head += f" pred_energy={i * 0.5 + 0.25}"
            f.write(f"{k}\n{head}\n")
            for j in range(k):
                species = "C" if (not variable or j % 3 or i % 2) else "H"
                row = f"{species} {i}.125 {j}.25 -1.5 0.1 0.2 0.3"
                if predictions:
                    row += f" 0.2 0.1 {j * 0.01} {j}.5"
                f.write(row + "\n")
    return str(path)


def load_store(path):
    """The ordinary memory-saving load of ``path``: its store."""
    from ffast.readers.xyz_reader import XyzReader
    forget_ram()
    arrays, *_ = text.read_level2(XyzReader, path, memory_mode="saving", workers=1)
    return arrays.store


@pytest.mark.parametrize("kind", ["constant", "variable", "predictions"])
def test_an_export_passes_its_own_checks_and_holds_every_column(folder, tmp_path, kind):  # noqa: F811
    path = write_xyz(tmp_path / "a.xyz", variable=kind == "variable", predictions=kind == "predictions")
    store = load_store(path)
    assert len(store.ranges) >= 3
    target = tmp_path / "out" / "a.ffast"
    description = bundle.export(path, target)
    assert not [p for p in target.parent.iterdir() if ".part-" in p.name]
    checked = bundle.read_description(target)
    assert checked["n_frames"] == 3000 and checked["columns"].keys() == store.info.keys()
    assert checked["source"]["name"] == "a.xyz" and checked["source"]["size"] == os.path.getsize(path)
    assert [r["file"] for r in description["ranges"]] == [f"batches/{a}-{b}.npz" for a, b in store.ranges]
    assert np.load(target / bundle.COUNTS).tolist() == store.counts.tolist()
    offsets = np.load(target / bundle.OFFSETS)
    assert len(offsets) == 3001 and offsets[0] == 0 and offsets[-1] == os.path.getsize(path)
    for key, facts in checked["columns"].items():
        assert facts["digest"] == store.info[key]["digest"]
    if kind == "predictions":
        assert {"energy:pred_energy", "forces:pred_forces", "atom:mag"} <= checked["columns"].keys()
    # Every batch holds every column.
    for entry in checked["ranges"]:
        with np.load(target / entry["file"]) as batch:
            assert "__omitted__" not in batch.files
            assert set(store.info) - {"n_atoms"} <= set(batch.files)


def test_a_cache_trimmed_to_level_one_still_exports_every_column(folder, tmp_path, monkeypatch):  # noqa: F811
    path = write_xyz(tmp_path / "a.xyz")
    owner = resources.Budget(ram_bytes=512 * 1024, parsed_bytes=2_500_000, result_bytes=8 * 2**20)
    owner.limit = 16 * 2**20
    monkeypatch.setattr(resources, "_budget", owner)
    store = load_store(path)
    omitted = 0
    for a, b in store.ranges:
        with np.load(store._cache_path(a, b)) as batch:
            omitted += "__omitted__" in batch.files
    assert omitted, "the cache was meant to be too small for positions"
    target = tmp_path / "a.ffast"
    bundle.export(path, target)
    checked = bundle.read_description(target)
    for entry in checked["ranges"]:
        with np.load(target / entry["file"]) as batch:
            assert "positions" in batch.files and "forces:forces" in batch.files
            assert "__omitted__" not in batch.files


def test_an_export_without_a_cache_parses_everything_and_rebuilds_the_digests(folder, tmp_path, monkeypatch):  # noqa: F811
    path = write_xyz(tmp_path / "a.xyz", variable=True)
    owner = resources.Budget(ram_bytes=512 * 1024, parsed_bytes=0, result_bytes=8 * 2**20)
    owner.limit = 16 * 2**20
    monkeypatch.setattr(resources, "_budget", owner)
    target = tmp_path / "a.ffast"
    bundle.export(path, target)
    checked = bundle.read_description(target)           # chunk digests agree with the columns
    assert checked["n_frames"] == 3000
    assert not folder.exists() or not list(folder.glob("*.npz"))


def test_a_failure_in_the_middle_leaves_no_folder(folder, tmp_path, monkeypatch):  # noqa: F811
    from ffast.readers import indexed
    path = write_xyz(tmp_path / "a.xyz")
    real = indexed._write_batch
    calls = []

    def failing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(*args, **kwargs)

    # Force the parse-and-write path for every batch (no cache to copy).
    store = load_store(path)
    for a, b in store.ranges:
        store._cache_path(a, b).unlink()
    monkeypatch.setattr(indexed, "_write_batch", failing)
    out = tmp_path / "out"
    with pytest.raises(OSError, match="disk full"):
        bundle.export(path, out / "a.ffast")
    assert len(calls) == 2
    assert list(out.iterdir()) == []


def test_a_cancel_leaves_no_folder(folder, tmp_path):  # noqa: F811
    from ffast.metrics import stop
    path = write_xyz(tmp_path / "a.xyz")
    load_store(path)
    count = [0]

    def cancelled():
        count[0] += 1
        return count[0] > 3

    token = stop.running_under(cancelled)
    try:
        with pytest.raises(stop.Stopped):
            bundle.export(path, tmp_path / "out" / "a.ffast")
    finally:
        stop.reset(token)
    assert list((tmp_path / "out").iterdir()) == []


def test_an_existing_target_is_refused_until_force(folder, tmp_path):  # noqa: F811
    path = write_xyz(tmp_path / "a.xyz", n=300)
    target = tmp_path / "a.ffast"
    bundle.export(path, target)
    (target / "mine.txt").write_text("keep")
    with pytest.raises(ReaderError, match="already exists; use --force"):
        bundle.export(path, target)
    assert (target / "mine.txt").exists()
    bundle.export(path, target, force=True)
    assert not (target / "mine.txt").exists()
    bundle.read_description(target)
    assert [p.name for p in tmp_path.iterdir() if ".old-" in p.name or ".part-" in p.name] == []


def test_a_file_that_is_not_xyz_is_refused(folder, tmp_path):  # noqa: F811
    other = tmp_path / "a.npz"
    np.savez(other, x=np.zeros(3))
    with pytest.raises(ReaderError, match="not an XYZ file"):
        bundle.export(other, tmp_path / "a.ffast")
    assert not (tmp_path / "a.ffast").exists()


def test_too_little_disk_space_is_refused_before_anything_is_written(folder, tmp_path, monkeypatch):  # noqa: F811
    import shutil
    path = write_xyz(tmp_path / "a.xyz", n=300)
    load_store(path)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 90, 10))
    with pytest.raises(ReaderError, match="Not enough disk space"):
        bundle.export(path, tmp_path / "out" / "a.ffast")
    assert list((tmp_path / "out").iterdir()) == []


def test_export_adds_to_the_parsed_cache_only_what_an_ordinary_load_does(folder, tmp_path):  # noqa: F811
    path = write_xyz(tmp_path / "a.xyz")
    load_store(path)
    before = sorted((p.name, p.stat().st_size) for p in folder.iterdir())
    pins = list(folder.parent.glob("*pins*"))
    bundle.export(path, tmp_path / "a.ffast")
    after = sorted((p.name, p.stat().st_size) for p in folder.iterdir() if not p.name.endswith(".open"))
    assert after == [x for x in before if not x[0].endswith(".open")]
    assert list(folder.parent.glob("*pins*")) == pins


def test_the_cli_writes_a_bundle_and_says_how_big_and_how_long(folder, tmp_path, capsys):  # noqa: F811
    from ffast.cli import cache as cli
    import argparse
    path = write_xyz(tmp_path / "a.xyz", n=300)
    parser = argparse.ArgumentParser()
    cli.register(parser.add_subparsers())
    args = parser.parse_args(["cache", "export", path, "-o", str(tmp_path / "a.ffast")])
    args.func(args)
    out = capsys.readouterr().out
    assert out.startswith("Wrote") and " GB in " in out and out.rstrip().endswith(" s")
    bundle.read_description(tmp_path / "a.ffast")
    args = parser.parse_args(["cache", "export", path, "-o", str(tmp_path / "a.ffast")])
    with pytest.raises(SystemExit):
        args.func(args)
    assert "already exists" in capsys.readouterr().err
