"""Opt-in plot repaint profiler (diagnostic only).

Pan/zoom lag on many-panel tabs is a pyqtgraph *repaint* cost, not a cost of
any application-level handler -- so to find the real bottleneck we time the two
graphics items that actually rasterise pixels: ``ScatterPlotItem`` (the atom
clouds) and ``PlotCurveItem`` (the AA lines). Each ``paint()`` is wrapped to
accumulate wall time, call count, and point count, flushed to the FFAST log
about once a second while painting is happening.

Enabled ONLY when ``FFAST_PLOT_PROFILE`` is set in the environment, so it adds
zero overhead to a normal run. Turn it on, pan/zoom the laggy tab, and read the
per-second lines: they say directly whether scatter re-rasterisation on zoom,
line re-strokes on scroll, or sheer point count is where the frame time goes.

    FFAST_PLOT_PROFILE=1 <launch command>

Interpreting a line like::

    [plot-profile] 1.03s: scatter 58 paints 742.1ms (max 31.4ms, 240k pts) | curve 12 paints 9.2ms

means scatter painting alone burned 742ms of that ~1s window -- the frame
budget is gone to re-rasterising the clouds, so the fix lives in the scatter
cache/point-count levers, not in reducing redraw *frequency*.
"""
import logging
import os
import time

logger = logging.getLogger("FFAST")

_installed = False
_prof = None


def note_refresh(kind):
    """Record a plot refresh (kind='request' or 'rebuild'); no-op if profiling
    is off. Called from BasicPlotWidget's refresh path so the profiler line
    shows the actual per-second rebuild rate alongside paint cost."""
    if _prof is not None:
        _prof.note_refresh(kind)


class _Bucket:
    __slots__ = ("count", "total", "peak", "points")

    def __init__(self):
        self.count = 0
        self.total = 0.0
        self.peak = 0.0
        self.points = 0

    def add(self, dt, npts):
        self.count += 1
        self.total += dt
        if dt > self.peak:
            self.peak = dt
        self.points = npts

    def reset(self):
        self.count = 0
        self.total = 0.0
        self.peak = 0.0


class _Profiler:
    # Report window; paint calls are cheap individually, the story is how much
    # they add up to per wall-clock second of interaction.
    WINDOW_S = 1.0

    def __init__(self):
        self.scatter = _Bucket()
        self.curve = _Bucket()
        self.refresh_req = 0    # visualRefresh() calls (timer restarts)
        self.refresh_done = 0   # _performVisualRefresh() actual rebuilds
        self._last_flush = time.perf_counter()

    def record(self, which, dt, npts):
        bucket = self.scatter if which == "scatter" else self.curve
        bucket.add(dt, npts)
        self._maybe_flush()

    def note_refresh(self, kind):
        if kind == "request":
            self.refresh_req += 1
        else:
            self.refresh_done += 1
        self._maybe_flush()

    def _maybe_flush(self):
        now = time.perf_counter()
        elapsed = now - self._last_flush
        active = (self.scatter.count or self.curve.count
                  or self.refresh_req or self.refresh_done)
        if elapsed >= self.WINDOW_S and active:
            self._flush(elapsed)
            self._last_flush = now

    def _flush(self, elapsed):
        s, c = self.scatter, self.curve
        logger.info(
            "[plot-profile] %.2fs: scatter %d paints %.1fms (max %.1fms, %s pts) "
            "| curve %d paints %.1fms (max %.1fms, %s pts) "
            "| refresh req=%d rebuilt=%d",
            elapsed,
            s.count, s.total * 1e3, s.peak * 1e3, _fmt_pts(s.points),
            c.count, c.total * 1e3, c.peak * 1e3, _fmt_pts(c.points),
            self.refresh_req, self.refresh_done,
        )
        s.reset()
        c.reset()
        self.refresh_req = 0
        self.refresh_done = 0


def _fmt_pts(n):
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def maybe_enable():
    """Wrap ScatterPlotItem/PlotCurveItem paint if FFAST_PLOT_PROFILE is set.

    Idempotent and no-op when the env var is unset -- safe to call at import."""
    global _installed, _prof
    if _installed or not os.environ.get("FFAST_PLOT_PROFILE"):
        return
    _installed = True

    import pyqtgraph

    prof = _Profiler()
    _prof = prof

    def _wrap(cls, which, count_points):
        orig = cls.paint

        def paint(self, *args, **kwargs):
            t = time.perf_counter()
            try:
                return orig(self, *args, **kwargs)
            finally:
                prof.record(which, time.perf_counter() - t, count_points(self))

        cls.paint = paint

    def _scatter_pts(item):
        data = getattr(item, "data", None)
        try:
            return len(data)
        except Exception:
            return 0

    def _curve_pts(item):
        x = getattr(item, "xData", None)
        try:
            return len(x)
        except Exception:
            return 0

    _wrap(pyqtgraph.ScatterPlotItem, "scatter", _scatter_pts)
    _wrap(pyqtgraph.PlotCurveItem, "curve", _curve_pts)
    logger.info("[plot-profile] enabled (FFAST_PLOT_PROFILE set)")
