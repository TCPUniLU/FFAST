import numpy as np
from ffast.metrics.builtin.force_metrics import force_difference

# Force MAE, RMSE and the rest are box settings of ffast.force_difference now
# (ADR 0065); tests/ffast/test_retired_metrics.py checks their numbers.


def test_builtin_registered_under_expected_ids():
    from ffast.metrics.registry import _default_registry
    for metric_id in [
        "ffast.force_difference",
        "ffast.force_net_residual",
    ]:
        decl, _ = _default_registry.get(metric_id)
        assert decl.id == metric_id


def test_force_difference_shape():
    reference = np.zeros((2, 3, 3))
    predicted = np.ones((2, 3, 3))
    diff = force_difference(reference, predicted)
    assert diff.shape == (2, 3, 3)
    assert np.all(diff == 1.0)


# ── Degenerate scientific inputs ──────────────────────────────────────────────

def test_force_difference_single_atom_single_frame():
    reference = np.array([[[1.0, 0.0, 0.0]]])   # (1 frame, 1 atom, 3)
    predicted = np.array([[[2.0, 0.0, 0.0]]])
    diff = force_difference(reference, predicted)
    assert diff.shape == (1, 1, 3)
    assert np.allclose(diff, [[[1.0, 0.0, 0.0]]])
