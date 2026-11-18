"""A prediction file gets one fingerprint however it is read (ADR 0107).

The ghost model of a prediction file is named by the chunked digests of its
energies and forces. Read as full arrays or in memory-saving batches, through
the whole Loading Coordinator path, the file must give the same name, and that
name must equal the one computed from the plain values. The file is large
enough that its forces span several 16,384-row chunks and several scan windows,
so chunks cross batch boundaries.
"""
import numpy as np
import pytest

from ffast.cache.fingerprint import CHUNK_ROWS, fingerprintFromArraysAndStrings
from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.readers import chunks, text
from ffast.readers.indexed import IndexedArrays
from tests.ffast.readers.test_scan_naming import folder, forget_ram, write_xyz  # noqa: F401
from tests.ffast.test_loading_coordinator import _Datasets, _FakeDataset, _file_env

pytestmark = pytest.mark.skipif(not text.fast_parser_usable(), reason="Optional fast parser unavailable")

N, ATOMS = 3000, 40


def plain_values(stride):
    """The values ``write_xyz`` writes, built without any reader."""
    frames = np.arange(0, N, stride)
    energies = frames.astype(np.float64)
    forces = np.tile(np.array([0.1, 0.2, 0.3]), (len(frames) * ATOMS, 1))
    return energies, forces


def load_ghost(path, stride, mode, monkeypatch):
    """The ghost fingerprint the coordinator gives ``path``, read in ``mode``,
    and the kind of arrays the reader handed back."""
    seen = []
    real = chunks.read_arrays

    def forced(*args, **kwargs):
        loaded = real(*args, **{**kwargs, "memory_mode": mode})
        seen.append(type(loaded.arrays))
        return loaded

    monkeypatch.setattr(chunks, "read_arrays", forced)
    forget_ram()
    n_kept = len(range(0, N, stride))
    env = _file_env(_FakeDataset(np.zeros(n_kept)))
    env.datasets = _Datasets(env.datasets.get("ds-fp"), slice_num=stride)
    LoadingCoordinator(env).loadPrepredictedDataset(path, "ds-fp", reader="xyz")
    monkeypatch.setattr(chunks, "read_arrays", real)
    (ghost,) = env.objects.registered
    return ghost, seen


@pytest.mark.parametrize("stride", [1, 3])
def test_a_prediction_file_has_one_fingerprint_in_full_and_memory_saving_reads(
        folder, tmp_path, monkeypatch, stride):
    path = write_xyz(tmp_path / "pred.xyz", n=N, atoms=ATOMS)
    assert len(range(0, N, stride)) * ATOMS > 2 * CHUNK_ROWS

    full, full_kind = load_ghost(path, stride, "auto", monkeypatch)
    saving, saving_kind = load_ghost(path, stride, "saving", monkeypatch)

    assert full_kind == [dict] and saving_kind == [IndexedArrays]
    assert full == saving == fingerprintFromArraysAndStrings(*plain_values(stride))
