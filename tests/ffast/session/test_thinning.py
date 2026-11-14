"""The server's thinned view keeps what a plot must show (ADR 0099)."""
from __future__ import annotations

import numpy as np

from ffast.session.thinning import THIN_ABOVE, thin_line, thin_points


def _line(n=200_000):
    xs = np.arange(n, dtype=float)
    ys = np.sin(xs / 5000)
    ys[123457], ys[7771], ys[150001] = 50, -50, np.nan
    return xs, ys


def test_a_thinned_line_keeps_every_peak_dip_gap_and_end():
    xs, ys = _line()
    idx = thin_line(xs, ys, None, 700)
    assert len(idx) <= 4 * 700
    assert {123457, 7771, 150001, 0, len(xs) - 1} <= set(idx.tolist())
    assert np.all(np.diff(idx) > 0)


def test_a_zoomed_line_keeps_the_point_beyond_each_edge():
    xs, ys = _line()
    small = thin_line(xs, ys, (1000, 1100), 700).tolist()
    assert set(range(999, 1102)) <= set(small)          # few enough: all of them
    wide = thin_line(xs, ys, (100_000, 140_000), 700).tolist()
    assert 99_999 in wide and 140_001 in wide           # the line runs to both borders


def test_a_short_or_unordered_line_is_sent_whole():
    xs = np.arange(THIN_ABOVE, dtype=float)
    assert thin_line(xs, np.sin(xs), None, 700) is None
    xs, ys = _line()
    assert thin_line(xs[::-1].copy(), ys, None, 700) is None


def test_thinned_dots_keep_one_per_cell_and_a_lone_outlier():
    n = 300_000
    i = np.arange(n)
    xs = (i % 1000) / 1000
    ys = xs + ((i * 7919) % 100) / 10000
    xs[4242], ys[4242] = 0.5, 3.0
    idx = thin_points(xs, ys, None, None, 700, 300)
    assert len(idx) <= 350 * 150
    assert 4242 in idx.tolist()
    zoom = thin_points(xs, ys, (0.2, 0.3), (0.2, 0.3), 700, 300)
    assert len(zoom) > len(idx)                          # finer where it is shown
