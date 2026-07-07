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
    # Data min/max (3, 7) deliberately differ from the explicit vmin/vmax
    # (0, 10) so this can't pass by silently falling back to
    # np.nanmin/np.nanmax of the data instead of using the presentation's
    # explicit range.
    values = np.array([3.0, 5.0, 7.0])
    presentation = AtomColorPresentation(metric_id="test.per_atom_scalar", colormap="viridis", vmin=0.0, vmax=10.0)
    colors = adapter.compute_colors("test.per_atom_scalar", {"x": values}, {}, presentation)

    # Ground truth from the real colormap-scaling math in metric_adapter.py:
    # normalized = clip((value - vmin) / (vmax - vmin), 0, 1) using the
    # EXPLICIT vmin=0/vmax=10 (0.3 and 0.7), NOT the data's own min/max
    # (which would normalize to 0.0 and 1.0).
    from vispy.color import get_colormap
    cmap = get_colormap("viridis")
    expected_min = cmap[np.float32(0.3)].rgba
    expected_max = cmap[np.float32(0.7)].rgba
    assert np.allclose(colors[0], expected_min)
    assert np.allclose(colors[2], expected_max)


def test_constant_values_dont_crash(adapter):
    values = np.array([3.0, 3.0, 3.0])
    colors = adapter.compute_colors("test.per_atom_scalar", {"x": values}, {}, PRESENTATION)
    assert colors.shape == (3, 4)

    # vmin == vmax (both fall back to the constant data value) hits the
    # explicit vmax==vmin branch in metric_adapter.py, which maps every value
    # to normalized 0.0 (not just "some" identical value) — assert against
    # that concrete, real colormap output rather than mere internal equality.
    from vispy.color import get_colormap
    cmap = get_colormap("viridis")
    expected = cmap[np.float32(0.0)].rgba
    assert np.allclose(colors, np.tile(expected, (3, 1)))


def test_metric_failure_propagated(adapter):
    result = adapter.compute_colors("test.always_fails", {"x": 1.0}, {}, PRESENTATION)
    assert isinstance(result, MetricFailure)
    assert result.metric_id == "test.always_fails"


def test_metric_failure_returned_verbatim_not_converted_to_colors():
    """When the executor yields a MetricFailure, compute_colors returns that exact
    object unchanged — it does NOT attempt colormap normalisation or produce an
    RGBA array. Uses a stub executor so the failure object's identity is checked.
    """
    sentinel = MetricFailure(
        metric_id="test.per_atom_scalar",
        traceback="deliberate upstream failure",
        parameters={"norm": "l2"},
    )

    class _FailingExecutor(MetricExecutor):
        def run(self, id, inputs, parameters):
            return sentinel

    adapter = MetricColorAdapter(_FailingExecutor())
    out = adapter.compute_colors(
        "test.per_atom_scalar",
        {"x": np.array([1.0, 2.0, 3.0])},
        {"norm": "l2"},
        PRESENTATION,
    )
    # Same object, passed straight through (not an ndarray of colors).
    assert out is sentinel
    assert not isinstance(out, np.ndarray)
    assert out.traceback == "deliberate upstream failure"
    assert out.parameters == {"norm": "l2"}


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
