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

def test_value_colors_shape():
    pytest.importorskip("vispy")
    from ffast.visualization.stages.builtin.color_stages import value_colors

    values = np.array([0.0, 0.5, 1.0])
    colors = value_colors(values, vmin=0.0, vmax=1.0)
    assert colors.shape == (3, 4)
    assert colors.dtype == np.float32


def test_value_colors_range():
    pytest.importorskip("vispy")
    from ffast.visualization.stages.builtin.color_stages import value_colors

    values = np.linspace(0, 1, 10)
    colors = value_colors(values, vmin=0.0, vmax=1.0)
    assert np.all(colors >= 0.0)
    assert np.all(colors <= 1.0)


def test_value_colors_auto_range():
    pytest.importorskip("vispy")
    from ffast.visualization.stages.builtin.color_stages import value_colors

    values = np.array([2.0, 4.0, 6.0])
    colors_auto = value_colors(values)
    colors_explicit = value_colors(values, vmin=2.0, vmax=6.0)
    assert np.allclose(colors_auto, colors_explicit)


def test_value_colors_constant_input():
    pytest.importorskip("vispy")
    from ffast.visualization.stages.builtin.color_stages import value_colors
    from vispy.color import get_colormap

    values = np.ones(5) * 3.0
    colors = value_colors(values, vmin=0.0, vmax=1.0)
    assert colors.shape == (5, 4)
    # value (3.0) is above vmax (1.0), so normalized = clip((3-0)/(1-0), 0, 1)
    # == 1.0 for every atom: every row must equal the colormap's endpoint
    # color exactly (independently computed via vispy's real "inferno"
    # colormap — the default used by value_colors — not re-derived logic).
    expected = get_colormap("inferno")[np.float32(1.0)].rgba
    assert np.allclose(colors, expected)


def test_value_colors_force_error_colormap_maps_gradient_endpoints():
    # The "force_error" branch of _get_colormap builds a custom blue→…→red
    # gradient (NOT a vispy builtin), so it must be exercised separately from the
    # named-colormap path. Its control endpoints are blue (0.1,0.1,0.9) at the
    # minimum and red (0.9,0.1,0.1) at the maximum.
    pytest.importorskip("vispy")
    from ffast.visualization.stages.builtin.color_stages import value_colors

    colors = value_colors(
        np.array([0.0, 10.0]), vmin=0.0, vmax=10.0, colormap="force_error"
    )
    assert np.allclose(colors[0, :3], [0.1, 0.1, 0.9], atol=1e-6)   # low → blue
    assert np.allclose(colors[-1, :3], [0.9, 0.1, 0.1], atol=1e-6)  # high → red
    assert np.allclose(colors[:, 3], 1.0)
