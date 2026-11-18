"""The synthetic file generator of the ingestion benchmarks (ADR 0105)."""

import hashlib
import json

import numpy as np
import pytest

from benchmarks.ingest import generate
from benchmarks.ingest.generate import CLASSES, Case, ensure

TARGET = 200_000


def _frame_counts(path):
    """Atoms per frame, read from the count lines (each frame is n + 2 lines)."""
    counts, lines = [], path.read_text().split("\n")
    i = 0
    while i < len(lines) and lines[i].strip():
        n = int(lines[i])
        counts.append(n)
        i += n + 2
    return counts


def test_same_case_in_two_folders_gives_identical_bytes(tmp_path):
    case = Case("100MB", "small", "variable", seed=3)
    a, ma = ensure(case, tmp_path / "a", workers=1, target_bytes=TARGET)
    b, mb = ensure(case, tmp_path / "b", workers=1, target_bytes=TARGET)
    assert a.read_bytes() == b.read_bytes()
    assert ma["sha256"] == mb["sha256"] == hashlib.sha256(a.read_bytes()).hexdigest()
    assert ma["bytes"] >= TARGET


def test_the_worker_count_does_not_change_the_file(tmp_path):
    case = Case("100MB", "medium", "variable")
    a, ma = ensure(case, tmp_path / "a", workers=1, target_bytes=TARGET)
    b, mb = ensure(case, tmp_path / "b", workers=3, target_bytes=TARGET)
    assert a.read_bytes() == b.read_bytes()
    assert ma["sha256"] == mb["sha256"]


def test_another_seed_gives_another_file(tmp_path):
    a, ma = ensure(Case("100MB", "small", "constant", 0), tmp_path, workers=1, target_bytes=TARGET)
    b, mb = ensure(Case("100MB", "small", "constant", 1), tmp_path, workers=1, target_bytes=TARGET)
    assert ma["sha256"] != mb["sha256"]


@pytest.mark.parametrize("atoms", ["small", "medium"])
def test_atom_counts_follow_the_class(tmp_path, atoms):
    low, high, constant = CLASSES[atoms]
    path, m = ensure(Case("100MB", atoms, "variable"), tmp_path, workers=1, target_bytes=TARGET)
    counts = _frame_counts(path)
    assert len(counts) == m["frames"] and sum(counts) == m["atoms_total"]
    assert all(low <= n <= high for n in counts)
    assert len(set(counts)) > 1
    path, m = ensure(Case("100MB", atoms, "constant"), tmp_path, workers=1, target_bytes=TARGET)
    assert set(_frame_counts(path)) == {constant}


def test_a_large_file_stops_on_a_small_chunk(tmp_path):
    """Chunks stay a few MB, so a 100 MB target is not overshot tenfold."""
    path, m = ensure(Case("100MB", "large", "variable"), tmp_path, workers=1, target_bytes=TARGET)
    assert TARGET <= m["bytes"] < TARGET + 10_000_000


def test_ffast_reads_the_file_and_finds_the_prediction_keys(tmp_path):
    from ffast.readers import chunks
    from ffast.readers.xyz_reader import XyzReader

    path, m = ensure(Case("100MB", "small", "variable"), tmp_path, workers=1, target_bytes=TARGET)
    loaded = chunks.read_arrays(XyzReader, str(path), workers=1, use_cache=False)
    assert loaded.n_file_frames == m["frames"]
    assert int(np.sum(loaded.arrays["n_atoms"])) == m["atoms_total"]
    keys = set(loaded.arrays) | set(loaded.contents)
    assert any("forces_pred" in str(k) for k in keys), keys
    assert any("energy_pred" in str(k) for k in keys), keys


def test_a_second_ensure_reuses_the_file(tmp_path):
    case = Case("100MB", "small", "constant")
    path, _ = ensure(case, tmp_path, workers=1, target_bytes=TARGET)
    before = path.stat().st_mtime_ns
    again, _ = ensure(case, tmp_path, workers=1, target_bytes=TARGET)
    assert again == path and path.stat().st_mtime_ns == before


def test_a_changed_manifest_version_regenerates(tmp_path):
    case = Case("100MB", "small", "constant")
    path, _ = ensure(case, tmp_path, workers=1, target_bytes=TARGET)
    manifest_path = path.with_suffix(".json")
    manifest = json.loads(manifest_path.read_text())
    manifest["generator_version"] = generate.GENERATOR_VERSION + 1
    manifest_path.write_text(json.dumps(manifest))
    before = path.stat().st_mtime_ns
    path, again = ensure(case, tmp_path, workers=1, target_bytes=TARGET)
    assert again["generator_version"] == generate.GENERATOR_VERSION
    assert path.stat().st_mtime_ns != before


def test_too_little_disk_is_refused(tmp_path, monkeypatch):
    class Usage:
        free = 10

    monkeypatch.setattr(generate.shutil, "disk_usage", lambda _: Usage)
    with pytest.raises(RuntimeError, match="not enough free disk"):
        ensure(Case("100MB", "small", "constant"), tmp_path, workers=1, target_bytes=TARGET)
