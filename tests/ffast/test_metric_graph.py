"""Metric DX: registry.freeze() validation + graph compute plans (decision M1/H2)."""
import numpy as np
import pytest

from ffast.metrics import dims
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.registry import MetricRegistry


def _registry_with_dep_chain():
    """A → B → leaf-ref, plus a sibling that shares B. Returns the registry."""
    r = MetricRegistry()

    @r.metric(
        id="t.diff",
        inputs={"reference": "reference.forces", "predicted": "prediction.forces"},
        shape=(dims.N_atoms, dims.xyz),
        unit="force",
    )
    def diff(reference, predicted):
        return predicted - reference

    @r.metric(
        id="t.mae",
        inputs={"d": "t.diff"},
        shape=(dims.scalar,),
        unit="force",
    )
    def mae(d):
        return np.mean(np.abs(d))

    @r.metric(
        id="t.rmse",
        inputs={"d": "t.diff"},
        shape=(dims.scalar,),
        unit="force",
    )
    def rmse(d):
        return np.sqrt(np.mean(d ** 2))

    return r


def test_freeze_clean_passes():
    r = _registry_with_dep_chain()
    assert r.freeze() == []


def test_compute_plan_orders_and_dedupes_shared_dep():
    r = _registry_with_dep_chain()
    r.freeze()
    plan = r.compute_plan(["t.mae", "t.rmse"])
    # Shared dependency appears exactly once, before both dependents.
    assert plan.count("t.diff") == 1
    assert plan.index("t.diff") < plan.index("t.mae")
    assert plan.index("t.diff") < plan.index("t.rmse")


def test_dependencies_of():
    r = _registry_with_dep_chain()
    r.freeze()
    assert r.dependencies_of("t.mae") == {"t.diff"}
    assert r.dependencies_of("t.diff") == set()


def test_freeze_flags_unknown_symbolic_ref():
    r = MetricRegistry()

    @r.metric(
        id="t.bad_ref",
        inputs={"x": "reference.bogus"},  # not in ALL_VALID_REFS, not a metric
        shape=(dims.scalar,),
        unit="energy",
    )
    def bad(x):
        return np.mean(x)

    errors = r.freeze()
    assert any(mid == "t.bad_ref" and "Unknown symbolic ref" in msg for mid, msg in errors)


def test_freeze_flags_legacy_string_shape():
    r = MetricRegistry()

    @r.metric(
        id="t.legacy_shape",
        inputs={"reference": "reference.energies"},
        shape="N_frames",  # legacy string instead of dims.*
        unit="energy",
    )
    def legacy(reference):
        return reference

    errors = r.freeze()
    assert any(mid == "t.legacy_shape" and "legacy string" in msg for mid, msg in errors)


def test_freeze_detects_cycle():
    r = MetricRegistry()

    @r.metric(id="t.a", inputs={"x": "t.b"}, shape=(dims.scalar,), unit="energy")
    def a(x):
        return x

    @r.metric(id="t.b", inputs={"x": "t.a"}, shape=(dims.scalar,), unit="energy")
    def b(x):
        return x

    errors = r.freeze()
    assert any(mid == "__cycle__" for mid, _ in errors)


def test_compute_plan_requires_freeze():
    r = _registry_with_dep_chain()
    with pytest.raises(RuntimeError):
        r.compute_plan(["t.mae"])


def test_run_batch_shares_intermediate_and_matches_single_run():
    r = _registry_with_dep_chain()
    r.freeze()
    ex = InProcessExecutor(r)
    inputs = {
        "reference": np.zeros((4, 3)),
        "predicted": np.ones((4, 3)),
    }
    batch = ex.run_batch(["t.mae", "t.rmse"], inputs, {})
    assert set(batch) == {"t.mae", "t.rmse"}
    assert float(batch["t.mae"].values) == pytest.approx(1.0)
    assert float(batch["t.rmse"].values) == pytest.approx(1.0)
    # Batch result matches an independent single run of the same metric.
    single = ex.run("t.mae", inputs, {})
    assert float(single.values) == pytest.approx(float(batch["t.mae"].values))
