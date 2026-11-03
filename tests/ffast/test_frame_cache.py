"""A file's arrays are kept on disk and opened by the next load (ADR 0083)."""
import os

import numpy as np
import pytest

from ffast.core.data_types import AtomsList
from ffast.io import extxyz_arrays as xa
from ffast.io import frame_cache
from ffast.io import frame_index as fi
from ffast.loaders.ase import aseDatasetLoader
from tests.ffast.read_once_fixtures import FILES


@pytest.fixture
def reads(monkeypatch):
    """Every read_frames call: a load that opens the cache makes none."""
    calls = []
    real = xa.read_frames
    monkeypatch.setattr(xa, "read_frames", lambda index, **kw: calls.append(index) or real(index, **kw))
    return calls


def _write(tmp_path, name="columns_change.extxyz"):
    p = tmp_path / name
    p.write_text(FILES[name])
    return str(p)


def _load(path, step=1):
    fi.clear_memory_cache()
    return aseDatasetLoader(path, atomsList=AtomsList(path, step=step))


def test_the_second_load_opens_the_kept_arrays(tmp_path, reads):
    path = _write(tmp_path)
    first = _load(path)
    second = _load(path)
    assert len(reads) == 1
    assert second.getFingerprint() == first.getFingerprint()
    assert np.array_equal(second.R_flat, first.R_flat)
    assert second.availableFieldKeys() == first.availableFieldKeys()
    assert np.array_equal(second.getAtomFieldMissing("spin"), first.getAtomFieldMissing("spin"))


def test_kept_arrays_are_read_only(tmp_path, reads):
    path = _write(tmp_path)
    _load(path)
    with pytest.raises(ValueError):
        _load(path).R_flat[0, 0] = 1.0


def test_a_changed_file_is_read_again(tmp_path, reads):
    path = _write(tmp_path)
    _load(path)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    _load(path)
    assert len(reads) == 2


def test_each_stride_is_kept_apart(tmp_path, reads):
    path = _write(tmp_path)
    assert _load(path).N == 3
    assert _load(path, step=2).N == 2
    assert _load(path, step=2).N == 2
    assert len(reads) == 2


def test_the_least_recently_used_entry_makes_room(tmp_path, monkeypatch, reads):
    a, b = _write(tmp_path), _write(tmp_path, "variable.extxyz")
    _load(a)
    kept_a = sum(f.stat().st_size for f in next(frame_cache._folder().iterdir()).iterdir())
    size_b = frame_cache._nbytes(xa.read_frames(fi.scan(b), workers=1))
    monkeypatch.setattr(frame_cache, "MAX_BYTES", kept_a + size_b - 1)   # one fits, not both
    _load(b)                                              # pushes a out
    _load(a)
    assert len(reads) == 4                                # a, b's size, b, a again
    assert len(list(frame_cache._folder().iterdir())) == 1


def test_a_broken_entry_is_read_again(tmp_path, reads):
    path = _write(tmp_path)
    _load(path)
    entry = next(frame_cache._folder().iterdir())
    (entry / "positions.npy").write_bytes(b"garbage")
    assert _load(path).N == 3
    assert len(reads) == 2


def test_a_failed_save_still_loads(tmp_path, monkeypatch, reads):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", blocker)   # mkdir under a file fails
    path = _write(tmp_path)
    assert _load(path).N == 3
    assert _load(path).N == 3
    assert len(reads) == 2
