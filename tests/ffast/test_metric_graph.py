"""Metric DX: registry.freeze() validation (decision M1/H2).

Ordering/compute-plan surface (compute_plan/dependencies_of/run_batch) was
removed by ADR 0046 — build_execution_plan's own walk (ffast.metrics.execution)
derives ordering now; MetricGraph.freeze stays as the startup/CLI validator
tested here.
"""
import numpy as np

from ffast.metrics import dims
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


