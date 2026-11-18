"""save_array must never damage the file an open array reads from."""

from pathlib import Path

import numpy as np
import pytest

from ffast.io.batched import BatchedArray
from ffast.io.result_array import open_array, save_array


def _saved(tmp_path, name="a.npy"):
    values = np.arange(60, dtype="<f8").reshape(20, 3)
    path = tmp_path / name
    np.save(path, values)
    return path, values


def test_saving_an_opened_array_onto_its_own_path_keeps_values(tmp_path):
    path, values = _saved(tmp_path)
    lazy = open_array(path)
    save_array(path, lazy)
    np.testing.assert_array_equal(np.asarray(open_array(path)), values)
    np.testing.assert_array_equal(np.asarray(lazy), values)
    assert [p.name for p in tmp_path.iterdir()] == ["a.npy"]


def test_failed_save_keeps_old_file_and_leaves_no_temp(tmp_path):
    path, values = _saved(tmp_path)
    def read(a, b):
        raise RuntimeError("boom")

    bad = BatchedArray((20, 3), np.dtype("<f8"), read)
    with pytest.raises(RuntimeError):
        save_array(path, bad)
    np.testing.assert_array_equal(np.load(path), values)
    assert [p.name for p in tmp_path.iterdir()] == ["a.npy"]


def test_open_array_refuses_a_truncated_file(tmp_path):
    path, _ = _saved(tmp_path)
    data = path.read_bytes()
    path.write_bytes(data[:-8])
    with pytest.raises(ValueError, match="truncated|size"):
        open_array(path)


def test_a_saved_array_gets_ordinary_file_permissions(tmp_path):
    """The temporary file is owner-only; the saved file must not stay so."""
    import os
    import numpy as np
    from ffast.io.result_array import save_array

    umask = os.umask(0o022)
    try:
        path = tmp_path / "a.npy"
        save_array(path, np.arange(6.0).reshape(3, 2))
        assert path.stat().st_mode & 0o777 == 0o644
    finally:
        os.umask(umask)
