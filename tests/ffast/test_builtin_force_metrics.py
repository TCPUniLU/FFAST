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


# ── Force parity plot: density of every component + the worst as dots ───────
# 2026-11-02: plotting every component sent >4 GB for 1.77M structures.

from ffast.metrics.builtin.force_metrics import parity_density, worst_components  # noqa: E402


def test_parity_density_counts_every_finite_component_on_a_square_grid():
    true = np.array([[0.0, 1.0, 2.0], [3.0, np.nan, 4.0]])
    pred = np.array([[0.0, 1.0, 2.0], [3.0, 1.0, 0.0]])
    grid = parity_density(true, pred, bins=4)
    assert grid.shape == (5, 4)
    centres, counts = grid[0], grid[1:]
    np.testing.assert_allclose(centres, [0.5, 1.5, 2.5, 3.5])   # over 0..4, both axes
    assert counts.sum() == 5                                    # the NaN pair is left out
    # counts[row = predicted bin][column = true bin]: true 4, predicted 0
    assert counts[0, 3] == 1
    assert np.trace(counts) == 4                                # the rest lie on the diagonal


def test_worst_components_name_their_structure_and_atom_worst_first():
    # Structures of 1 and 2 atoms; one component of each atom is off.
    offsets = np.array([0, 1, 3])
    true = np.zeros((3, 3))
    pred = np.zeros((3, 3))
    pred[0, 2] = 0.5      # structure 0, atom 0, z
    pred[2, 0] = -3.0     # structure 1, atom 1, x
    pred[1, 1] = 1.0      # structure 1, atom 0, y
    rows = worst_components(true, pred, offsets, k=2, chunk=4)
    # columns: true, predicted, structure, atom, component
    np.testing.assert_array_equal(rows, [[0.0, -3.0, 1, 1, 0],
                                         [0.0, 1.0, 1, 0, 1]])


def test_worst_components_returns_all_when_fewer_than_asked():
    rows = worst_components(np.zeros((1, 3)), np.array([[1.0, np.nan, 2.0]]),
                            np.array([0, 1]), k=10)
    np.testing.assert_array_equal(rows[:, 4], [2, 0])           # the NaN is left out


# ── Parallel chunks give the serial answer (ADR 0079 decision 4) ─────────────
# Reference copies of the single-thread versions, from before the chunk pool.

from ffast.metrics.builtin.force_metrics import _components, framesOrOne  # noqa: E402


def _serial_finite_pairs(t, p, chunk):
    for start in range(0, t.size, chunk):
        a, b = t[start:start + chunk], p[start:start + chunk]
        keep = np.isfinite(a) & np.isfinite(b)
        yield a[keep], b[keep]


def _serial_parity_density(true, pred, bins=300, chunk=10_000_000):
    t, p = _components(true, pred)
    lo, hi = np.inf, -np.inf
    for a, b in _serial_finite_pairs(t, p, chunk):
        if a.size:
            lo = min(lo, a.min(), b.min())
            hi = max(hi, a.max(), b.max())
    if not np.isfinite(lo):
        lo, hi = 0.0, 1.0
    if hi <= lo:
        hi = lo + 1.0
    width = (hi - lo) / bins
    counts = np.zeros(bins * bins, dtype=np.int64)
    for a, b in _serial_finite_pairs(t, p, chunk):
        col = np.minimum(((a - lo) / width).astype(np.int64), bins - 1)
        row = np.minimum(((b - lo) / width).astype(np.int64), bins - 1)
        counts += np.bincount(row * bins + col, minlength=bins * bins)
    grid = np.empty((bins + 1, bins))
    grid[0] = lo + (np.arange(bins) + 0.5) * width
    grid[1:] = counts.reshape(bins, bins)
    return grid


def _serial_worst_components(true, pred, offsets, k=50_000, chunk=10_000_000):
    t, p = _components(true, pred)
    picked, errors = [], []
    for start in range(0, t.size, chunk):
        err = np.abs(p[start:start + chunk] - t[start:start + chunk])
        err[~np.isfinite(err)] = -1.0
        idx = np.argpartition(-err, k - 1)[:k] if err.size > k else np.arange(err.size)
        idx = idx[err[idx] >= 0]
        picked.append(idx + start)
        errors.append(err[idx])
    idx = np.concatenate(picked) if picked else np.zeros(0, dtype=np.int64)
    err = np.concatenate(errors) if errors else np.zeros(0)
    if idx.size > k:
        keep = np.argpartition(-err, k - 1)[:k]
        idx, err = idx[keep], err[keep]
    idx = idx[np.argsort(-err, kind="stable")]
    atom = idx // 3
    starts = framesOrOne(offsets, range(t.size // 3))
    frame = np.searchsorted(starts[1:], atom, side="right")
    return np.column_stack([t[idx], p[idx], frame, atom - starts[frame], idx % 3]).astype(float)


def _noisy_forces():
    rng = np.random.default_rng(7)
    n = 2_345_678 // 3 * 3 // 3
    true = rng.normal(size=(n, 3))
    pred = true + rng.normal(scale=0.3, size=(n, 3))
    true[rng.random(true.shape) < 0.01] = np.nan
    pred[rng.random(pred.shape) < 0.01] = np.nan
    return true, pred


def test_parallel_parity_density_equals_the_serial_one():
    true, pred = _noisy_forces()
    assert np.array_equal(parity_density(true, pred, chunk=100_000),
                          _serial_parity_density(true, pred, chunk=100_000))


def test_parallel_worst_components_equal_the_serial_ones():
    true, pred = _noisy_forces()
    offsets = np.arange(0, true.shape[0] + 1, 4)
    offsets[-1] = true.shape[0]
    assert np.array_equal(worst_components(true, pred, offsets, k=1000, chunk=100_000),
                          _serial_worst_components(true, pred, offsets, k=1000, chunk=100_000))


def test_all_nan_and_empty_input_match_the_serial_answer():
    nan = np.full((5, 3), np.nan)
    assert np.array_equal(parity_density(nan, nan), _serial_parity_density(nan, nan))
    empty = np.zeros((0, 3))
    assert np.array_equal(parity_density(empty, empty), _serial_parity_density(empty, empty))
    offsets = np.array([0, 5])
    assert np.array_equal(worst_components(nan, nan, offsets, k=3),
                          _serial_worst_components(nan, nan, offsets, k=3))


def test_map_chunks_keeps_the_order_of_the_starts():
    import time

    from ffast.metrics.resources import map_chunks

    def slow_first(i):
        time.sleep(0.1 * (4 - i))
        return i

    assert map_chunks(slow_first, [0, 1, 2, 3]) == [0, 1, 2, 3]


def test_map_chunks_stops_when_the_data_is_deleted():
    import time

    import pytest

    from ffast.metrics import stop
    from ffast.metrics.resources import map_chunks

    ran = []

    def work(i):
        ran.append(i)
        time.sleep(0.05)

    token = stop.running_under(lambda: True)
    try:
        with pytest.raises(stop.Stopped):
            map_chunks(work, list(range(500)))
    finally:
        stop.reset(token)
    time.sleep(0.3)
    assert len(ran) < 500
