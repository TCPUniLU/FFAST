import numpy as np

from cluster.remote_dataset import CachedRemoteDataset
from datasetLoaders.loader import DatasetLoader, VariableDatasetLoader
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
