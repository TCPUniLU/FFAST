"""The storage bench takes FFAST's own batch set as input (ADR 0109 step 1)."""

import json

import numpy as np
import pytest

pytest.importorskip("zarr")

from benchmarks.ingest.generate import Case, ensure
from benchmarks.storage.__main__ import main
from benchmarks.storage.source import Source, batch_files, read_restored

PRED_KEYS = ["--profiler-arg=--pred-energy-key=energy_pred",
             "--profiler-arg=--pred-force-key=forces_pred"]


@pytest.mark.parametrize("counts", ["constant", "variable"])
def test_source_keeps_the_batches_ffast_writes(tmp_path, counts):
    path, manifest = ensure(Case("100MB", "small", counts), tmp_path / "data",
                            workers=1, target_bytes=400_000)
    out = tmp_path / "source"
    assert main(["source", str(path), "--out", str(out), *PRED_KEYS]) == 0
    info = json.loads((out / "source.json").read_text())
    assert info["frames"] == manifest["frames"]
    assert info["atoms"] == manifest["atoms_total"]
    assert info["omitted_in_some_batch"] == []
    assert info["file_bytes"] == manifest["bytes"]
    files = batch_files(out / info["batches_dir"])[1]
    assert [b["file"] for b in info["batches"]] == [p.name for _, _, p in files]
    assert info["bytes"] == sum(p.stat().st_size for _, _, p in files)
    assert sum(b["frames"] for b in info["batches"]) == info["frames"]
    assert sum(b["atoms"] for b in info["batches"]) == info["atoms"]
    source = Source(out)
    assert source.n_atoms.sum() == manifest["atoms_total"]
    assert "positions" in source.columns and source.force_columns
    part = read_restored(source.batch_path(0))
    assert part["numbers"].dtype != np.uint8 or source.restore == []
    assert len(part["positions"]) == source.batches[0]["atoms"]


def test_source_refuses_a_folder_that_is_in_use(tmp_path, capsys):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("x")
    (tmp_path / "f.extxyz").write_text("1\nProperties=species:S:1:pos:R:3\nH 0 0 0\n")
    assert main(["source", str(tmp_path / "f.extxyz"), "--out", str(tmp_path / "out")]) == 2
    assert "not empty" in capsys.readouterr().err
