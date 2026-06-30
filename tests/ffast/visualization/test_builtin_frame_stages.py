import numpy as np

from ffast.visualization.stages.builtin.frame_stages import frame


def test_frame_selects_by_index():
    traj = np.array([
        [[0.0, 0.0, 0.0]],
        [[1.0, 0.0, 0.0]],
        [[2.0, 0.0, 0.0]],
    ])
    assert np.allclose(frame(traj, index=2), [[2.0, 0.0, 0.0]])


def test_frame_default_index_zero():
    traj = np.array([[[5.0, 0.0, 0.0]], [[9.0, 0.0, 0.0]]])
    assert np.allclose(frame(traj), [[5.0, 0.0, 0.0]])


def test_frame_index_clamped_high():
    traj = np.array([[[0.0, 0.0, 0.0]], [[1.0, 0.0, 0.0]]])
    assert np.allclose(frame(traj, index=99), [[1.0, 0.0, 0.0]])


def test_frame_index_clamped_low():
    traj = np.array([[[0.0, 0.0, 0.0]], [[1.0, 0.0, 0.0]]])
    assert np.allclose(frame(traj, index=-5), [[0.0, 0.0, 0.0]])


def test_frame_single_structure_passthrough():
    single = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])  # (N,3)
    assert np.allclose(frame(single, index=3), single)


def test_frame_float_index_truncated():
    traj = np.array([[[0.0, 0.0, 0.0]], [[1.0, 0.0, 0.0]], [[2.0, 0.0, 0.0]]])
    assert np.allclose(frame(traj, index=1.9), [[1.0, 0.0, 0.0]])
