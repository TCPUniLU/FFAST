"""A gap ("no value", NaN) never becomes a number in a transform (ADR 0064
step 3). The test loops over the transform registry, so a transform added
later without a no-value rule fails it."""
import numpy as np
import pytest

from ffast.metrics.transforms import TRANSFORMS, _smooth

GAPS = [1, 4, 5]
SOURCE = np.array([0.3, np.nan, -1.2, 2.0, np.nan, np.nan, 0.7, 1.1, -0.4, 0.9])
CLEAN = SOURCE[np.isfinite(SOURCE)]
EXTRA = {"shift": 0.5}


def _run(transform, values):
    params = {k: spec["default"] for k, spec in transform.compute_params.items()}
    return np.asarray(transform.fn(values, EXTRA, params), dtype=np.float64)


@pytest.mark.parametrize("name", sorted(TRANSFORMS))
def test_a_gap_is_left_out_never_made_a_number(name):
    transform = TRANSFORMS[name]
    out = _run(transform, SOURCE)
    assert not np.isinf(out).any()
    if out.shape == SOURCE.shape:
        # Value by value: the gap stays a gap, in its place.
        assert np.flatnonzero(np.isnan(out)).tolist() == GAPS
    else:
        # A curve or one number: the same as if the gaps were not there.
        np.testing.assert_allclose(out, _run(transform, CLEAN))
        assert np.isfinite(out).all()


def test_smoothing_keeps_each_gap_in_its_place():
    np.testing.assert_allclose(_smooth([1.0, np.nan, 3.0, 5.0], 2), [1.0, 3.0, 4.0])
    out = _smooth([1.0, np.nan, np.nan, 5.0], 2)
    assert np.isnan(out[1]) and out[0] == 1.0 and out[2] == 5.0


def test_a_summary_of_only_gaps_has_no_value():
    out = _run(TRANSFORMS["rmse"], np.array([np.nan, np.nan]))
    assert np.isnan(out)
