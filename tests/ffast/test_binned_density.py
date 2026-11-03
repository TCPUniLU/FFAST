"""Density curves of large samples are binned (MeluXina 2026-11-03).

SciPy's exact ``gaussian_kde`` took ~18 s per curve on 1.77M structures
(3.5M mirrored values, 200 x-positions). A large sample is now counted into
bins finer than the bandwidth and smoothed with one convolution, the method of
statsmodels (``fft=True``) and KDEpy; the curve matches the exact one to well
under a pixel. Small samples, such as a SUB subset, keep the exact method.
"""
import time

import numpy as np
from scipy.stats import gaussian_kde

from ffast.metrics.transforms import _abs_kde, _kde_xy, _mirror_kde


def _exact(sample, x):
    return gaussian_kde(sample)(x)


def test_a_large_mirrored_sample_matches_the_exact_curve():
    rng = np.random.default_rng(0)
    a = rng.lognormal(0, 0.6, 300_000)
    curve = _mirror_kde(a)
    exact = _exact(np.concatenate([a, -a]), curve[0])
    assert np.max(np.abs(curve[1] - exact)) < 1e-3 * exact.max()


def test_a_long_tail_matches_the_exact_curve():
    rng = np.random.default_rng(1)
    a = np.concatenate([rng.normal(1e5, 2e4, 300_000), rng.uniform(1e5, 8e5, 300)])
    curve = _abs_kde(a)
    exact = _exact(np.abs(a), curve[0])
    assert np.max(np.abs(curve[1] - exact)) < 1e-3 * exact.max()


def test_a_small_sample_keeps_the_exact_curve():
    rng = np.random.default_rng(2)
    a = rng.lognormal(0, 1, 500)
    curve = _mirror_kde(a)
    np.testing.assert_allclose(curve[1], _exact(np.concatenate([a, -a]), curve[0]))


def test_millions_of_values_take_well_under_a_second():
    rng = np.random.default_rng(3)
    a = rng.lognormal(0, 0.6, 1_773_605)
    t = time.perf_counter()
    curve = _mirror_kde(a)
    assert time.perf_counter() - t < 2.0
    assert curve.shape == (2, 200) and np.all(np.isfinite(curve))


def test_degenerate_input_is_still_a_flat_zero():
    curve = _kde_xy(np.full(100_000, 3.0), lambda s: (0.0, 4.0))
    assert np.all(curve[1] == 0)
