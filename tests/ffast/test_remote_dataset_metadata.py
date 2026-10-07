import numpy as np

from cluster.remote_dataset import CachedRemoteDataset
from ffast.loaders.dataset import (
    AtomFilteredDataset, DatasetLoader, SubDataset, VariableDatasetLoader,
)
from ffast.protocol import DatasetMeta


class _UniformDataset(DatasetLoader):
    datasetName = "fake uniform"

    def getN(self):
        return 2

    def getForces(self):
        return np.zeros((2, 3, 3))

    def getElements(self):
        return np.array([8, 1, 1])


class _VariableDataset(VariableDatasetLoader):
    datasetName = "fake variable"

    def getForces(self):
        return [np.zeros((2, 3)), np.zeros((3, 3))]


def _make_uniform():
    dataset = _UniformDataset.__new__(_UniformDataset)
    dataset.name = "water"
    dataset.path = "/data/water.xyz"
    return dataset


def _make_variable():
    dataset = _VariableDataset.__new__(_VariableDataset)
    dataset.name = "mixed"
    dataset.path = "/data/mixed.xyz"
    dataset.N = 2
    dataset.z_flat = np.array([6, 1, 8, 1, 1])
    dataset.molecule_offsets = np.array([0, 2, 5])
    return dataset


def test_legacy_uniform_metadata_carries_elements_for_remote_proxy():
    meta = DatasetMeta.model_validate(_make_uniform().toMetaDict())

    assert meta.variable is False
    assert meta.elements == [8, 1, 1]
    assert meta.offsets is None
    assert meta.path == "/data/water.xyz"
    assert meta.source_type == "fake uniform"

    proxy = CachedRemoteDataset("fp", meta.name, meta.n)
    proxy.apply_metadata(
        elements=meta.elements, offsets=meta.offsets, is_variable=meta.variable
    )

    assert np.array_equal(proxy.getElements(), np.array([8, 1, 1]))
    assert proxy.getNAtoms() == 3


def test_legacy_variable_metadata_carries_flat_elements_and_offsets():
    meta = DatasetMeta.model_validate(_make_variable().toMetaDict())

    assert meta.variable is True
    assert meta.elements == [6, 1, 8, 1, 1]
    assert meta.offsets == [0, 2, 5]
    assert meta.path == "/data/mixed.xyz"
    assert meta.source_type == "fake variable"

    proxy = CachedRemoteDataset("fp", meta.name, meta.n)
    proxy.apply_metadata(
        elements=meta.elements, offsets=meta.offsets, is_variable=meta.variable
    )

    assert np.array_equal(proxy.getElements(), np.array([6, 1, 8, 1, 1]))
    assert np.array_equal(proxy.getElements(1), np.array([8, 1, 1]))
    assert np.array_equal(proxy.getNAtoms(), np.array([2, 3]))


# ── lineage: which configurations a subset shares with its parent (ADR 0056) ──
# The browser matches a frame of one dataset to the same configuration in a
# related one (a subset and its parent), so the announcement names the parent
# and, for a frame subset, which parent frame each of its frames is.

def _parent():
    parent = _make_uniform()
    parent.fingerprint = "parentfp"
    parent.loaded = True
    parent.bondSizes = None
    parent.getName = lambda: "water"
    return parent


def test_a_loaded_dataset_has_no_parent():
    meta = DatasetMeta.model_validate(_make_uniform().toMetaDict())
    assert meta.parent is None and meta.parent_frames is None


def test_a_frame_subset_names_its_parent_and_parent_frames():
    sub = SubDataset(_parent(), None, np.array([3, 5, 8]), "picked")
    sub.getN = lambda: 3
    meta = DatasetMeta.model_validate(sub.toMetaDict())
    assert meta.parent == "parentfp"
    assert meta.parent_frames == [3, 5, 8]


def test_an_atom_subset_shares_every_frame_with_its_parent():
    """Its indices are atoms, not frames: frame i is the parent's frame i."""
    sub = AtomFilteredDataset(_parent(), np.array([0, 2]))
    meta = DatasetMeta.model_validate(sub.toMetaDict())
    assert meta.parent == "parentfp"
    assert meta.parent_frames is None


def test_the_desktop_accepts_lineage_in_the_announcement(monkeypatch):
    """The desktop's handler names every field it takes; new ones must not
    break it."""
    from ffast.core.connection_manager import ConnectionManager

    seen = {}
    manager = ConnectionManager.__new__(ConnectionManager)
    manager.getDataset = lambda fp: seen.setdefault("fp", fp) and None
    monkeypatch.setattr("cluster.remote_dataset.CachedRemoteDataset.__init__",
                        lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
    try:
        manager._onRemoteDatasetMeta("subfp", name="s", n=3, is_sub=True,
                                     parent="parentfp", parent_frames=[3, 5, 8])
    except RuntimeError as exc:
        assert str(exc) == "stop"   # got past the signature and validation
    assert seen["fp"] == "subfp"
