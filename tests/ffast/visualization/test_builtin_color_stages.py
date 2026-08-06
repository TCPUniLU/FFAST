import numpy as np
import pytest
from ffast.visualization.stages.builtin.color_stages import displacement_stats


# --- displacement_stats ---

def test_displacement_stats_shapes():
    traj = np.random.randn(20, 10, 3)
    dTot, dMean = displacement_stats(traj)
    assert dTot.shape == (10,)
    assert dMean.shape == (10,)


def test_displacement_stats_zero_trajectory():
    traj = np.ones((10, 5, 3))   # all atoms stationary
    dTot, dMean = displacement_stats(traj)
    assert np.allclose(dTot, 0.0)
    assert np.allclose(dMean, 0.0)


def test_displacement_stats_dtot_first_to_last():
    # one atom, moves 3 units along x from frame 0 to last frame
    n_frames, n_atoms = 5, 1
    traj = np.zeros((n_frames, n_atoms, 3))
    traj[-1, 0, 0] = 3.0
    dTot, _ = displacement_stats(traj)
    assert np.isclose(dTot[0], np.sqrt(3.0))


def test_displacement_stats_dmean_step_average():
    # 3 frames, 1 atom, step size 1 along x each frame
    traj = np.array([[[0.0, 0, 0]], [[1.0, 0, 0]], [[2.0, 0, 0]]])
    _, dMean = displacement_stats(traj)
    assert np.isclose(dMean[0], np.sqrt(1.0 / 3.0))


def test_displacement_stats_nonnegative():
    traj = np.random.randn(15, 8, 3)
    dTot, dMean = displacement_stats(traj)
    assert np.all(dTot >= 0.0)
    assert np.all(dMean >= 0.0)


def test_displacement_stats_single_step():
    traj = np.array([[[0.0, 0, 0], [1.0, 0, 0]], [[1.0, 0, 0], [2.0, 0, 0]]])
    dTot, dMean = displacement_stats(traj)
    assert dTot.shape == (2,)
    assert dMean.shape == (2,)


def test_displacement_stats_per_atom_independence():
    # atom 0 is stationary, atom 1 moves
    traj = np.zeros((5, 2, 3))
    traj[:, 1, 0] = np.arange(5, dtype=float)   # atom 1 moves along x
    dTot, dMean = displacement_stats(traj)
    assert np.isclose(dTot[0], 0.0)
    assert dTot[1] > 0.0


def test_displacement_stats_registered():
    from ffast.visualization.stages.registry import _default_registry
    _, fn = _default_registry.get("ffast.displacement_stats")
    assert fn is displacement_stats


# --- value_colors (requires vispy — skip if unavailable) ---

