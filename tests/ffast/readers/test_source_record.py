"""A finished scan keeps the digests of its source bytes (ADR 0111, step 2)."""
import json
import os
import shutil

import numpy as np
import pytest

from ffast.cache import source_digest as sd
from ffast.readers import bundle, text
from ffast.readers.indexed import CHUNKS_SUFFIX, SOURCE_DIGESTS, SOURCE_RANGES
from tests.ffast.readers.test_bundle_export import write_xyz as write_extended  # noqa: F401
from tests.ffast.readers.test_bundle_open import exported, opened  # noqa: F401
from tests.ffast.readers.test_scan_naming import folder, scan, write_xyz  # noqa: F401
from tests.ffast.readers.test_scan_record import cancel_after

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")


@pytest.fixture(autouse=True)
def finder_with_digests():
    import ffast_xyz
    if getattr(ffast_xyz, "SOURCE_SCHEME", None) != sd.SCHEME:
        pytest.skip("the installed ffast-xyz predates source digests")


def sidecar(folder, store):
    with np.load(folder / f"{store.identity}{CHUNKS_SUFFIX}") as z:
        return {k: z[k] for k in z.files}


def test_a_finished_scan_covers_the_file_and_each_digest_is_right(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    store = scan(path)
    arrays = sidecar(folder, store)
    ranges, digests = arrays[SOURCE_RANGES], arrays[SOURCE_DIGESTS]
    data = open(path, "rb").read()
    assert ranges.dtype == np.int64 and digests.dtype == np.uint8 and digests.shape == (len(ranges), 32)
    assert len(ranges) >= 3
    assert ranges[0, 0] == 0 and ranges[-1, 1] == len(data)
    assert (ranges[1:, 0] == ranges[:-1, 1]).all()
    for (a, b), digest in zip(ranges.tolist(), digests):
        assert digest.tobytes() == sd.range_digest(data[a:b])
    assert not list(folder.glob("*.source.bin"))            # the record is gone with the scan


def test_blank_lines_after_the_last_frame_are_covered_too(folder, tmp_path):
    path = write_xyz(tmp_path / "t.xyz", n=500)
    with open(path, "a") as f:
        f.write("\n  \n\n")
    store = scan(path)
    ranges = sidecar(folder, store)[SOURCE_RANGES]
    assert ranges[-1, 1] == os.path.getsize(path) > store.ends[-1]


def test_a_resumed_scan_gives_the_sidecar_of_an_uninterrupted_one(folder, tmp_path, monkeypatch):
    from ffast.io import frame_cache
    path = write_xyz(tmp_path / "b.xyz")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "reference")
    reference = sidecar(tmp_path / "reference" / "batches", scan(path))
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    cancel_after(path, 2)
    [source_bin] = folder.glob("*.source.bin")
    assert source_bin.stat().st_size == 48 * 2
    [progress] = folder.glob("*.progress.json")
    assert json.loads(progress.read_text())["source_length"] == 96
    resumed = sidecar(folder, scan(path))
    assert resumed.keys() == reference.keys()
    for key in reference:
        np.testing.assert_array_equal(resumed[key], reference[key], err_msg=key)


def test_bytes_appended_after_the_last_commit_are_ignored_on_resume(folder, tmp_path, monkeypatch):
    path = write_xyz(tmp_path / "c.xyz")
    cancel_after(path, 2)
    [source_bin] = folder.glob("*.source.bin")
    with open(source_bin, "ab") as f:
        f.write(b"\x07" * 48)            # a crash after the append, before the record
    arrays = sidecar(folder, scan(path))
    assert arrays[SOURCE_RANGES][-1, 1] == os.path.getsize(path)
    assert len(arrays[SOURCE_RANGES]) == len(arrays[SOURCE_DIGESTS])


def test_dataset_fingerprints_do_not_change(folder, tmp_path, monkeypatch):
    import ffast_xyz
    from ffast.io import frame_cache
    path = write_xyz(tmp_path / "d.xyz")
    with_digests = scan(path)
    real = ffast_xyz.find_frames
    monkeypatch.setattr(ffast_xyz, "find_frames", lambda *a, **k: real(*a, **k)[:4])
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "plain")
    plain = scan(path)
    assert SOURCE_RANGES not in sidecar(tmp_path / "plain" / "batches", plain)
    assert {k: (v["digest"], v["float_digest"]) for k, v in with_digests.info.items()} == \
           {k: (v["digest"], v["float_digest"]) for k, v in plain.info.items()}
    assert with_digests.identity != "" and with_digests.identity == plain.identity


def test_a_scan_with_a_window_lacking_a_digest_keeps_none(folder, tmp_path, monkeypatch):
    import ffast_xyz
    path = write_xyz(tmp_path / "e.xyz")
    real, calls = ffast_xyz.find_frames, []

    def partly(*a, **k):
        got = real(*a, **k)
        calls.append(1)
        return got[:4] if len(calls) == 2 else got
    monkeypatch.setattr(ffast_xyz, "find_frames", partly)
    store = scan(path)
    assert len(calls) >= 3
    assert SOURCE_RANGES not in sidecar(folder, store) and SOURCE_DIGESTS not in sidecar(folder, store)


def test_an_exported_bundle_carries_the_source_arrays_and_opens(folder, tmp_path):
    path, ds, target = exported(tmp_path, "constant")
    with np.load(target / bundle.CHUNKS) as z:
        ranges = z[SOURCE_RANGES]
        assert z[SOURCE_DIGESTS].shape == (len(ranges), 32)
    assert ranges[-1, 1] == os.path.getsize(path)
    bundle.read_description(target)
    os.unlink(path)
    assert opened(target).getFingerprint() == ds.getFingerprint()
