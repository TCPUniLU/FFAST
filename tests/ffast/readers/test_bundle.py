"""The portable bundle: source probe and description checks (ADR 0110, step 2)."""
import json

import numpy as np
import pytest

from ffast.cache.fingerprint import SCHEME, ChunkHasher
from ffast.readers import bundle
from ffast.readers.contract import ReaderError


def make_file(path, size, seed=1):
    rng = np.random.default_rng(seed)
    path.write_bytes(rng.integers(0, 256, size, dtype=np.uint8).tobytes())
    return path


def test_probe_of_a_small_file_hashes_the_whole_file(tmp_path):
    path = make_file(tmp_path / "small.xyz", 5000)
    record = bundle.source_probe(path)
    assert record["name"] == "small.xyz" and record["size"] == 5000
    assert record["offsets"] == [0] and record["slice_bytes"] == 5000
    assert bundle.probe_matches(path, record)
    data = bytearray(path.read_bytes())
    data[4999] ^= 1                              # the last byte: inside the one slice
    path.write_bytes(bytes(data))
    assert not bundle.probe_matches(path, record)


def test_probe_of_a_large_file_samples_eight_slices(tmp_path):
    size = 9 * 2**20 + 123
    path = make_file(tmp_path / "big.xyz", size)
    record = bundle.source_probe(path)
    assert len(record["offsets"]) == 8 == len(record["sha256"])
    assert record["offsets"][0] == 0 and record["offsets"][-1] == size - 2**20
    assert record["slice_bytes"] == 2**20
    assert bundle.probe_matches(path, record)
    # A byte outside every slice goes unseen: a quick check, not proof.
    gap = record["offsets"][1] + 2**20 + 10
    assert all(not (o <= gap < o + 2**20) for o in record["offsets"])
    data = bytearray(path.read_bytes())
    data[gap] ^= 1
    path.write_bytes(bytes(data))
    assert bundle.probe_matches(path, record)
    # A byte inside a sampled slice, and a changed size, are seen.
    data[record["offsets"][3] + 5] ^= 1
    path.write_bytes(bytes(data))
    assert not bundle.probe_matches(path, record)
    path.write_bytes(bytes(data) + b"x")
    assert not bundle.probe_matches(path, record)


def test_a_source_is_found_beside_the_bundle_when_the_original_path_is_gone(tmp_path):
    (tmp_path / "data").mkdir()
    original = make_file(tmp_path / "data" / "run.xyz", 3000)
    record = bundle.source_probe(original)
    folder = tmp_path / "out" / "run.ffast"
    folder.mkdir(parents=True)
    assert bundle.find_source(folder, record) == original
    moved = tmp_path / "run.xyz.moved"
    original.rename(moved)
    assert bundle.find_source(folder, record) is None
    (tmp_path / "out" / "run.xyz").write_bytes(moved.read_bytes())
    assert bundle.find_source(folder, record) == (tmp_path / "out" / "run.xyz").resolve()
    (tmp_path / "out" / "run.xyz").write_bytes(b"other" * 600)       # same name, other text
    assert bundle.find_source(folder, record) is None


# -- the description ----------------------------------------------------------

def a_bundle(tmp_path):
    """The smallest folder that passes every check."""
    folder = tmp_path / "b.ffast"
    (folder / "batches").mkdir(parents=True)
    batch = folder / "batches" / "0-4.npz"
    np.savez(batch, x=np.arange(4.0))
    np.save(folder / bundle.COUNTS, np.full(4, 2, dtype=np.int64))
    np.save(folder / bundle.OFFSETS, np.arange(5, dtype=np.int64))
    h = ChunkHasher((), np.float64)
    h.update(np.arange(4.0))
    digests = np.frombuffer(b"".join(h.all_digests()), dtype=np.uint8).reshape(-1, 32)
    np.savez(folder / bundle.CHUNKS, **{"<f8|energy:e": digests})
    bundle.write_description(folder, {
        "scheme": SCHEME.decode(),
        "ranges": [{"start": 0, "stop": 4, "file": "batches/0-4.npz", "bytes": batch.stat().st_size}],
        "columns": {"energy:e": {"dtype": "<f8", "tail": [], "digest": h.hexdigest(),
                                 "float_digest": h.hexdigest(), "has_nan": False}},
    })
    return folder


def edit(folder, change):
    path = folder / bundle.DESCRIPTION
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_a_good_bundle_reads_from_its_folder_or_its_description(tmp_path):
    folder = a_bundle(tmp_path)
    assert bundle.read_description(folder)["schema"] == 1
    assert bundle.read_description(folder / bundle.DESCRIPTION)["scheme"] == SCHEME.decode()


def test_a_newer_schema_says_to_update_ffast(tmp_path):
    folder = a_bundle(tmp_path)
    edit(folder, lambda d: d.update(schema=2))
    with pytest.raises(ReaderError, match="made by a newer FFAST.*update FFAST"):
        bundle.read_description(folder)


def test_a_missing_or_unreadable_description_is_named(tmp_path):
    folder = a_bundle(tmp_path)
    (folder / bundle.DESCRIPTION).write_text("{not json")
    with pytest.raises(ReaderError, match=r"b\.ffast.*cannot be read"):
        bundle.read_description(folder)
    (folder / bundle.DESCRIPTION).unlink()
    with pytest.raises(ReaderError, match="dataset.ffast.json is missing"):
        bundle.read_description(folder)


def test_another_scheme_is_refused(tmp_path):
    folder = a_bundle(tmp_path)
    edit(folder, lambda d: d.update(scheme="old-scheme"))
    with pytest.raises(ReaderError, match="old-scheme"):
        bundle.read_description(folder)


def test_a_missing_batch_is_named(tmp_path):
    folder = a_bundle(tmp_path)
    (folder / "batches" / "0-4.npz").unlink()
    with pytest.raises(ReaderError, match=r"batches/0-4\.npz is missing"):
        bundle.read_description(folder)


def test_a_batch_of_the_wrong_size_is_named(tmp_path):
    folder = a_bundle(tmp_path)
    with open(folder / "batches" / "0-4.npz", "ab") as f:
        f.write(b"x")
    with pytest.raises(ReaderError, match=r"batches/0-4\.npz has .* bytes, not the"):
        bundle.read_description(folder)


def test_chunk_digests_that_disagree_with_a_column_digest_are_refused(tmp_path):
    folder = a_bundle(tmp_path)
    edit(folder, lambda d: d["columns"]["energy:e"].update(digest="0" * 64))
    with pytest.raises(ReaderError, match="chunk digests of energy:e do not match"):
        bundle.read_description(folder)


def test_a_missing_fixed_file_is_named(tmp_path):
    folder = a_bundle(tmp_path)
    (folder / bundle.OFFSETS).unlink()
    with pytest.raises(ReaderError, match="source-offsets.npy is missing"):
        bundle.read_description(folder)
