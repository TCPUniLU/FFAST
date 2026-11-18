"""One file, every way of loading it, one fingerprint (ADR 0107).

Full arrays in memory, a memory-saving indexed load and a memory-saving
one-pass scan all cut a column into the same 16,384-row chunks, so they give
the same dataset fingerprint and the same digest per column.
"""
import numpy as np
import pytest

from ffast.cache.fingerprint import CHUNK_ROWS, array_digest
from ffast.io import budget as resources
from ffast.io import frame_cache, frame_index
from ffast.io.batched import BatchedArray
from ffast.readers import chunks, text
from ffast.readers.dataset import ReaderDataset
from ffast.readers.xyz_reader import XyzReader

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")


@pytest.fixture
def small_budget(monkeypatch, tmp_path):
    owner = resources.Budget(ram_bytes=512 * 1024, parsed_bytes=2**30, result_bytes=8 * 2**20)
    owner.limit = 16 * 2**20        # 1 MiB scan windows
    monkeypatch.setattr(resources, "_budget", owner)
    monkeypatch.setenv("FFAST_RESULT_CACHE_DIR", str(tmp_path / "results"))
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    yield owner


def write_frames(path, n, atoms):
    """``n`` frames of ``atoms`` atoms each: a per-frame energy, per-atom forces."""
    rng = np.random.default_rng(5)
    with open(path, "w") as f:
        for i in range(n):
            f.write(f"{atoms}\nProperties=species:S:1:pos:R:3:forces:R:3 energy={rng.normal():.6f}\n")
            for j in range(atoms):
                x = rng.normal(size=6)
                f.write(f"{'CH'[(i + j) % 2]} {x[0]:.6f} {x[1]:.6f} {x[2]:.6f} {x[3]:.6f} {x[4]:.6f} {x[5]:.6f}\n")
    return str(path)


def load(path, mode):
    """``(dataset, {column: digest})`` for one way of loading the file."""
    if mode == "full":
        kwargs = dict(use_cache=False)
    elif mode == "indexed":
        kwargs = dict(memory_mode="saving", select=lambda n: range(n))
    else:
        kwargs = dict(memory_mode="saving")
    loaded = chunks.read_arrays(XyzReader, path, workers=1, **kwargs)
    ds = ReaderDataset(path, loaded, "xyz")
    digests = {}
    for key, value in loaded.arrays.items():
        if key == "n_atoms":
            continue
        digests[key] = (value.content_digest() if isinstance(value, BatchedArray)
                        else array_digest(np.asarray(value)))
    return ds, digests


@pytest.mark.parametrize("n, atoms", [
    (300, 7),               # a few windows' worth, one chunk
    (CHUNK_ROWS + 500, 1),  # more than 16,384 frames: per-frame columns cross a chunk
    (2000, 20),             # 40,000 atoms: per-atom columns cross a chunk, several windows
], ids=["small", "many frames", "many atoms"])
def test_every_way_of_loading_gives_the_same_fingerprint_and_column_digests(
        tmp_path, small_budget, monkeypatch, n, atoms):
    from ffast.readers import indexed

    scans = []
    real = indexed.IndexedStore._scan_source
    monkeypatch.setattr(indexed.IndexedStore, "_scan_source",
                        lambda self, progress: (scans.append(1), real(self, progress))[1])
    path = write_frames(tmp_path / "f.xyz", n, atoms)
    results = {}
    for mode in ("full", "indexed", "one-pass"):
        frame_cache_dir = tmp_path / "parsed"
        if frame_cache_dir.exists():
            import shutil
            shutil.rmtree(frame_cache_dir)
        results[mode] = load(path, mode)
    assert scans == [1]                       # only the one-pass mode scanned
    assert results["full"][0].storage is None
    assert results["indexed"][0].storage is not None
    if n == 2000:
        assert len(results["one-pass"][0].storage.ranges) > 1
    full_fp = results["full"][0].getFingerprint()
    full_digests = results["full"][1]
    assert {"positions", "numbers"} <= full_digests.keys()
    for mode in ("indexed", "one-pass"):
        ds, digests = results[mode]
        assert ds.getFingerprint() == full_fp, mode
        assert digests == full_digests, mode


def test_a_prediction_from_a_separate_file_and_the_same_values_as_a_column_share_a_ghost_fingerprint(tmp_path):
    """The ghost fingerprint hashes the prediction's energies and forces: the
    same values must give the same one wherever they came from, flat or as a
    flat-frames array."""
    from ffast.cache.fingerprint import fingerprintFromArraysAndStrings

    rng = np.random.default_rng(1)
    n = CHUNK_ROWS + 5
    energies = rng.normal(size=n)
    forces = rng.normal(size=(n * 2, 3))
    from_file = np.load  # a prediction file stores them as saved arrays
    np.savez(tmp_path / "p.npz", E=energies, F=forces)
    saved = from_file(tmp_path / "p.npz")
    as_column = [energies.copy(), forces.copy()]
    assert (fingerprintFromArraysAndStrings(saved["E"], saved["F"], "pred")
            == fingerprintFromArraysAndStrings(*as_column, "pred"))
    batched = BatchedArray(forces.shape, forces.dtype, lambda a, b: forces[a:b], label="F")
    assert (fingerprintFromArraysAndStrings(as_column[0], batched, "pred")
            == fingerprintFromArraysAndStrings(*as_column, "pred"))
