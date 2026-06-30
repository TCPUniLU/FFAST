import numpy as np
import pytest

from ffast.config.models import AtomColorPresentation
from ffast.metrics.executor import InProcessExecutor, MetricExecutor
from ffast.metrics.models import MetricFailure, MetricResult
from ffast.metrics.registry import MetricRegistry
from ffast.visualization.metric_adapter import MetricColorAdapter


@pytest.fixture
def registry():
    r = MetricRegistry()

    @r.metric(id="test.per_atom_scalar", inputs={"x": "ref.x"}, shape="per_structure_per_atom", unit="force")
    def per_atom_scalar(x):
        return x

    @r.metric(id="test.always_fails", inputs={"x": "ref.x"}, shape="scalar", unit="energy")
    def always_fails(x):
        raise RuntimeError("boom")

    return r


@pytest.fixture
def adapter(registry):
    return MetricColorAdapter(InProcessExecutor(registry))


PRESENTATION = AtomColorPresentation(metric_id="test.per_atom_scalar", colormap="viridis")


def test_colors_shape(adapter):
    values = np.array([0.0, 0.5, 1.0])
    colors = adapter.compute_colors("test.per_atom_scalar", {"x": values}, {}, PRESENTATION)
    assert colors.shape == (3, 4)


def test_colors_range(adapter):
    values = np.array([0.0, 0.5, 1.0])
    colors = adapter.compute_colors("test.per_atom_scalar", {"x": values}, {}, PRESENTATION)
    assert np.all(colors >= 0.0)
    assert np.all(colors <= 1.0)


def test_vmin_vmax_respected(adapter):
    values = np.array([0.0, 5.0, 10.0])
    presentation = AtomColorPresentation(metric_id="test.per_atom_scalar", colormap="viridis", vmin=0.0, vmax=10.0)
    colors = adapter.compute_colors("test.per_atom_scalar", {"x": values}, {}, presentation)
    assert np.allclose(colors[:, 3], 1.0)


def test_constant_values_dont_crash(adapter):
    values = np.array([3.0, 3.0, 3.0])
    colors = adapter.compute_colors("test.per_atom_scalar", {"x": values}, {}, PRESENTATION)
    assert colors.shape == (3, 4)
    assert np.allclose(colors[0], colors[1])


def test_metric_failure_propagated(adapter):
    result = adapter.compute_colors("test.always_fails", {"x": 1.0}, {}, PRESENTATION)
    assert isinstance(result, MetricFailure)
    assert result.metric_id == "test.always_fails"


# ── MetricExecutor ABC acceptance ─────────────────────────────────────────────

def test_adapter_accepts_any_metric_executor(registry):
    """MetricColorAdapter must accept any MetricExecutor, not just InProcessExecutor."""
    class _StubExecutor(MetricExecutor):
        def run(self, id, inputs, parameters):
            return MetricResult(
                metric_id=id,
                shape="per_structure_per_atom",
                dtype="float32",
                unit="force",
                compute_parameters={},
                implementation_hash="stub",
                checksum="stub",
                values=np.array([0.0, 0.5, 1.0]),
            )

    adapter = MetricColorAdapter(_StubExecutor())
    colors = adapter.compute_colors("test.per_atom_scalar", {"x": np.array([0.0, 0.5, 1.0])}, {}, PRESENTATION)
    assert colors.shape == (3, 4)
