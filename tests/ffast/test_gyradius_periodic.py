"""The radius of gyration warns on periodic data, where it measures the cell's
unwrapped coordinates, not a molecule (ported from dev b746f06)."""
import numpy as np
from ase import Atoms

import ffast.metrics.builtin  # noqa: F401 — registers the built-ins
from ffast.loaders.ase import aseDatasetLoader
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.input_resolver import InputResolver
from ffast.metrics.models import MetricResult
from ffast.metrics.registry import default_registry

_POSITIONS = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def _dataset(cell=None):
    frames = [Atoms("H2O", positions=_POSITIONS, cell=cell, pbc=cell is not None)
              for _ in range(2)]
    dataset = aseDatasetLoader("x.xyz", atomsList=frames)
    dataset.initialise()
    return dataset


def test_the_resolver_says_whether_a_dataset_is_periodic():
    resolver = InputResolver(env=None)
    assert resolver.resolve("reference.periodic", dataset=_dataset(cell=np.eye(3) * 10)) is True
    assert resolver.resolve("reference.periodic", dataset=_dataset()) is False


def _gyradius(periodic):
    source = FlatInputSource({
        "positions": np.tile(_POSITIONS, (2, 1)),
        "elements": np.array([1, 1, 8, 1, 1, 8]),
        "offsets": np.array([0, 3, 6]),
        "periodic": periodic,
    })
    return InProcessExecutor(default_registry).run("ffast.gyradius", source, {})


def test_gyradius_on_periodic_data_carries_a_warning():
    result = _gyradius(periodic=True)
    assert isinstance(result, MetricResult)
    assert result.values.shape == (2,)
    assert any("periodic" in w for w in result.warnings)


def test_gyradius_on_molecules_has_no_warning():
    result = _gyradius(periodic=False)
    assert isinstance(result, MetricResult)
    assert result.warnings == []
