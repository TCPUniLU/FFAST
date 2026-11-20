"""The strict check proves source and cache against stored digests (ADR 0111, step 3)."""
import gc
import os
import shutil

import numpy as np
import pytest

from ffast.cache import source_digest as sd
from ffast.io import cache_space
from ffast.readers import bundle, text, verify
from ffast.readers.indexed import CHUNKS_SUFFIX
from tests.ffast.readers.test_bundle_open import exported, opened  # noqa: F401
from tests.ffast.readers.test_scan_naming import folder, forget_ram, scan, write_xyz  # noqa: F401
from tests.ffast.readers.test_scan_record import cancel_after

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")


@pytest.fixture(autouse=True)
def finder_with_digests():
    import ffast_xyz
    if getattr(ffast_xyz, "SOURCE_SCHEME", None) != sd.SCHEME:
        pytest.skip("the installed ffast-xyz predates source digests")


def scanned(path):
    """Scan ``path`` and let go of the store, which holds the cache open."""
    store = scan(path)
    identity = store.identity
    del store
    gc.collect()
    return identity


def by_part(report):
    return {p.name: p for p in report.parts}


def batch_files(folder, identity):
    return sorted(folder.glob(f"{identity}-[0-9]*-[0-9]*.npz"), key=lambda p: int(p.stem.split("-")[-2]))


def damage(file, key_prefix="forces", delta=0.5):
    """Rewrite a batch with one value of its forces changed."""
    with np.load(file) as z:
        data = {k: z[k] for k in z.files}
    key = next(k for k in data if k.startswith(key_prefix))
    data[key] = data[key].copy()
    data[key].flat[3] += delta
    np.savez(file, **data)
    return key


def snapshot(folder):
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in folder.rglob("*"))


def test_a_clean_cache_verifies(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    scanned(path)
    report = verify.verify(path, root=folder.parent)
    parts = by_part(report)
    assert report.exit_code == 0 and report.kind == "cache" and not report.changed
    assert parts["source"].status == parts["batches"].status == verify.VERIFIED


def test_a_damaged_batch_is_named_deleted_and_parsed_again(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    identity = scanned(path)
    files = batch_files(folder, identity)
    original = None
    from ffast.readers.xyz_reader import XyzReader
    from ffast.readers import chunks
    original = np.array(chunks.read_arrays(XyzReader, path, workers=1, use_cache=False).arrays["forces:forces"])
    victim = files[1]
    key = damage(victim)
    report = verify.verify(path, root=folder.parent)
    parts = by_part(report)
    assert report.exit_code == 1
    assert parts["source"].status == verify.VERIFIED and parts["batches"].status == verify.DIFFERS
    assert victim.name in parts["batches"].detail and "forces" in parts["batches"].detail
    assert not victim.exists() and str(victim) in report.changed
    assert files[0].exists() or len(report.changed) > 1
    # A later load reads the original values, and a second check is clean.
    from ffast.readers.indexed import IndexedStore
    store = scan(path)
    values = np.asarray(store.column("forces:forces"))
    np.testing.assert_array_equal(values, original)
    del store
    gc.collect()
    again = verify.verify(path, root=folder.parent)
    assert again.exit_code == 0, [(p.name, p.status, p.detail) for p in again.parts]


def test_an_unreadable_batch_counts_as_a_difference(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    identity = scanned(path)
    victim = batch_files(folder, identity)[2]
    victim.write_bytes(victim.read_bytes()[:100])
    report = verify.verify(path, root=folder.parent)
    assert report.exit_code == 1
    assert victim.name in by_part(report)["batches"].detail and not victim.exists()


def test_a_changed_source_with_size_and_time_restored_deletes_the_whole_cache_but_not_the_pin(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    identity = scanned(path)
    st = os.stat(path)
    root = folder.parent
    cache_space.write_pins(root, [os.path.realpath(path)])
    with open(path, "r+b") as f:                # same inode, same size
        f.seek(st.st_size // 2)
        byte = f.read(1)
        f.seek(st.st_size // 2)
        f.write(b"7" if byte != b"7" else b"8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert (os.stat(path).st_size, os.stat(path).st_mtime_ns) == (st.st_size, st.st_mtime_ns)
    report = verify.verify(path, root=root)
    assert report.exit_code == 1 and by_part(report)["source"].status == verify.DIFFERS
    assert "differ" in by_part(report)["source"].detail
    assert not list(folder.glob(f"{identity}*"))
    assert cache_space.read_pins(root) == [os.path.realpath(path)]
    # The next check has nothing to compare with.
    assert verify.verify(path, root=root).exit_code == 2


def test_a_bundle_with_a_damaged_batch_is_reported_and_left_alone(folder, tmp_path):
    path, ds, target = exported(tmp_path, "constant")
    del ds
    gc.collect()
    victim = sorted((target / bundle.BATCHES).glob("*.npz"))[1]
    damage(victim)
    # The same size, so the fast check passes: write the bytes back at that length.
    before = snapshot(target)
    report = verify.verify(target / bundle.DESCRIPTION)
    assert report.kind == "bundle" and report.exit_code == 1
    assert "fast check" not in by_part(report)          # the size is right: only the digests see it
    assert victim.name in by_part(report)["batches"].detail
    assert by_part(report)["source"].status == verify.VERIFIED
    assert not report.changed
    assert snapshot(target) == before


def test_a_bundle_with_no_source_says_where_it_looked(folder, tmp_path):
    path, ds, target = exported(tmp_path, "constant")
    del ds
    os.unlink(path)
    report = verify.verify(target)
    parts = by_part(report)
    assert parts["source"].status == verify.NOT_CHECKED
    assert str(tmp_path / "a.xyz") in parts["source"].detail
    assert str(tmp_path / "a.xyz") in parts["source"].detail and "not found" in parts["source"].detail
    assert parts["batches"].status == verify.VERIFIED and report.exit_code == 0


def test_a_cache_without_source_digests_still_checks_its_batches(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    identity = scanned(path)
    sidecar = folder / f"{identity}{CHUNKS_SUFFIX}"
    with np.load(sidecar) as z:
        kept = {k: z[k] for k in z.files if not k.startswith("source|")}
    np.savez(sidecar, **kept)
    report = verify.verify(path, root=folder.parent)
    parts = by_part(report)
    assert parts["source"].status == verify.NOT_CHECKED and "not recorded" in parts["source"].detail
    assert parts["batches"].status == verify.VERIFIED and report.exit_code == 0


def test_columns_the_cache_dropped_are_listed_as_not_cached(folder, tmp_path):
    from ffast.io import budget as resources
    resources.budget().parsed_limit = 1_000_000         # room for the per-frame values only
    path = write_xyz(tmp_path / "a.xyz")
    scanned(path)
    report = verify.verify(path, root=folder.parent)
    parts = by_part(report)
    assert "not cached" in parts, [(p.name, p.detail) for p in report.parts]
    assert "forces" in parts["not cached"].detail and "covered by the source part" in parts["not cached"].detail
    assert parts["batches"].status == verify.VERIFIED and report.exit_code == 0


def test_a_dataset_open_in_another_process_is_not_touched(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    identity = scanned(path)
    victim = batch_files(folder, identity)[0]
    damage(victim)
    holder = cache_space.hold_open(folder / f"{identity}{cache_space.OPEN}")
    try:
        report = verify.verify(path, root=folder.parent)
    finally:
        holder.close()
    assert report.exit_code == 1 and victim.exists() and not report.changed
    assert "in use by another FFAST" in by_part(report)["repair"].detail


def test_an_unfinished_scan_has_nothing_to_check(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    cancel_after(path, 2)
    gc.collect()
    report = verify.verify(path, root=folder.parent)
    assert report.exit_code == 2
    assert "not finished" in by_part(report)["cache"].detail


def test_a_file_never_loaded_has_nothing_to_check(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    report = verify.verify(path, root=folder.parent)
    assert report.exit_code == 2 and "no finished cache" in by_part(report)["cache"].detail


def test_the_name_verify_looks_for_is_the_name_the_scan_gave(folder, tmp_path):
    from ffast.readers.indexed import IndexedStore, file_state
    from ffast.readers.xyz_reader import XyzReader
    path = write_xyz(tmp_path / "a.xyz")
    identity = scanned(path)
    assert IndexedStore.identity_of(XyzReader, os.path.abspath(path), file_state(path)) == identity


def test_a_changed_source_deletes_only_its_own_identity_not_other_copies_of_the_path(folder, tmp_path):
    path = write_xyz(tmp_path / "a.xyz")
    identity = scanned(path)
    other = "ab" * 32
    (folder / f"{other}.json").write_text("{}")
    (folder / f"{other}.source.json").write_text('{"path": "%s"}' % os.path.realpath(path))
    with open(path, "r+b") as f:
        st = os.fstat(f.fileno())
        f.seek(100)
        f.write(b"9" if f.read(1) != b"9" else b"8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert verify.verify(path, root=folder.parent).exit_code == 1
    assert not list(folder.glob(f"{identity}*")) and (folder / f"{other}.json").exists()


def test_a_deleted_batch_is_written_again_when_its_frames_are_read_and_the_next_check_is_clean(folder, tmp_path):
    from ffast.readers.xyz_reader import XyzReader
    path = write_xyz(tmp_path / "a.xyz")
    identity = scanned(path)
    victim = batch_files(folder, identity)[1]
    before = sorted(p.name for p in batch_files(folder, identity))
    damage(victim)
    report = verify.verify(path, root=folder.parent)
    assert report.exit_code == 1 and not victim.exists()
    deleted = [os.path.basename(f) for f in report.changed if f.endswith(".npz")]
    assert victim.name in deleted
    # A load from the manifest (no scan) reads those frames from the source and keeps them.
    arrays, *_ = text.read_level2(XyzReader, path, memory_mode="saving", workers=1)
    store = arrays.store
    forget_ram()                                  # the scan left the parsed windows in memory
    for name in deleted:
        a, b = (int(x) for x in name[:-4].split("-")[-2:])
        assert not (folder / name).exists()
        part = store._part(a, b)                  # what a bulk read of those frames does
        assert part["forces:forces"].shape[0] == int(store.offsets[b] - store.offsets[a])
        assert (folder / name).exists()
    assert sorted(p.name for p in batch_files(folder, identity)) == before
    del store, arrays
    gc.collect()
    again = verify.verify(path, root=folder.parent)
    assert again.exit_code == 0 and not again.changed, [(p.name, p.status, p.detail) for p in again.parts]


def test_a_batch_written_again_keeps_the_level_of_its_siblings(folder, tmp_path):
    from ffast.io import budget as resources
    from ffast.readers.xyz_reader import XyzReader
    resources.budget().parsed_limit = 1_000_000           # the per-frame values only
    path = write_xyz(tmp_path / "a.xyz")
    identity = scanned(path)
    files = batch_files(folder, identity)
    assert len(files) >= 3
    with np.load(files[0]) as z:
        assert "__omitted__" in z.files
    gone = files[1]
    gone.unlink()
    arrays, *_ = text.read_level2(XyzReader, path, memory_mode="saving", workers=1)
    forget_ram()
    a, b = (int(x) for x in gone.stem.split("-")[-2:])
    arrays.store._part(a, b)
    assert gone.exists()
    with np.load(gone) as healed, np.load(files[0]) as sibling:
        assert sorted(healed.files) == sorted(sibling.files)
        assert healed["__omitted__"].tolist() == sibling["__omitted__"].tolist()
