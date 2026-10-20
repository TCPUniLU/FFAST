"""
Every built-in metric gives, on flat inputs, the numbers it gave on uniform
inputs before ADR 0058 step 6.

Until step 6 a uniform dataset handed metrics 3D per-atom arrays, one
structure's elements and masses, and no offsets. Now every dataset hands flat
arrays (one row per atom, frame after frame), every atom's elements and
masses, and offsets. The numbers the metrics gave on the old form were saved
before their 3D code was deleted (data/metrics_on_uniform_before_flat.npz,
written at commit d7fb84f from ``_uniform()`` below). Joining frames gives the
same order as flattening (frames, atoms, ...), so results are compared
flattened.
"""
from pathlib import Path

import numpy as np
import pytest

import ffast.metrics.builtin  # noqa: F401  (registers the built-in metrics)
from ffast.metrics.execution import InputSource
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.models import MetricFailure
from ffast.metrics.registry import default_registry

N_FRAMES, N_ATOMS = 5, 4


def _uniform():
    rng = np.random.default_rng(7)
    return {
        "reference.forces": rng.normal(size=(N_FRAMES, N_ATOMS, 3)),
        "prediction.forces": rng.normal(size=(N_FRAMES, N_ATOMS, 3)),
        "reference.positions": rng.normal(size=(N_FRAMES, N_ATOMS, 3)),
        "reference.elements": np.array([6, 1, 1, 8]),
        "reference.masses": np.array([12.011, 1.008, 1.008, 15.999]),
        "reference.energies": rng.normal(size=N_FRAMES),
        "prediction.energies": rng.normal(size=N_FRAMES),
        "n_atoms": np.full(N_FRAMES, N_ATOMS),
    }


def _flat(uniform):
    flat = dict(uniform)
    for ref in ("reference.forces", "prediction.forces", "reference.positions"):
        flat[ref] = uniform[ref].reshape(-1, 3)
    for ref in ("reference.elements", "reference.masses"):
        flat[ref] = np.tile(uniform[ref], N_FRAMES)
    flat["offsets"] = np.arange(N_FRAMES + 1) * N_ATOMS
    return flat


class _ByRef(InputSource):
    def __init__(self, values):
        self._values = values

    def get(self, metric_id, local_key, ref):
        if ref in self._values:
            return True, self._values[ref]
        return False, None


def _leaves(metric_id, seen=None):
    """The non-metric refs a metric reads, through its dependencies."""
    seen = set() if seen is None else seen
    schema, _ = default_registry.get(metric_id)
    refs = set()
    for ref in schema.inputs.values():
        if default_registry.has(ref):
            if ref not in seen:
                seen.add(ref)
                refs |= _leaves(ref, seen)
        else:
            refs.add(ref)
    return refs


AVAILABLE = set(_uniform()) | {"offsets"}
METRICS = sorted(m for m in default_registry.list_metrics() if _leaves(m) <= AVAILABLE)

BEFORE = np.load(Path(__file__).parent / "data" / "metrics_on_uniform_before_flat.npz")


def test_the_comparison_covers_the_force_and_structure_metrics():
    for metric in ("ffast.force_rmse", "ffast.force_mae_per_element", "ffast.accel_rmse",
                   "ffast.gyradius", "ffast.force_net_residual"):
        assert metric in METRICS


def test_every_metric_compared_has_its_saved_numbers():
    assert set(METRICS) == set(BEFORE.files)


@pytest.mark.parametrize("metric_id", METRICS)
def test_a_metric_gives_the_same_numbers_on_flat_inputs(metric_id):
    flat = InProcessExecutor(default_registry).run(metric_id, _ByRef(_flat(_uniform())), {})
    assert not isinstance(flat, MetricFailure), flat
    np.testing.assert_allclose(np.ravel(flat.values), BEFORE[metric_id], rtol=1e-10, atol=1e-12)


def test_acceleration_per_atom_keeps_every_atom_when_structures_differ_in_size():
    """Atom i of one structure is not atom i of another, so nothing is
    averaged: as the metric gave on variable files before step 6."""
    rng = np.random.default_rng(3)
    values = {
        "reference.forces": rng.normal(size=(7, 3)),
        "prediction.forces": rng.normal(size=(7, 3)),
        "reference.masses": np.ones(7),
        "offsets": np.array([0, 3, 7]),
    }
    executor = InProcessExecutor(default_registry)
    per_atom = executor.run("ffast.accel_mae_per_atom", _ByRef(values), {})
    accel = executor.run("ffast.accel_mae", _ByRef(values), {})
    np.testing.assert_allclose(per_atom.values, accel.values)
    assert per_atom.values.shape == (7,)


def test_the_resolver_hands_a_uniform_file_and_its_prediction_flat():
    """A uniform file and its prediction, cached as (frames, atoms, 3), both
    reach metrics as one row per atom, with every atom's elements and masses
    and the offsets (ADR 0058 step 6b)."""
    import types
    from ase import Atoms
    from ase.calculators.singlepoint import SinglePointCalculator
    from ffast.loaders.ase import aseDatasetLoader
    from ffast.metrics.input_resolver import InputResolver

    rng = np.random.default_rng(1)
    frames = []
    for _ in range(3):
        atoms = Atoms("H2O", positions=rng.normal(size=(3, 3)))
        atoms.calc = SinglePointCalculator(atoms, energy=0.0, forces=rng.normal(size=(3, 3)))
        frames.append(atoms)
    dataset = aseDatasetLoader("u.xyz", atomsList=frames)
    predicted = rng.normal(size=(3, 3, 3))
    entity = types.SimpleNamespace(get=lambda field: predicted)
    resolver = InputResolver(types.SimpleNamespace(getData=lambda *a, **k: entity))

    def resolve(ref):
        return resolver.resolve(ref, model=object(), dataset=dataset)

    assert resolve("reference.forces").shape == (9, 3)
    np.testing.assert_array_equal(resolve("prediction.forces"), predicted.reshape(-1, 3))
    assert resolve("reference.elements").tolist() == [1, 1, 8] * 3
    assert resolve("reference.masses").shape == (9,)
    assert resolve("offsets").tolist() == [0, 3, 6, 9]
