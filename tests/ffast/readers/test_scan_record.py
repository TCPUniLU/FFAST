"""The progress record of a one-pass scan (ADR 0107, step 4)."""
import json

import numpy as np
import pytest

from ffast.cache.fingerprint import CHUNK_ROWS, ChunkHasher, column_digest_of_chunks
from ffast.metrics import stop
from ffast.readers import chunks, text
from ffast.readers.xyz_reader import XyzReader
from tests.ffast.readers.test_scan_naming import Progress, folder, scan, write_xyz  # noqa: F401

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")


def cancel_after(path, windows):
    cancelled = [False]
    token = stop.running_under(lambda: cancelled[0])
    try:
        with pytest.raises(stop.Stopped):
            scan(path, Progress(lambda done: cancelled.__setitem__(0, done >= windows)))
    finally:
        stop.reset(token)


def whole_arrays(path):
    """Every column of the file as ordinary arrays, read without any cache."""
    loaded = chunks.read_arrays(XyzReader, path, workers=1, use_cache=False)
    return {k: np.asarray(v) for k, v in loaded.arrays.items()}


def test_the_record_names_exactly_the_committed_windows(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz", n=3000, atoms=40)
    cancel_after(path, 2)
    [progress] = folder.glob("*.progress.json")
    identity = progress.name[:-len(".progress.json")]
    record = json.loads(progress.read_text())
    batches = sorted((int(p.name[:-4].split("-")[-2]), int(p.name[:-4].split("-")[-1]))
                     for p in folder.glob("*-*.npz"))
    assert [tuple(r) for r in record["ranges"]] == batches and len(batches) == 2
    assert record["frame_end"] == batches[-1][1]
    # The append-only files are as long as the record says.
    frames_bin = folder / f"{identity}.frames.bin"
    chunks_bin = folder / f"{identity}.chunks.bin"
    assert frames_bin.stat().st_size == record["frames_length"] == 16 * record["frame_end"]
    assert chunks_bin.stat().st_size == record["chunks_length"]
    pairs = np.frombuffer(frames_bin.read_bytes(), dtype="<i8").reshape(-1, 2)
    reference = scan(path)          # an uninterrupted scan of the same file
    assert pairs[:, 0].tolist() == reference.starts[:record["frame_end"]].tolist()
    assert pairs[:, 1].tolist() == reference.counts[:record["frame_end"]].tolist()
    assert record["byte_end"] == int(reference.ends[record["frame_end"] - 1])
    assert record["atom_end"] == int(reference.offsets[record["frame_end"]])


def test_the_tail_holds_the_rows_after_the_last_full_chunk_and_the_digests_match(folder, tmp_path):
    path = write_xyz(tmp_path / "b.xyz", n=3000, atoms=40)
    cancel_after(path, 2)
    [progress] = folder.glob("*.progress.json")
    identity = progress.name[:-len(".progress.json")]
    record = json.loads(progress.read_text())
    source = whole_arrays(path)
    atoms = record["atom_end"]
    assert atoms > 3 * CHUNK_ROWS            # several chunks are already complete
    assert record["tail_file"] == f"{identity}.{record['frame_end']}.tail.npz"
    assert len(list(folder.glob("*.tail.npz"))) == 1        # older tails are gone
    tails = np.load(folder / record["tail_file"])
    digests = {}
    for line in (folder / f"{identity}.chunks.bin").read_text().splitlines():
        key, dtype, index, digest = json.loads(line)
        digests.setdefault((key, dtype), {})[index] = bytes.fromhex(digest)
    for key, facts in record["columns"].items():
        rows = atoms if key in ("positions", "numbers") or key.startswith("forces:") else record["frame_end"]
        for dtype, hasher in facts["hashers"].items():
            expected = ChunkHasher(facts["tail"], np.dtype(dtype))
            expected.update(source[key][:rows])
            assert hasher["rows"] == rows
            tail = tails[hasher["tail"]]
            assert tail.shape[0] == rows % CHUNK_ROWS
            np.testing.assert_array_equal(tail, expected.tail())
            on_disk = digests.get((key, dtype), {})
            assert [on_disk[i] for i in range(hasher["chunks"])] == expected.digests()


def test_a_finished_scan_leaves_no_progress_files_and_a_sidecar_that_rebuilds_every_digest(folder, tmp_path):
    path = write_xyz(tmp_path / "c.xyz", n=3000, atoms=40)
    store = scan(path)
    names = sorted(p.name for p in folder.iterdir())
    assert not [n for n in names if n.endswith((".bin", ".tail.npz", ".progress.json", ".tmp", ".lock")) and n != ".lock"]
    manifest = json.loads((folder / f"{store.identity}.json").read_text())
    assert manifest["chunks"] == f"{store.identity}.chunks.npz"
    sidecar = np.load(folder / manifest["chunks"])
    for key, info in store.info.items():
        row = sidecar[f"{info['dtype'].str}|{key}"]
        assert row.dtype == np.uint8 and row.shape[1] == 32
        assert column_digest_of_chunks(row) == info["digest"]
        wide = sidecar[f"{np.dtype(np.float64).str}|{key}"]
        assert column_digest_of_chunks(wide) == info["float_digest"]


def test_a_batch_that_could_not_be_kept_ends_the_record(folder, tmp_path, monkeypatch):
    from ffast.io import budget as resources

    path = write_xyz(tmp_path / "d.xyz", n=3000, atoms=40)
    resources.budget().parsed_limit = 1          # nothing fits on disk
    store = scan(path)
    assert not list(folder.glob("*.progress.json")) and not list(folder.glob("*.bin"))
    assert store.counts.sum() > 0
