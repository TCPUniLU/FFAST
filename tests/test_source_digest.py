"""The digest of a byte range of a source file (ADR 0111, step 1): one
recipe, computed the same way by Python and by the compiled frame finder."""
import hashlib

import numpy as np
import pytest

from ffast.cache import source_digest as sd
from ffast.io import frame_index
from ffast.readers import text

TAG = sd.SCHEME.encode()


def by_hand(data):
    outer = hashlib.sha256(TAG)
    for at in range(0, len(data), sd.PIECE):
        outer.update(hashlib.sha256(data[at:at + sd.PIECE]).digest())
    return outer.digest()


@pytest.mark.parametrize("size", [0, 3, sd.PIECE, sd.PIECE + 1])
def test_range_digest_follows_the_recipe(size):
    data = (b"abc" * (size // 3 + 1))[:size]
    pieces = [hashlib.sha256(data[i:i + sd.PIECE]).digest() for i in range(0, size, sd.PIECE)]
    expected = hashlib.sha256(TAG + b"".join(pieces)).digest()
    assert sd.range_digest(data) == expected == by_hand(data)
    assert sd.range_digest(memoryview(data)) == expected
    assert sd.SCHEME == "ffast-bytes-v1-sha256-8M"


def test_an_empty_range_hashes_only_the_tag():
    assert sd.range_digest(b"") == hashlib.sha256(TAG).digest()


def test_file_range_digests_equal_the_digest_of_the_same_slices(tmp_path):
    rng = np.random.default_rng(1)
    data = rng.integers(0, 256, 3 * sd.PIECE + 12345, dtype=np.uint8).tobytes()
    path = tmp_path / "f.bin"
    path.write_bytes(data)
    ranges = [(0, 10), (10, sd.PIECE + 5),                  # starts mid-piece of nothing, spans one piece
              (sd.PIECE + 5, sd.PIECE + 5),                 # empty
              (sd.PIECE + 7, len(data))]                    # many pieces, ends short
    got = sd.file_range_digests(path, ranges, threads=3)
    assert got == [sd.range_digest(data[a:b]) for a, b in ranges]
    assert sd.file_range_digests(path, [(0, len(data))], threads=1) == [sd.range_digest(data)]


def write_xyz(path, frames=2500, atoms=30):
    rng = np.random.default_rng(0)
    with open(path, "w") as f:
        for i in range(frames):
            f.write(f'{atoms}\nProperties=species:S:1:pos:R:3 energy={i}.5\n')
            xyz = rng.normal(size=(atoms, 3))
            f.writelines(f"H {x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in xyz)
    return path


needs_fast = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")


@needs_fast
def test_the_scan_gives_each_window_the_digest_of_its_bytes(tmp_path):
    import ffast_xyz
    if getattr(ffast_xyz, "SOURCE_SCHEME", None) != sd.SCHEME:
        pytest.skip("the installed ffast-xyz predates source digests")
    path = write_xyz(tmp_path / "a.xyz")
    data = path.read_bytes()
    seen = []
    frame_index.scan(str(path), on_batch=lambda s, c, end, digest=None, window=None:
                     seen.append((window, sd.resolve(digest))), batch_bytes=200_000)
    assert len(seen) > 5
    assert seen[0][0][0] == 0 and seen[-1][0][1] == len(data)
    for (a, b), digest in seen:
        assert digest == sd.range_digest(data[a:b])
    assert all(x[0][1] == y[0][0] for x, y in zip(seen, seen[1:]))     # no gap, no overlap


@needs_fast
def test_a_digest_spans_many_pieces_the_same_in_rust_and_python(tmp_path):
    import ffast_xyz
    if getattr(ffast_xyz, "SOURCE_SCHEME", None) != sd.SCHEME:
        pytest.skip("the installed ffast-xyz predates source digests")
    path = write_xyz(tmp_path / "big.xyz", frames=9000, atoms=60)      # about 20 MB: three pieces
    data = path.read_bytes()
    assert len(data) > 2 * sd.PIECE
    s, c, end, state, digest = ffast_xyz.find_frames(str(path), 0, len(data), 4)
    assert state == ffast_xyz.FIND_END and end == len(data)
    assert sd.resolve(digest) == sd.range_digest(data[:end])


@needs_fast
def test_an_old_four_value_finder_gives_no_digest_and_the_scan_still_works(tmp_path, monkeypatch):
    import ffast_xyz
    path = write_xyz(tmp_path / "old.xyz", frames=300)
    real = ffast_xyz.find_frames
    monkeypatch.setattr(ffast_xyz, "find_frames",
                        lambda *a, **k: real(*a, **k)[:4])
    digests = []
    index = frame_index.scan(str(path), on_batch=lambda s, c, end, digest=None, window=None:
                             digests.append(digest), batch_bytes=50_000)
    assert digests and all(d is None for d in digests)
    assert len(index) == 300


@needs_fast
def test_a_finder_with_another_scheme_gives_no_digest(tmp_path, monkeypatch):
    import ffast_xyz
    path = write_xyz(tmp_path / "other.xyz", frames=100)
    monkeypatch.setattr(ffast_xyz, "SOURCE_SCHEME", "something-else", raising=False)
    digests = []
    frame_index.scan(str(path), on_batch=lambda s, c, end, digest=None, window=None:
                     digests.append(digest))
    assert digests == [None]


# -- at most one window waits for its hash, and its buffer is counted (ADR 0111, A2) --------

class SpyJob:
    """The finder's digest job, noting when somebody waited for it."""

    def __init__(self, job):
        self.job, self.called = job, False

    def result(self):
        self.called = True
        return self.job.result()


@pytest.fixture
def spied(monkeypatch, tmp_path):
    import ffast_xyz
    from ffast.io import budget as resources
    if getattr(ffast_xyz, "SOURCE_SCHEME", None) != sd.SCHEME:
        pytest.skip("the installed ffast-xyz predates source digests")
    owner = resources.Budget(ram_bytes=512 * 1024, parsed_bytes=2**30, result_bytes=8 * 2**20)
    owner.limit = 256 * 2**20
    monkeypatch.setattr(resources, "_budget", owner)
    real, jobs = ffast_xyz.find_frames, []

    def spy(*args, **kwargs):
        s, c, end, state, job = real(*args, **kwargs)
        jobs.append(SpyJob(job))
        return s, c, end, state, jobs[-1]

    monkeypatch.setattr(ffast_xyz, "find_frames", spy)
    return owner, jobs


@needs_fast
def test_a_window_waits_for_the_hash_of_the_one_before_it(tmp_path, spied, monkeypatch):
    owner, jobs = spied
    path = write_xyz(tmp_path / "a.xyz")
    waited = []
    import ffast_xyz
    real = ffast_xyz.find_frames

    def checking(*args, **kwargs):
        waited.append(jobs[-1].called if jobs else True)     # the previous window's job
        return real(*args, **kwargs)

    monkeypatch.setattr(ffast_xyz, "find_frames", checking)
    frame_index.scan(str(path), on_batch=lambda *a, **k: None, batch_bytes=200_000)
    assert len(waited) > 5 and all(waited)


@needs_fast
def test_the_budget_counts_the_buffer_while_its_hash_is_pending_and_frees_it_after(tmp_path, spied):
    owner, jobs = spied
    path = write_xyz(tmp_path / "a.xyz")
    base = owner.used
    seen = []

    def on_batch(s, c, end, digest=None, window=None):
        seen.append(owner.used - base)                      # the hash has not been collected
        digest.result()
        seen.append(owner.used - base)

    frame_index.scan(str(path), on_batch=on_batch, batch_bytes=200_000)
    pending, collected = seen[0::2], seen[1::2]
    assert all(p >= 200_000 for p in pending)               # the window's buffer is counted
    assert all(c == 0 for c in collected)                   # and given back when the hash is read
    assert owner.used == base and owner.pending == 0


@needs_fast
def test_a_cancel_gives_the_buffer_back(tmp_path, spied):
    owner, jobs = spied
    path = write_xyz(tmp_path / "a.xyz")
    base = owner.used

    def on_batch(s, c, end, digest=None, window=None):
        assert owner.used - base >= 200_000
        raise frame_index.FrameIndexCancelled(path)

    with pytest.raises(frame_index.FrameIndexCancelled):
        frame_index.scan(str(path), on_batch=on_batch, batch_bytes=200_000)
    assert owner.used == base and owner.pending == 0


@needs_fast
def test_a_scan_nobody_watches_does_not_hash(tmp_path, monkeypatch):
    import ffast_xyz
    path = write_xyz(tmp_path / "plain.xyz", frames=200)
    seen = []
    real = ffast_xyz.find_frames
    monkeypatch.setattr(ffast_xyz, "find_frames", lambda *a, **k: seen.append(a) or real(*a, **k))
    if getattr(ffast_xyz, "SOURCE_SCHEME", None) != sd.SCHEME:
        pytest.skip("the installed ffast-xyz predates source digests")
    frame_index.scan(str(path))
    assert seen and all(len(a) == 5 and a[4] is False for a in seen)


@needs_fast
def test_two_threads_may_wait_for_one_digest(tmp_path):
    import contextlib
    import threading

    import ffast_xyz
    if getattr(ffast_xyz, "SOURCE_SCHEME", None) != sd.SCHEME:
        pytest.skip("the installed ffast-xyz predates source digests")
    path = write_xyz(tmp_path / "two.xyz", frames=9000, atoms=60)
    size = path.stat().st_size
    *_, job = ffast_xyz.find_frames(str(path), 0, size, 4)
    held = frame_index.HeldDigest(job, contextlib.ExitStack())
    out = []
    threads = [threading.Thread(target=lambda: out.append(held.result())) for _ in range(4)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert len(out) == 4 and len(set(out)) == 1
    assert out[0] == sd.range_digest(path.read_bytes())
