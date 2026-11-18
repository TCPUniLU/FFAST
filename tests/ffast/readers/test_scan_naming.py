"""A full scan names its cache when it starts (ADR 0107, step 3)."""
import fcntl

import pytest

from ffast.io import budget as resources
from ffast.io import frame_cache, frame_index
from ffast.metrics import stop
from ffast.readers import text
from ffast.readers.indexed import IndexedStore
from ffast.readers.xyz_reader import XyzReader

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")


@pytest.fixture
def folder(monkeypatch, tmp_path):
    owner = resources.Budget(ram_bytes=512 * 1024, parsed_bytes=2**30, result_bytes=8 * 2**20)
    owner.limit = 16 * 2**20        # 1 MiB scan windows
    monkeypatch.setattr(resources, "_budget", owner)
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "parsed")
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    return tmp_path / "parsed" / "batches"


def write_xyz(path, n=3000, atoms=40):
    with open(path, "w") as f:
        for i in range(n):
            f.write(f"{atoms}\nProperties=species:S:1:pos:R:3:forces:R:3 energy={i}\n")
            for j in range(atoms):
                f.write(f"C {i}.125 {j}.25 -1.5 0.1 0.2 0.3\n")
    return str(path)


class Progress:
    """A progress callback that can act after each committed window."""

    def __init__(self, after=None):
        self.windows = 0
        self.after = after

    def __call__(self, done, total):
        pass

    def bytes_read(self, done, total, frames):
        self.windows += 1
        if self.after:
            self.after(self.windows)


def forget_ram():
    """Windows parsed by an earlier scan stay in the shared RAM cache under the
    same name (the cache identity is stable now); a test that counts parses
    must start with none."""
    owner = resources.budget()
    owner.cache.clear()
    owner.cache_bytes = 0


def scan(path, progress=None):
    forget_ram()
    return IndexedStore(XyzReader, path, XyzReader(path).contents(), [], [], [], [],
                        workers=1, scan_source=True, progress=progress)


def test_the_name_is_the_same_before_and_after_a_scan_and_across_two_scans(folder, tmp_path, monkeypatch):
    path = write_xyz(tmp_path / "a.xyz")
    names = []
    real = IndexedStore._scan_source

    def spy(self, progress):
        names.append(self.identity)
        return real(self, progress)

    monkeypatch.setattr(IndexedStore, "_scan_source", spy)
    first = scan(path)
    assert len(first.ranges) > 2
    assert names == [first.identity]
    # The manifest and every batch carry that name.
    assert (folder / f"{first.identity}.json").exists()
    assert all(p.name.startswith(first.identity) for p in folder.iterdir() if p.name != ".lock")
    second = scan(path)
    assert names == [first.identity, first.identity] and second.identity == first.identity


def test_a_cancelled_scan_keeps_its_committed_windows_and_no_temporary_files(folder, tmp_path):
    path = write_xyz(tmp_path / "b.xyz")
    cancelled = [False]
    token = stop.running_under(lambda: cancelled[0])
    try:
        with pytest.raises(stop.Stopped):
            scan(path, Progress(lambda windows: cancelled.__setitem__(0, windows >= 2)))
    finally:
        stop.reset(token)
    starts = sorted(int(p.name[:-4].split("-")[-2]) for p in folder.glob("*-*.npz"))
    assert len(starts) == 2 and starts[0] == 0
    assert not list(folder.glob("*.tmp*"))
    assert [p.name for p in folder.glob("*.lock")] == [".lock"]     # the folder's own, not the scan's
    assert [p.name for p in folder.glob("*.json") if not p.name.endswith(".progress.json")] == []


def test_a_scan_that_cannot_take_the_lock_loads_correctly_and_writes_no_batches(folder, tmp_path):
    path = write_xyz(tmp_path / "c.xyz")
    reference = scan(path)
    identity = reference.identity
    for p in folder.iterdir():
        p.unlink()
    held = open(folder / f"{identity}.lock", "a+b")
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        other = scan(path)
        assert other.identity == identity
        assert other.use_cache is False
        assert [p.name for p in folder.iterdir()] == [f"{identity}.lock"]     # only the holder's lock
        for key in reference.info:
            assert other.info[key]["digest"] == reference.info[key]["digest"]
        assert other.column("positions")[5].tolist() == reference.column("positions")[5].tolist()
    finally:
        held.close()
