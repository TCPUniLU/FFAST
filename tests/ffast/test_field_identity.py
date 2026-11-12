"""A metric over a Dataset Field is cached under the field's values, not only
the dataset's fingerprint.

Architecture review (Nov 2026), candidate 01: the Dataset fingerprint covers
elements, positions, energies and forces (ADR 0058), not fields. A field input
was named ``<ref>|<dataset fp>`` in the metric cache, so after deleting a
dataset and loading one with the same geometry but other field values, a
metric over the field returned the old values.
"""

import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator
from ase.io import write

from ffast.core.environment import Environment
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.registry import MetricRegistry


def _write(path, charge):
    frames = []
    for i in range(3):
        a = Atoms("H2", positions=[[0, 0, 0], [0.74 + 0.01 * i, 0, 0]])
        a.calc = SinglePointCalculator(a, energy=-1.0 - i, forces=np.zeros((2, 3)))
        a.info["charge"] = charge
        frames.append(a)
    write(str(path), frames, format="extxyz")
    return path


def _load(path):
    from ffast.loaders.ase import aseDatasetLoader
    ds = aseDatasetLoader(str(path))
    ds.initialise()
    return ds


class _Model:
    isGhost = True

    def __init__(self, fingerprint):
        self.fingerprint = fingerprint

    def onDelete(self):
        pass


@pytest.fixture
def env():
    registry = MetricRegistry()
    calls = []

    @registry.metric(id="t.reference_charge", inputs={"q": "reference.info.charge"},
                     shape=("N",), unit="")
    def reference_charge(q):
        calls.append("reference")
        return np.asarray(q, dtype=float)

    @registry.metric(id="t.predicted_charge", inputs={"q": "prediction.info.charge"},
                     shape=("N",), unit="")
    def predicted_charge(q):
        calls.append("prediction")
        return np.asarray(q, dtype=float)

    e = Environment()
    e.data._metricExecutor = InProcessExecutor(registry)
    e.calls = calls
    return e


def _values(env, metric_id, dataset, model=None):
    result = env.data.compute_metric(metric_id, {}, model, dataset)
    assert hasattr(result, "values"), result
    return np.asarray(result.values).tolist()


def test_a_reload_with_other_field_values_is_not_served_the_old_result(env, tmp_path):
    a = _load(_write(tmp_path / "a.extxyz", 0.1))
    b = _load(_write(tmp_path / "b.extxyz", 0.9))
    assert a.fingerprint == b.fingerprint       # the premise: fields are not in it

    env.datasets.add(a)
    assert _values(env, "t.reference_charge", a) == [0.1, 0.1, 0.1]
    env.deleteObject(a.fingerprint)

    env.datasets.add(b)
    assert _values(env, "t.reference_charge", b) == [0.9, 0.9, 0.9]


def test_the_same_dataset_still_reads_its_result_from_the_cache(env, tmp_path):
    a = _load(_write(tmp_path / "a.extxyz", 0.1))
    env.datasets.add(a)

    _values(env, "t.reference_charge", a)
    _values(env, "t.reference_charge", a)

    assert env.calls == ["reference"]           # computed once
    assert len(a._fieldDigests) == 1            # and its field hashed once


def test_a_prediction_reloaded_with_other_field_values_is_not_served_the_old_result(env, tmp_path):
    ds = _load(_write(tmp_path / "a.extxyz", 0.0))
    env.datasets.add(ds)
    model = _Model("ghost")
    fields = env.data.predictionFields

    fields.replace("ghost", ds.fingerprint, {"info": {"charge": np.full(3, 0.1)}, "atoms": {}})
    assert _values(env, "t.predicted_charge", ds, model) == [0.1, 0.1, 0.1]
    assert _values(env, "t.predicted_charge", ds, model) == [0.1, 0.1, 0.1]
    assert env.calls == ["prediction"]          # the second read came from the cache

    fields.replace("ghost", ds.fingerprint, {"info": {"charge": np.full(3, 0.9)}, "atoms": {}})
    assert _values(env, "t.predicted_charge", ds, model) == [0.9, 0.9, 0.9]


def test_a_prediction_reloaded_without_the_field_fails_instead_of_reusing_it(env, tmp_path):
    ds = _load(_write(tmp_path / "a.extxyz", 0.0))
    env.datasets.add(ds)
    model = _Model("ghost")
    fields = env.data.predictionFields
    fields.replace("ghost", ds.fingerprint, {"info": {"charge": np.full(3, 0.1)}, "atoms": {}})
    _values(env, "t.predicted_charge", ds, model)

    fields.replace("ghost", ds.fingerprint, None)

    result = env.data.compute_metric("t.predicted_charge", {}, model, ds)
    assert not hasattr(result, "values")        # a MetricFailure: the field is gone


# ── the server's whole-dataset result store (ADR 0085) ──────────────────────
# It keys a result by (metric, dataset fp, prediction fp, parameters). A load
# that replaces an object under the same fingerprint, without a delete, must
# make it forget, as a delete does.

@pytest.fixture
def served(env):
    """The store wired as server.py wires it; returns a ``get`` that gives the
    values, or the kind of reason there are none."""
    import asyncio

    from ffast.metrics.result_store import MetricResultStore

    env.results = MetricResultStore(env)
    for event in ("DATASET_DELETED", "MODEL_DELETED", "SUBDATASET_INDICES_CHANGED", "OBJECT_REPLACED"):
        env.eventSubscribe(event, env.results.forget_objects)

    def get(metric_id, dataset_fp, model_fp=None):
        asyncio.run(env.eventHandle())      # what the server's event loop does
        out = asyncio.run(env.results.get(metric_id, {}, dataset_fp, model_fp))
        return np.asarray(out.values).tolist() if hasattr(out, "values") else out.kind
    return get


def test_a_dataset_loaded_over_one_with_its_fingerprint_is_not_served_the_old_result(env, served, tmp_path):
    a = _load(_write(tmp_path / "a.extxyz", 0.1))
    b = _load(_write(tmp_path / "b.extxyz", 0.9))
    env.datasets.add(a)
    assert served("t.reference_charge", a.fingerprint) == [0.1, 0.1, 0.1]

    env.datasets.add(b)                         # no delete in between

    assert served("t.reference_charge", b.fingerprint) == [0.9, 0.9, 0.9]


def test_a_prediction_ingested_again_is_not_served_the_old_result(env, served, tmp_path):
    from ffast.core.loading_coordinator import LoadingCoordinator

    ds = _load(_write(tmp_path / "a.extxyz", 0.0))
    env.datasets.add(ds)
    env.models.add(_Model("ghost"))
    coordinator = LoadingCoordinator(env)

    class _Source:
        def __init__(self, charge):
            self.charge = charge

        def availableFieldKeys(self):
            return (["charge"], []) if self.charge is not None else ([], [])

        def getFrameField(self, key, indices=None):
            return np.full(3, self.charge) if self.charge is not None else None

        def getAtomField(self, key, indices=None):
            return None

    def ingest(charge):
        assert coordinator._ingestPrediction(
            ds, np.zeros(3), None, path="/p.xyz", name="p", fingerprint="ghost",
            source=_Source(charge))

    ingest(0.1)
    assert served("t.predicted_charge", ds.fingerprint, "ghost") == [0.1, 0.1, 0.1]
    ingest(0.9)
    assert served("t.predicted_charge", ds.fingerprint, "ghost") == [0.9, 0.9, 0.9]
    ingest(None)
    assert served("t.predicted_charge", ds.fingerprint, "ghost") == "missing_input"
    ingest(0.5)                                 # a remembered failure is forgotten too
    assert served("t.predicted_charge", ds.fingerprint, "ghost") == [0.5, 0.5, 0.5]


def test_a_subset_of_a_replaced_dataset_shows_the_new_field_values(env, served, tmp_path):
    """A subset reads its frames through its parent object; after a replace it
    pointed at the old one and kept showing the old field values."""
    from ffast.loaders.dataset import DatasetView

    a = _load(_write(tmp_path / "a.extxyz", 0.1))
    b = _load(_write(tmp_path / "b.extxyz", 0.9))
    env.datasets.add(a)
    sub = DatasetView.frozenSubset(a, None, np.array([0, 2]), "Energy")
    sub.initialise()
    env.datasets.add(sub, -2)
    subsub = DatasetView.frozenSubset(sub, None, np.array([1]), "Energy 2")
    subsub.initialise()
    env.datasets.add(subsub, -2)
    assert served("t.reference_charge", sub.fingerprint) == [0.1, 0.1]
    assert served("t.reference_charge", subsub.fingerprint) == [0.1]

    env.datasets.add(b)

    assert sub.parent is b and subsub.parent is sub
    assert served("t.reference_charge", sub.fingerprint) == [0.9, 0.9]
    assert served("t.reference_charge", subsub.fingerprint) == [0.9]
