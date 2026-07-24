"""Regression tests for metric-driven atom coloring end-to-end.

Covers the chain:
  SET_PARAMETER(ffast.atom_color, source="metric:ffast.force_mae")
  + view.state.prediction_ref set
  → resolve_atom_color_values → ffast.force_mae computed → per-atom colors

This is the server-side seam for H4 and H1 from the diagnose session.
"""
import numpy as np
import pytest

from ffast.visualization.models import VisualizationState


# ── minimal dataset / prediction stubs ──────────────────────────────────────

class _FakeDataset:
    def __init__(self, n_atoms=3, n_frames=1):
        self._n = n_atoms
        self._n_frames = n_frames
        self._pos = np.zeros((n_frames, n_atoms, 3), dtype=np.float64)
        self._z = np.array([1, 6, 8], dtype=np.int32)[:n_atoms]
        self._forces = np.ones((n_frames, n_atoms, 3), dtype=np.float64)

    def getCoordinates(self, index=None):
        if index is None:
            return self._pos
        return self._pos[index]

    def getElements(self, index=None):
        return self._z

    def getForces(self, index=None):
        if self._n_frames == 1:
            return self._forces[0]
        return self._forces[index]

    def getN(self):
        return self._n_atoms

    def getLength(self):
        return self._n_frames


class _PredictionView:
    def __init__(self, forces):
        self.forces = forces


# ── resolve_atom_color_values tests ──────────────────────────────────────────────

def _make_state_with_metric(metric_id, prediction_ref="pred-fp-001", dataset_ref="ds-fp-001"):
    state = VisualizationState(view_id="v1")
    state.dataset_ref = dataset_ref
    state.prediction_ref = prediction_ref
    state.parameters["ffast.atom_color"] = {"source": f"metric:{metric_id}"}
    return state


def test_resolve_color_force_mae_returns_per_atom_values():
    """Core path: force_mae metric produces one value per atom."""
    # Register built-ins so the metric is in the registry.
    from ffast.metrics.builtin import force_metrics  # noqa: F401
    from ffast.metrics.executor import InProcessExecutor
    from ffast.metrics.registry import _default_registry as reg
    from ffast.visualization.color_values import resolve_atom_color_values

    n_atoms = 3
    ds = _FakeDataset(n_atoms=n_atoms)
    ref_forces = np.ones((n_atoms, 3), dtype=np.float64)
    pred_forces = np.ones((n_atoms, 3), dtype=np.float64) * 2.0

    prediction_fp = "pred-fp-001"
    dataset_fp = "ds-fp-001"
    state = _make_state_with_metric("ffast.force_mae", prediction_ref=prediction_fp)

    def get_prediction(ds_fp, model_fp):
        if ds_fp == dataset_fp and model_fp == prediction_fp:
            return _PredictionView(pred_forces[np.newaxis])  # (1, n_atoms, 3)
        return None

    raw_positions = ref_forces[:, 0:3] * 0  # (n_atoms, 3) dummy coords
    result = resolve_atom_color_values(
        state, ds, 0, raw_positions, ds._z, get_prediction,
        executor=InProcessExecutor(reg),
    )

    assert result is not None, "Expected per-atom values, got None (metric failed)"
    values, label, unit = result
    assert values.shape == (n_atoms,), f"Expected ({n_atoms},), got {values.shape}"
    # pred - ref = [1,1,1], l2 norm = sqrt(3); all atoms should be equal
    assert np.all(values > 0), "Expected nonzero force errors"


def test_resolve_color_prediction_ref_override_wins_over_global():
    """ADR 0045 issue 03: an explicit ffast.atom_color 'prediction_ref' param
    overrides the view's global state.prediction_ref — the same Option B
    pattern as ffast.force_arrows (scene_builder.py) — so coloring by one
    model's error doesn't force the whole view onto that model."""
    from ffast.metrics.builtin import force_metrics  # noqa: F401
    from ffast.metrics.executor import InProcessExecutor
    from ffast.metrics.registry import _default_registry as reg
    from ffast.visualization.color_values import resolve_atom_color_values

    n_atoms = 3
    ds = _FakeDataset(n_atoms=n_atoms)
    ref_forces = np.zeros((n_atoms, 3), dtype=np.float64)

    global_fp = "global-pred-fp"
    override_fp = "override-pred-fp"
    dataset_fp = "ds-fp-001"

    state = VisualizationState(view_id="v1")
    state.dataset_ref = dataset_fp
    state.prediction_ref = global_fp   # the view's overlay prediction
    state.parameters["ffast.atom_color"] = {
        "source": "metric:ffast.force_mae",
        "prediction_ref": override_fp,   # coloring explicitly asks for a different model
    }

    calls = []

    def get_prediction(ds_fp, model_fp):
        calls.append(model_fp)
        if ds_fp == dataset_fp and model_fp == override_fp:
            return _PredictionView((ref_forces + 5.0)[np.newaxis])
        if ds_fp == dataset_fp and model_fp == global_fp:
            return _PredictionView(ref_forces[np.newaxis])
        return None

    result = resolve_atom_color_values(
        state, ds, 0, np.zeros((n_atoms, 3)), ds._z, get_prediction,
        executor=InProcessExecutor(reg),
    )

    assert result is not None
    assert override_fp in calls and global_fp not in calls
    values, _label, _unit = result
    assert np.all(values > 0)   # resolved from the override prediction's nonzero forces


def test_resolve_color_no_prediction_ref_returns_none():
    """Without prediction_ref, metric coloring falls back to None (element colors)."""
    from ffast.metrics.builtin import force_metrics  # noqa: F401
    from ffast.metrics.executor import InProcessExecutor
    from ffast.metrics.registry import _default_registry as reg
    from ffast.visualization.color_values import resolve_atom_color_values

    state = VisualizationState(view_id="v1")
    state.dataset_ref = "ds-fp-001"
    state.prediction_ref = None  # not set — the critical case
    state.parameters["ffast.atom_color"] = {"source": "metric:ffast.force_mae"}

    ds = _FakeDataset(n_atoms=3)

    result = resolve_atom_color_values(
        state, ds, 0, np.zeros((3, 3)), ds._z, lambda *a: None,
        executor=InProcessExecutor(reg),
    )
    assert result is None, "Should fall back to None when prediction_ref is absent"


def test_resolve_color_prediction_lookup_failure_returns_none():
    """If get_prediction returns None (server cache miss), graceful fallback."""
    from ffast.metrics.builtin import force_metrics  # noqa: F401
    from ffast.metrics.executor import InProcessExecutor
    from ffast.metrics.registry import _default_registry as reg
    from ffast.visualization.color_values import resolve_atom_color_values

    state = _make_state_with_metric("ffast.force_mae")
    ds = _FakeDataset(n_atoms=3)

    result = resolve_atom_color_values(
        state, ds, 0, np.zeros((3, 3)), ds._z, lambda *a: None,
        executor=InProcessExecutor(reg),
    )
    assert result is None, "Cache miss should return None, not raise"


def test_resolve_color_element_source_returns_none():
    """Source='element' is the no-op sentinel; must return None."""
    from ffast.visualization.color_values import resolve_atom_color_values

    state = VisualizationState(view_id="v1")
    state.parameters["ffast.atom_color"] = {"source": "element"}
    ds = _FakeDataset(n_atoms=3)

    result = resolve_atom_color_values(state, ds, 0, np.zeros((3, 3)), ds._z, None)
    assert result is None


def test_resolve_color_unknown_metric_returns_none():
    """Unregistered metric ID must not raise, just return None."""
    from ffast.visualization.color_values import resolve_atom_color_values

    state = _make_state_with_metric("ffast.nonexistent_metric")
    ds = _FakeDataset(n_atoms=3)

    result = resolve_atom_color_values(state, ds, 0, np.zeros((3, 3)), ds._z, None)
    assert result is None


def test_resolve_color_displacement_returns_per_atom_rms():
    """source='displacement' → per-atom RMS displacement over the trajectory,
    no metric/executor involved (pure-array path, no OpenGL)."""
    from ffast.visualization.color_values import resolve_atom_color_values

    ds = _FakeDataset(n_atoms=3, n_frames=2)
    ds._pos = np.zeros((2, 3, 3), dtype=np.float64)
    ds._pos[1, 0] = [1.0, 1.0, 1.0]  # only atom 0 moves between the two frames

    state = VisualizationState(view_id="v1")
    state.parameters["ffast.atom_color"] = {"source": "displacement"}

    result = resolve_atom_color_values(state, ds, 0, ds._pos[0], ds._z, None)
    assert result is not None
    values, label, unit = result
    assert (label, unit) == ("displacement", "Å")
    assert values.shape == (3,)
    assert values[0] == pytest.approx(1.0)         # sqrt(mean([1,1,1]**2)) = 1
    assert values[1] == 0.0 and values[2] == 0.0   # stationary atoms
