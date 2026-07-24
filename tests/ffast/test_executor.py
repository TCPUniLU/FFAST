import numpy as np
import pytest
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.registry import MetricRegistry
from ffast.metrics.executor import InProcessExecutor


@pytest.fixture
def registry():
    return MetricRegistry()


@pytest.fixture
def executor(registry):
    return InProcessExecutor(registry)


@pytest.fixture
def registry_with_force_mae(registry):
    @registry.metric(
        id="test.force_mae",
        inputs={"force_difference": "ffast.force_difference"},
        shape="per_structure_per_atom",
        unit="force",
        parameters={
            "norm": {"type": "choice", "choices": ["l1", "l2"], "default": "l2", "role": "compute"},
            "colormap": {"type": "choice", "choices": ["viridis", "plasma"], "default": "viridis", "role": "present"},
        },
    )
    def force_mae(force_difference, *, norm="l2"):
        if norm == "l1":
            return np.mean(np.abs(force_difference), axis=-1)
        return np.linalg.norm(force_difference, axis=-1)

    return registry


def test_run_returns_metric_result(registry_with_force_mae):
    executor = InProcessExecutor(registry_with_force_mae)
    diff = np.zeros((2, 3, 3))
    diff[0, 1] = [1.0, 1.0, 1.0]

    result = executor.run(
        "test.force_mae",
        source=FlatInputSource({"force_difference": diff}),
        parameters={"norm": "l2"},
    )

    assert isinstance(result, MetricResult)
    assert result.values.shape == (2, 3)
    assert np.isclose(result.values[0, 1], np.sqrt(3))
    assert np.isclose(result.values[0, 0], 0.0)
    assert result.metric_id == "test.force_mae"
    assert result.shape == "per_structure_per_atom"
    assert result.unit == "force"


def test_result_is_cached(registry_with_force_mae):
    from ffast.metrics.cache import MetricCache
    cache = MetricCache()
    executor = InProcessExecutor(registry_with_force_mae, cache=cache)
    diff = np.ones((2, 3, 3))

    result1 = executor.run("test.force_mae", source=FlatInputSource({"force_difference": diff}), parameters={"norm": "l2"})
    result2 = executor.run("test.force_mae", source=FlatInputSource({"force_difference": diff}), parameters={"norm": "l2"})

    assert result1 is result2  # same object from cache


def test_cache_miss_on_different_compute_param(registry_with_force_mae):
    from ffast.metrics.cache import MetricCache
    cache = MetricCache()
    executor = InProcessExecutor(registry_with_force_mae, cache=cache)
    diff = np.ones((2, 3, 3))

    result_l2 = executor.run("test.force_mae", source=FlatInputSource({"force_difference": diff}), parameters={"norm": "l2"})
    result_l1 = executor.run("test.force_mae", source=FlatInputSource({"force_difference": diff}), parameters={"norm": "l1"})

    assert result_l2 is not result_l1
    assert result_l2.checksum != result_l1.checksum


def test_presentation_param_does_not_affect_cache(registry_with_force_mae):
    from ffast.metrics.cache import MetricCache
    cache = MetricCache()
    executor = InProcessExecutor(registry_with_force_mae, cache=cache)
    diff = np.ones((2, 3, 3))

    result1 = executor.run("test.force_mae", source=FlatInputSource({"force_difference": diff}), parameters={"norm": "l2", "colormap": "viridis"})
    result2 = executor.run("test.force_mae", source=FlatInputSource({"force_difference": diff}), parameters={"norm": "l2", "colormap": "plasma"})

    # Presentation params don't affect compute — same cache entry
    assert result1 is result2


def test_present_parameters_not_passed_to_function(registry):
    received = {}

    @registry.metric(
        id="test.spy_metric",
        inputs={"x": "reference.x"},
        shape="scalar",
        unit="energy",
        parameters={
            "colormap": {"type": "choice", "choices": ["a", "b"], "default": "a", "role": "present"},
        },
    )
    def spy_metric(x, **kwargs):
        received.update(kwargs)
        return x

    executor = InProcessExecutor(registry)
    executor.run("test.spy_metric", source=FlatInputSource({"x": 1.0}), parameters={"colormap": "b"})

    assert "colormap" not in received


def test_unknown_id_raises(executor):
    with pytest.raises(KeyError):
        executor.run("test.nonexistent", source=FlatInputSource({}), parameters={})


def test_failing_metric_returns_metric_failure(registry):
    @registry.metric(id="test.bad_metric", inputs={"x": "ref.x"}, shape="scalar", unit="energy")
    def bad_metric(x):
        raise ValueError("something went wrong")

    executor = InProcessExecutor(registry)
    result = executor.run("test.bad_metric", source=FlatInputSource({"x": 1.0}), parameters={})

    assert isinstance(result, MetricFailure)
    assert result.metric_id == "test.bad_metric"
    assert "ValueError" in result.traceback
    assert "something went wrong" in result.traceback


def test_failure_does_not_affect_other_metrics(registry):
    @registry.metric(id="test.bad2", inputs={"x": "ref.x"}, shape="scalar", unit="energy")
    def bad2(x):
        raise RuntimeError("boom")

    @registry.metric(id="test.good", inputs={"x": "ref.x"}, shape="scalar", unit="energy")
    def good(x):
        return x * 2

    executor = InProcessExecutor(registry)
    bad_result = executor.run("test.bad2", source=FlatInputSource({"x": 1.0}), parameters={})
    good_result = executor.run("test.good", source=FlatInputSource({"x": 3.0}), parameters={})

    assert isinstance(bad_result, MetricFailure)
    assert isinstance(good_result, MetricResult)
    assert np.isclose(good_result.values, 6.0)


def test_auto_resolves_metric_dependencies(registry):
    """Executor auto-runs dependency metrics when input ref is a registered metric ID."""

    @registry.metric(
        id="test.double",
        inputs={"x": "raw.x"},
        shape="scalar",
        unit="energy",
    )
    def double(x):
        return x * 2

    @registry.metric(
        id="test.quadruple",
        inputs={"doubled": "test.double"},
        shape="scalar",
        unit="energy",
    )
    def quadruple(doubled):
        return doubled * 2

    executor = InProcessExecutor(registry)
    result = executor.run("test.quadruple", source=FlatInputSource({"x": 3.0}), parameters={})

    assert isinstance(result, MetricResult)
    assert np.isclose(result.values, 12.0)


def test_dependency_failure_propagates(registry):
    @registry.metric(id="test.fails", inputs={"x": "raw.x"}, shape="scalar", unit="energy")
    def fails(x):
        raise RuntimeError("dep failed")

    @registry.metric(id="test.uses_fails", inputs={"y": "test.fails"}, shape="scalar", unit="energy")
    def uses_fails(y):
        return y

    executor = InProcessExecutor(registry)
    result = executor.run("test.uses_fails", source=FlatInputSource({"x": 1.0}), parameters={})

    assert isinstance(result, MetricFailure)
    assert result.metric_id == "test.uses_fails"
    assert "test.fails" in result.traceback


def test_result_has_implementation_hash(registry):
    @registry.metric(id="test.simple", inputs={"x": "raw.x"}, shape="scalar", unit="energy")
    def simple(x):
        return x

    executor = InProcessExecutor(registry)
    result = executor.run("test.simple", source=FlatInputSource({"x": 1.0}), parameters={})

    assert isinstance(result.implementation_hash, str)
    assert len(result.implementation_hash) == 16


def test_result_has_checksum(registry):
    @registry.metric(id="test.simple2", inputs={"x": "raw.x"}, shape="scalar", unit="energy")
    def simple2(x):
        return x

    executor = InProcessExecutor(registry)
    result = executor.run("test.simple2", source=FlatInputSource({"x": 1.0}), parameters={})

    assert isinstance(result.checksum, str)
    assert len(result.checksum) == 64  # SHA-256 hex
