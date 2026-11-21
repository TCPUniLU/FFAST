"""The parsed-cache features used together (ADRs 0107, 0108, 0110, 0111, 0112).

Each feature has its own tests. These cover what happens when two meet:
eviction during an export, a partly evicted cache that is exported or
verified, a repair of a pinned dataset, pins against the keep-free rule, and
older copies of a file that has changed.
"""
import gc
import os
import shutil

import pytest

from ffast.io import cache_space
from ffast.readers import bundle, text, verify
from tests.ffast.readers.test_bundle_open import opened, original
from tests.ffast.readers.test_scan_naming import folder, forget_ram, scan  # noqa: F401
from tests.ffast.readers.test_verify import batch_files, by_part, damage, scanned, write_xyz

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")


def released(path):
    """Scan ``path``, let go of the store, and return its identity."""
    return scanned(path)


def evict_some_of(root, identity, files=2):
    """Evict just enough that ``files`` of the dataset's per-atom batches go
    (eviction deletes them largest first). Returns the deleted files."""
    batches = batch_files(root / "batches", identity)
    assert len(batches) > files
    sizes = sorted((p.stat().st_size for p in batches), reverse=True)
    used = cache_space.used(root)
    cache_space.make_room(root, sum(sizes[:files]), used)
    gone = [p for p in batches if not p.exists()]
    assert 0 < len(gone) < len(batches)
    return gone


def test_an_export_of_a_partly_evicted_cache_is_complete_and_keeps_the_fingerprint(folder, tmp_path):  # noqa: F811
    root = folder.parent
    path = write_xyz(tmp_path / "a.xyz")
    ds = original(path, "constant")
    fingerprint = ds.getFingerprint()
    del ds
    gc.collect()
    identity = released(path)                            # the same copy, found again
    evict_some_of(root, identity)

    target = tmp_path / "out" / "a.ffast"
    bundle.export(path, target)
    assert opened(target).getFingerprint() == fingerprint
    report = verify.verify(target / bundle.DESCRIPTION)
    assert report.exit_code == 0, [(p.name, p.status, p.detail) for p in report.parts]


def test_an_eviction_during_an_export_leaves_the_exported_dataset_alone(folder, tmp_path, monkeypatch):  # noqa: F811
    root = folder.parent
    path = write_xyz(tmp_path / "a.xyz")
    other = write_xyz(tmp_path / "b.xyz")
    identity = released(path)
    other_identity = released(other)
    mine = {p.name for p in folder.iterdir() if p.name.startswith(identity)}

    # Another process needs all the room while the export copies batches.
    real_copy = shutil.copyfile
    calls = []

    def copy_and_evict(src, dst, *args, **kwargs):
        if not calls:
            cache_space.make_room(root, 1, 1)
        calls.append(src)
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copyfile", copy_and_evict)
    target = tmp_path / "out" / "a.ffast"
    bundle.export(path, target)
    monkeypatch.setattr(shutil, "copyfile", real_copy)

    assert calls, "the export copied no cached batch, so nothing was tested"
    assert not batch_files(folder, other_identity)          # the eviction did run
    assert mine <= {p.name for p in folder.iterdir()}        # the open dataset kept every file
    assert verify.verify(target / bundle.DESCRIPTION).exit_code == 0


def test_a_partly_evicted_cache_verifies_and_nothing_is_deleted(folder, tmp_path):  # noqa: F811
    root = folder.parent
    path = write_xyz(tmp_path / "a.xyz")
    identity = released(path)
    gone = evict_some_of(root, identity)
    left = {p.name for p in folder.iterdir()}

    report = verify.verify(path, root=root)
    parts = by_part(report)
    assert report.exit_code == 0, [(p.name, p.status, p.detail) for p in report.parts]
    assert parts["batches"].status == verify.VERIFIED
    assert f"{len(gone)} batch files are not cached" in parts["not cached"].detail
    assert not report.changed and {p.name for p in folder.iterdir()} == left


def test_a_repair_of_a_pinned_dataset_keeps_the_pin_and_the_next_load_heals_it(folder, tmp_path):  # noqa: F811
    root = folder.parent
    path = write_xyz(tmp_path / "a.xyz")
    identity = released(path)
    cache_space.write_pins(root, [os.path.realpath(path)])
    victim = batch_files(folder, identity)[1]
    damage(victim)

    report = verify.verify(path, root=root)
    assert report.exit_code == 1 and str(victim) in report.changed and not victim.exists()
    assert cache_space.read_pins(root) == [os.path.realpath(path)]

    store = scan(path)
    store.column("forces:forces")                       # reads every frame: the gap is parsed again
    assert store.identity == identity
    del store
    gc.collect()
    assert victim.exists()
    assert verify.verify(path, root=root).exit_code == 0


def test_a_pinned_dataset_stays_when_the_keep_free_rule_leaves_no_room(folder, tmp_path):  # noqa: F811
    from ffast.io import budget as resources

    root = folder.parent
    pinned, other = write_xyz(tmp_path / "a.xyz"), write_xyz(tmp_path / "b.xyz")
    pinned_identity, other_identity = released(pinned), released(other)
    cache_space.write_pins(root, [os.path.realpath(pinned)])
    before = {p.name for p in folder.iterdir() if p.name.startswith(pinned_identity)}

    # The disk is already down to the keep-free amount: no growth is allowed.
    owner = resources.budget()
    owner.keep_free = 10 * 2**30
    limit = cache_space.allowance(root, owner.keep_free)
    assert limit == cache_space.used(root)
    # Asking for more than the unpinned dataset holds: it goes, and still the
    # need does not fit, because the pinned one may not give way.
    other_bytes = {d.path: d.bytes for d in cache_space.datasets(root)}[os.path.realpath(other)]
    assert not cache_space.make_room(root, other_bytes + 1, limit)

    assert not batch_files(folder, other_identity)
    assert before <= {p.name for p in folder.iterdir()}


def grow(path):
    """Append one frame: the file is now in another state, so a new copy."""
    with open(path, "a") as f:
        f.write("1\nProperties=species:S:1:pos:R:3:forces:R:3 energy=1\nC 0 0 0 0 0 0\n")


def mine(folder, identity):
    return {p.name for p in folder.iterdir() if p.name.startswith(identity)}


@pytest.mark.parametrize("protect", ["pinned", "open"])
def test_an_older_copy_of_a_changed_file_can_be_evicted(folder, tmp_path, protect):  # noqa: F811
    """A pin or an open copy protects the file as it is now, not the copies of
    its older versions: a growing file would otherwise leave one copy per
    version that nothing can evict."""
    root = folder.parent
    path = write_xyz(tmp_path / "a.xyz")
    old_identity = released(path)
    grow(path)
    store = scan(path)
    new_identity = store.identity
    assert new_identity != old_identity
    if protect == "pinned":
        cache_space.write_pins(root, [os.path.realpath(path)])
        store = None
        gc.collect()
    current = mine(folder, new_identity)
    try:
        cache_space.make_room(root, 1, 1)
        assert not mine(folder, old_identity)
        assert current <= mine(folder, new_identity)
    finally:
        store = None
        gc.collect()


def test_an_older_copy_still_open_somewhere_is_not_evicted(folder, tmp_path):  # noqa: F811
    root = folder.parent
    path = write_xyz(tmp_path / "a.xyz")
    old_identity = released(path)
    grow(path)
    released(path)
    old = mine(folder, old_identity)
    holder = cache_space.hold_open(folder / f"{old_identity}{cache_space.OPEN}")
    try:
        cache_space.make_room(root, 1, 1)
        assert old <= mine(folder, old_identity)
    finally:
        holder.close()


def test_a_pinned_file_that_is_gone_keeps_every_copy(folder, tmp_path):  # noqa: F811
    """Without the file there is no telling which copy is current: keep them."""
    root = folder.parent
    path = write_xyz(tmp_path / "a.xyz")
    old_identity = released(path)
    grow(path)
    new_identity = released(path)
    cache_space.write_pins(root, [os.path.realpath(path)])
    before = mine(folder, old_identity) | mine(folder, new_identity)
    os.unlink(path)
    assert not cache_space.make_room(root, 1, 1)
    assert before <= mine(folder, old_identity) | mine(folder, new_identity)


def test_the_dataset_being_saved_does_not_protect_its_older_copies(folder, tmp_path):  # noqa: F811
    root = folder.parent
    path = write_xyz(tmp_path / "a.xyz")
    old_identity = released(path)
    grow(path)
    new_identity = released(path)
    current = mine(folder, new_identity)
    cache_space.make_room(root, 1, 1, saving=os.path.realpath(path))
    assert not mine(folder, old_identity)
    assert current <= mine(folder, new_identity)
