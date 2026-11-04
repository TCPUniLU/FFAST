"""Metric Execution Context (ADR 0035).

The resolution rules that used to be re-implemented in the panel path, the
in-process executor, and the worker pool now live here and are tested here — in
isolation, with no Environment and no live worker pool. A tiny recording
``run_fn`` stands in for both transports so the driver's cache / dependency /
failure behaviour is observable directly.
"""
from __future__ import annotations

import pickle

import numpy as np
import pytest

from ffast.metrics.cache import MetricCache
from ffast.metrics.execution import (
    DepInput,
    FlatInputSource,
    RawInput,
    build_execution_plan,
    run_plan,
)
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.registry import MetricRegistry


# ── registry fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def registry():
    r = MetricRegistry()

    @r.metric(id="t.diff", inputs={"reference": "reference.forces", "predicted": "prediction.forces"},
              shape="per_structure_per_atom", unit="force")
    def diff(reference, predicted):
        return predicted - reference

    @r.metric(id="t.mae", inputs={"d": "t.diff"}, shape="scalar", unit="force")
    def mae(d):
        return np.mean(np.abs(d))

    @r.metric(id="t.rmse", inputs={"d": "t.diff"}, shape="scalar", unit="force")
    def rmse(d):
        return np.sqrt(np.mean(d ** 2))

    @r.metric(
        id="t.params",
        inputs={"x": "ref.x"},
        shape="scalar",
        unit="energy",
        parameters={
            "norm": {"type": "choice", "choices": ["l1", "l2"], "default": "l2", "role": "compute"},
            "colormap": {"type": "choice", "choices": ["a", "b"], "default": "a", "role": "present"},
        },
    )
    def params(x, *, norm="l2"):
        return x

    @r.metric(id="t.opt", inputs={"x": "ref.x"}, optional_inputs=["offsets"],
              shape="scalar", unit="energy")
    def opt(x, offsets=None):
        return x

    return r


def _direct_run_fn(recorder):
    """A run_fn that calls the metric function directly and records each call."""
    def run_fn(mid, schema, fn, kwargs, compute_params):
        recorder.append(mid)
        return fn(**kwargs, **compute_params)
    return run_fn


# ── plan building: ordering ─────────────────────────────────────────────────

def test_dependencies_ordered_before_dependents(registry):
    plan = build_execution_plan(registry, "t.mae", {}, FlatInputSource({}))
    ids = [s.metric_id for s in plan.steps]
    assert ids.index("t.diff") < ids.index("t.mae")


def test_shared_dependency_appears_once(registry):
    plan = build_execution_plan(registry, ["t.mae", "t.rmse"], {}, FlatInputSource({}))
    ids = [s.metric_id for s in plan.steps]
    assert ids.count("t.diff") == 1
    assert ids.index("t.diff") < ids.index("t.mae")
    assert ids.index("t.diff") < ids.index("t.rmse")


def test_metric_dep_is_a_depinput(registry):
    plan = build_execution_plan(registry, "t.mae", {}, FlatInputSource({}))
    mae_step = next(s for s in plan.steps if s.metric_id == "t.mae")
    assert mae_step.bindings["d"] == DepInput("t.diff")


# ── plan building: compute parameters ─────────────────────────────────────────

def test_only_compute_params_kept_with_defaults(registry):
    plan = build_execution_plan(registry, "t.params", {"colormap": "b"}, FlatInputSource({"x": 1.0}))
    step = plan.steps[0]
    assert step.compute_params == {"norm": "l2"}  # default applied, present-param dropped


def test_explicit_compute_param_overrides_default(registry):
    plan = build_execution_plan(registry, "t.params", {"norm": "l1"}, FlatInputSource({"x": 1.0}))
    assert plan.steps[0].compute_params == {"norm": "l1"}


# ── plan building: optional / missing input semantics ─────────────────────────

def test_missing_required_raw_input_records_failure(registry):
    plan = build_execution_plan(registry, "t.diff", {}, FlatInputSource({"reference": np.zeros((1, 3))}))
    step = plan.steps[0]
    assert step.failure is not None
    assert "Missing raw input 'predicted'" in step.failure


def test_present_none_required_input_is_unavailable(registry):
    plan = build_execution_plan(
        registry, "t.diff", {}, FlatInputSource({"reference": np.zeros((1, 3)), "predicted": None})
    )
    step = plan.steps[0]
    assert step.failure is not None
    assert "unavailable for this dataset" in step.failure


def test_optional_input_missing_becomes_none_without_failure(registry):
    plan = build_execution_plan(registry, "t.opt", {}, FlatInputSource({"x": 1.0}))
    step = plan.steps[0]
    assert step.failure is None
    assert step.bindings["offsets"] == RawInput(None)


def test_raw_value_is_coerced_to_ndarray(registry):
    plan = build_execution_plan(registry, "t.params", {}, FlatInputSource({"x": [1.0, 2.0]}))
    binding = plan.steps[0].bindings["x"]
    assert isinstance(binding, RawInput)
    assert isinstance(binding.value, np.ndarray)


def test_flat_source_distinguishes_absent_from_none():
    src = FlatInputSource({"a": None})
    assert src.get("m", "a", "ref.a") == (True, None)     # present, explicitly None
    assert src.get("m", "b", "ref.b") == (False, None)    # absent


# ── plan is transport-ready (picklable) ───────────────────────────────────────

def test_plan_is_picklable(registry):
    plan = build_execution_plan(
        registry, "t.mae", {}, FlatInputSource({"reference": np.zeros((2, 3)), "predicted": np.ones((2, 3))})
    )
    restored = pickle.loads(pickle.dumps(plan))
    assert [s.metric_id for s in restored.steps] == [s.metric_id for s in plan.steps]


# ── run_plan: driver behaviour ────────────────────────────────────────────────

def test_run_plan_wires_dependency_output(registry):
    plan = build_execution_plan(
        registry, "t.mae", {},
        FlatInputSource({"reference": np.zeros((2, 3)), "predicted": np.ones((2, 3))}),
    )
    calls = []
    results = run_plan(plan, registry, MetricCache(), _direct_run_fn(calls))
    assert isinstance(results["t.mae"], MetricResult)
    assert float(results["t.mae"].values) == pytest.approx(1.0)
    assert calls == ["t.diff", "t.mae"]  # dep ran first, then dependent


def test_run_plan_static_failure_skips_run_fn(registry):
    plan = build_execution_plan(registry, "t.diff", {}, FlatInputSource({"reference": np.zeros((1, 3))}))
    calls = []
    results = run_plan(plan, registry, MetricCache(), _direct_run_fn(calls))
    assert isinstance(results["t.diff"], MetricFailure)
    assert calls == []  # never executed — the required input was missing


def test_run_plan_propagates_dependency_failure(registry):
    # predicted absent → t.diff fails → t.mae must fail citing the dependency
    plan = build_execution_plan(registry, "t.mae", {}, FlatInputSource({"reference": np.zeros((1, 3))}))
    results = run_plan(plan, registry, MetricCache(), _direct_run_fn([]))
    assert isinstance(results["t.mae"], MetricFailure)
    assert "t.diff" in results["t.mae"].traceback


def test_run_plan_cache_hit_skips_run_fn(registry):
    inputs = FlatInputSource({"x": np.array([2.0])})
    cache = MetricCache()
    plan = build_execution_plan(registry, "t.params", {}, inputs)

    calls1 = []
    run_plan(plan, registry, cache, _direct_run_fn(calls1))
    assert calls1 == ["t.params"]

    # Same plan, same cache → second run is served from cache, run_fn untouched.
    plan2 = build_execution_plan(registry, "t.params", {}, inputs)
    calls2 = []
    run_plan(plan2, registry, cache, _direct_run_fn(calls2))
    assert calls2 == []


def test_run_plan_run_fn_failure_recorded(registry):
    plan = build_execution_plan(registry, "t.params", {}, FlatInputSource({"x": np.array([1.0])}))

    def failing(mid, schema, fn, kwargs, cparams):
        return MetricFailure(metric_id=mid, traceback="boom", parameters={})

    results = run_plan(plan, registry, MetricCache(), failing)
    assert isinstance(results["t.params"], MetricFailure)
    assert results["t.params"].traceback == "boom"


# ── what kind of failure (ADR 0085) ───────────────────────────────────────────

def test_a_metric_that_raises_fails_with_kind_failed(registry):
    plan = build_execution_plan(registry, "t.params", {}, FlatInputSource({"x": np.array([1.0])}))

    def raising(mid, schema, fn, kwargs, cparams):
        return MetricFailure(metric_id=mid, traceback="Traceback (most recent call last):\nValueError: x", parameters={})

    results = run_plan(plan, registry, MetricCache(), raising)
    assert results["t.params"].kind == "failed"


def test_a_missing_required_input_fails_with_kind_missing_input(registry):
    plan = build_execution_plan(registry, "t.diff", {}, FlatInputSource({"reference": np.zeros((1, 3))}))
    results = run_plan(plan, registry, MetricCache(), _direct_run_fn([]))
    assert results["t.diff"].kind == "missing_input"


def test_a_stopped_run_fails_with_kind_temporary(registry):
    plan = build_execution_plan(registry, "t.params", {}, FlatInputSource({"x": np.array([1.0])}))
    results = run_plan(plan, registry, MetricCache(), _direct_run_fn([]), stopped=lambda: True)
    assert results["t.params"].kind == "temporary"


def test_a_failed_dependency_passes_its_kind_on(registry):
    plan = build_execution_plan(registry, "t.mae", {}, FlatInputSource({"reference": np.zeros((1, 3))}))
    results = run_plan(plan, registry, MetricCache(), _direct_run_fn([]))
    assert results["t.mae"].kind == "missing_input"


def test_a_failure_with_a_kind_survives_pickling():
    failure = MetricFailure(metric_id="m", traceback="Stopped", kind="temporary")
    assert pickle.loads(pickle.dumps(failure)).kind == "temporary"


# ── cycle handling ─────────────────────────────────────────────────────────
#
# build_execution_plan's DFS tracks the current recursion stack (a ``visiting``
# set) in addition to the ``seen`` dedup set, so a dependency cycle raises a
# ValueError instead of silently emitting an unsatisfiable plan. This matches
# ffast/metrics/graph.py's MetricGraph.freeze, which reports the same cycle via
# TopologicalSorter (contrast test below).

@pytest.fixture
def cyclic_registry():
    """Two metrics that depend on each other: cyc.a <-> cyc.b."""
    r = MetricRegistry()
    r.metric(id="cyc.a", inputs={"x": "cyc.b"}, shape="scalar", unit="energy")(lambda x: x)
    r.metric(id="cyc.b", inputs={"y": "cyc.a"}, shape="scalar", unit="energy")(lambda y: y)
    return r


def test_build_execution_plan_raises_on_cycle(cyclic_registry):
    # build_execution_plan's DFS tracks the current recursion stack (not just a
    # ``seen`` set), so a dependency cycle raises instead of silently emitting
    # an unsatisfiable plan. MetricGraph.freeze rejects the same graph (below).
    with pytest.raises(ValueError, match="cycle"):
        build_execution_plan(cyclic_registry, "cyc.a", {}, FlatInputSource({}))


def test_metric_graph_freeze_does_detect_the_same_cycle(cyclic_registry):
    # Contrast: the frozen-graph path (graph.py) flags the same cycle that
    # build_execution_plan now raises on above.
    from ffast.metrics.graph import MetricGraph

    graph = MetricGraph()
    errors = graph.freeze(cyclic_registry._metrics, valid_refs=None)
    assert any(mid == "__cycle__" for mid, _ in errors)


def test_self_cycle_raises():
    # A metric that lists itself as an input is the degenerate 1-node cycle.
    r = MetricRegistry()
    r.metric(id="loop.self", inputs={"x": "loop.self"}, shape="scalar", unit="energy")(lambda x: x)

    with pytest.raises(ValueError, match="cycle"):
        build_execution_plan(r, "loop.self", {}, FlatInputSource({}))


def test_valid_diamond_dag_produces_correct_topological_order():
    # Contrast to the cycle cases: a well-formed diamond
    #   leaf  <-  left, right  <-  root
    # must order the shared leaf before both middles, both middles before the
    # root, and list the shared leaf exactly once.
    r = MetricRegistry()
    r.metric(id="d.leaf", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(lambda x: x)
    r.metric(id="d.left", inputs={"a": "d.leaf"}, shape="scalar", unit="energy")(lambda a: a)
    r.metric(id="d.right", inputs={"b": "d.leaf"}, shape="scalar", unit="energy")(lambda b: b)
    r.metric(
        id="d.root",
        inputs={"l": "d.left", "r": "d.right"},
        shape="scalar",
        unit="energy",
    )(lambda l, r: l + r)

    plan = build_execution_plan(r, "d.root", {}, FlatInputSource({"x": np.array(2.0)}))
    ids = [s.metric_id for s in plan.steps]
    assert ids.count("d.leaf") == 1
    assert ids.index("d.leaf") < ids.index("d.left")
    assert ids.index("d.leaf") < ids.index("d.right")
    assert ids.index("d.left") < ids.index("d.root")
    assert ids.index("d.right") < ids.index("d.root")

    # And it actually executes end-to-end: root = left + right = 2 + 2 = 4.
    results = run_plan(plan, r, MetricCache(), _direct_run_fn([]))
    assert isinstance(results["d.root"], MetricResult)
    assert float(results["d.root"].values) == pytest.approx(4.0)


# ── inputs keyed by where they came from (2026-11-02) ────────────────────────
# On 1.77M structures each force metric spent ~18 s hashing 4 GB of forces
# for its cache key. A source that knows where an input comes from (this ref
# of this dataset and prediction) names it instead, and a step reading
# another metric's output is keyed by that metric's key, not its values.

class _NamedSource(FlatInputSource):
    def identity(self, ref):
        return f"{ref}@ds"


def _no_hashing(monkeypatch):
    import ffast.metrics.cache as cache_module

    def refuse(arr):
        raise AssertionError("an input was hashed by content")

    monkeypatch.setattr(cache_module, "_array_fingerprint", refuse)


def test_named_inputs_and_dependencies_are_not_hashed(registry, monkeypatch):
    _no_hashing(monkeypatch)
    ref, pred = np.zeros((4, 3)), np.ones((4, 3))
    cache, ran = MetricCache(), []
    for mid in ("t.mae", "t.rmse"):
        plan = build_execution_plan(registry, mid, {}, _NamedSource({"reference": ref, "predicted": pred}))
        out = run_plan(plan, registry, cache, _direct_run_fn(ran))
        assert isinstance(out[mid], MetricResult)
    assert ran == ["t.diff", "t.mae", "t.rmse"]       # the difference is computed once


def test_a_named_input_is_trusted_by_its_name(registry):
    """The name stands for the value: the same name means the same input."""
    cache, ran = MetricCache(), []
    for value in (np.zeros((4, 3)), np.ones((4, 3))):
        source = _NamedSource({"reference": value, "predicted": value})
        run_plan(build_execution_plan(registry, "t.diff", {}, source), registry, cache, _direct_run_fn(ran))
    assert ran == ["t.diff"]


def test_an_unnamed_input_is_still_hashed_by_content(registry):
    cache, ran = MetricCache(), []
    for value in (np.zeros((4, 3)), np.ones((4, 3))):
        source = FlatInputSource({"reference": value, "predicted": value})
        run_plan(build_execution_plan(registry, "t.diff", {}, source), registry, cache, _direct_run_fn(ran))
    assert ran == ["t.diff", "t.diff"]


def test_the_server_names_inputs_of_a_loaded_dataset_and_prediction():
    from types import SimpleNamespace
    from ffast.metrics.input_resolver import _ResolverSource

    loaded = SimpleNamespace(fingerprint="ds", isSubDataset=False)
    model = SimpleNamespace(fingerprint="m")
    source = _ResolverSource(None, model, loaded)
    assert source.identity("reference.forces") == "reference.forces|ds"
    assert source.identity("prediction.forces") == "prediction.forces|ds|m"
    assert source.identity("offsets") == "offsets|ds"
    # a plot selection changes under the same name
    assert source.identity("selection.indices") is None
    # a subset that follows the zoom changes its frames under the same fingerprint
    view = SimpleNamespace(fingerprint="sub", isSubDataset=True)
    assert _ResolverSource(None, model, view).identity("reference.forces") is None
    # a prediction input without a known prediction
    assert _ResolverSource(None, None, loaded).identity("prediction.forces") is None
