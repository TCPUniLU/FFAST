"""Transform-Metric compiler (ADR 0021, Phase 5).

A **Transform** is a reduction (smoothing, KDE density, per-structure reduction)
described once in a catalog. The **compiler** turns a Panel's
``{metric, transform, params}`` into a deterministically-named concrete
**Metric** registered in the default registry, with a static **Metric Graph**
edge to its source (Model A — compile-to-concrete, ADR 0021). This replaces
hand-written transform metrics as the authoring path: a Panel binds
``{metric, transform}`` and the compiler emits the metric so the server computes
it and the client only draws the result.

Identity rules (locked in the Phase-5 plan):

* Concrete id = ``{source}__{transform}`` (a pipeline chains ``__t1__t2``), with
  an 8-char param-hash suffix ``__p<hash>`` appended only when a step carries
  *identity* params. **Compute** params (``window``, ``shifted``, ...) are NOT in
  the id — they ride the cache key exactly as the Phase-0/4 metrics already do.
* For a pipeline the *final* metric declares the **union** of every step's
  compute params, so the Panel surfaces one control per knob and the client
  cache key folds them all; each intermediate declares only its own.
* Compilation is idempotent: an already-registered id is returned unchanged, so
  the same compile pass can run on server, client, and headless thread.

The reduction bodies (``_smooth``, ``_mirror_kde``, ...) live here so the metrics
package stays self-contained; ``builtin/transform_metrics.py`` imports them to
keep its literal Phase-0/4 ids.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ffast.metrics import dims, units  # noqa: F401  (units re-exported for callers)
from ffast.metrics.registry import default_registry


# --------------------------------------------------------------------------- #
# Shared reduction bodies (self-contained; transform_metrics.py imports these)
# --------------------------------------------------------------------------- #
def _smooth(values, window):
    """Sliding-average over a 1-D per-frame series (legacy ``np.convolve(...,
    "valid")``). Window is clamped to the series length, so an over-large window
    degrades to a single mean rather than emitting nothing."""
    v = np.asarray(values, dtype=np.float64).ravel()
    w = int(max(1, min(window, len(v)))) if len(v) else 1
    if w <= 1:
        return v
    return np.convolve(v, np.ones(w) / w, mode="valid")


def _kde_xy(sample, bounds, n_pts=200):
    """KDE of ``sample`` over ``bounds(sample) -> (lo, hi)`` as a (2, G) array
    (row 0 x-grid, row 1 density). Degenerate input (empty / <2 pts / ~constant)
    returns a flat-zero density rather than raising (matches the Phase-0 mirror
    KDE's degenerate branch)."""
    from scipy.stats import gaussian_kde

    sample = np.asarray(sample, dtype=np.float64).ravel()
    if sample.size < 2 or np.std(sample) < 1e-10:
        top = max(float(np.max(sample)), 1e-10) if sample.size else 1.0
        x = np.linspace(0.0, top, n_pts)
        return np.vstack([x, np.zeros_like(x)])
    lo, hi = bounds(sample)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = 0.0, 1.0
    x = np.linspace(lo, hi, n_pts)
    return np.vstack([x, gaussian_kde(sample)(x)])


def _mirror_kde(values):
    """Symmetric-mirror KDE of ``|values|`` over ``[0, max·1.05]`` — the
    error-distribution shape (mirror about zero so it starts at 0)."""
    a = np.abs(np.asarray(values, dtype=np.float64).ravel())
    sample = np.concatenate([a, -a]) if a.size else np.array([0.0, 0.0])
    return _kde_xy(sample, lambda s: (0.0, float(np.max(np.abs(s))) * 1.05))


def _abs_kde(values):
    """KDE of ``|values|`` (not mirrored) over ``[min·0.95, max·1.05]`` — the
    per-element atomic-error distribution shape."""
    a = np.abs(np.asarray(values, dtype=np.float64).ravel())
    return _kde_xy(a, lambda s: (float(np.min(s)) * 0.95, float(np.max(s)) * 1.05))


def _value_kde(values):
    """KDE of raw ``values`` over ``[min, max]`` padded 5% each side — the
    distribution shape for non-error quantities (e.g. gyration radius)."""

    def bounds(s):
        delta = float(np.max(s) - np.min(s))
        return (float(np.min(s)) - delta * 0.05, float(np.max(s)) + delta * 0.05)

    return _kde_xy(np.asarray(values, dtype=np.float64).ravel(), bounds)


# --------------------------------------------------------------------------- #
# Transform catalog
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Transform:
    """One reduction archetype.

    ``fn(src, extra, params)`` receives the source values, a dict of resolved
    ``extra_inputs`` values, and a dict of this transform's compute-param values.
    ``shape`` / ``unit`` are ``"inherit"`` (take the source's) or an explicit
    Dim-tuple / units constant. ``compute_params`` are ParameterSchema dicts
    (interactive, fold into the cache key); ``extra_inputs`` maps an input slot to
    a concrete metric id (e.g. ``shift`` → ``ffast.energy_shift``)."""

    name: str
    fn: Callable[[Any, dict, dict], Any]
    shape: Any = "inherit"
    unit: Any = "inherit"
    compute_params: dict = field(default_factory=dict)
    extra_inputs: dict = field(default_factory=dict)
    label: str = ""


TRANSFORMS: dict[str, Transform] = {}


def register_transform(t: Transform) -> Transform:
    """Register a Transform in the catalog (the reduction analog of
    ``register_panel_kind``)."""
    TRANSFORMS[t.name] = t
    return t


# Compute-param schemas reused across transforms (mirror the Phase-0/4 ones).
_WINDOW = {"type": "int", "default": 1, "min": 1, "max": 10000, "role": "compute",
           "label": "Smoothing", "description": "Sliding-average window (frames)."}
_SHIFTED = {"type": "bool", "default": False, "role": "compute", "label": "Shift",
            "description": "Subtract the mean energy offset mean(E_pred − E_true)."}

_DENSITY_SHAPE = (dims.curve_xy, dims.grid)
_SCALAR_SHAPE = (dims.scalar,)


def _f(arr):
    return np.asarray(arr, dtype=np.float64)


# Transform bodies are module-level (not lambdas) so a compiled transform metric
# — and therefore the whole registry — stays picklable for the metric worker pool
# (pool.py ships the registry to a subprocess). A lambda or local closure here
# breaks ``pickle.dumps(registry)`` with "Can't pickle local object".
def _tb_smooth(s, e, p): return _smooth(s, p.get("window", 1))
def _tb_abs(s, e, p): return np.abs(_f(s))
def _tb_mirror_kde(s, e, p): return _mirror_kde(s)
def _tb_abs_kde(s, e, p): return _abs_kde(s)
def _tb_value_kde(s, e, p): return _value_kde(s)
def _tb_mean_abs(s, e, p): return float(np.mean(np.abs(_f(s))))
def _tb_rmse(s, e, p): return float(np.sqrt(np.mean(_f(s) ** 2)))
def _tb_shift(s, e, p):
    return _f(s) - (float(np.asarray(e["shift"])) if p.get("shifted") else 0.0)


for _t in (
    Transform("smooth", _tb_smooth, compute_params={"window": _WINDOW}),
    Transform("abs", _tb_abs),
    Transform("mirror_kde", _tb_mirror_kde, shape=_DENSITY_SHAPE),
    Transform("abs_kde", _tb_abs_kde, shape=_DENSITY_SHAPE),
    Transform("value_kde", _tb_value_kde, shape=_DENSITY_SHAPE),
    Transform("mean_abs", _tb_mean_abs, shape=_SCALAR_SHAPE),
    Transform("rmse", _tb_rmse, shape=_SCALAR_SHAPE),
    Transform("shift", _tb_shift,
              extra_inputs={"shift": "ffast.energy_shift"},
              compute_params={"shifted": _SHIFTED}),
):
    register_transform(_t)


# --------------------------------------------------------------------------- #
# Compiler
# --------------------------------------------------------------------------- #
def _id_suffix(ident_params: dict) -> str:
    if not ident_params:
        return ""
    h = hashlib.md5(json.dumps(ident_params, sort_keys=True).encode()).hexdigest()[:8]
    return f"__p{h}"


class _TransformFn:
    """Picklable wrapper adapting a Transform to the registry's calling
    convention: the executor calls ``fn(**resolved_inputs, **compute_params)``,
    so this splits those back into the ``(src, extra, params)`` the Transform body
    expects. A module-level class (not a local closure) so a compiled transform
    metric — and the whole registry — pickles to the metric worker pool (pool.py).
    """

    def __init__(self, transform: Transform):
        self.transform = transform
        self.extra_keys = tuple(transform.extra_inputs.keys())
        self.param_keys = tuple(transform.compute_params.keys())

    def __call__(self, src, **kwargs):
        extra = {k: kwargs.get(k) for k in self.extra_keys}
        params = {k: kwargs.get(k) for k in self.param_keys}
        return self.transform.fn(src, extra, params)

    def implementation_source(self) -> str:
        """Stable source description for ``function_hash`` (instances have no
        retrievable source). Combines the wrapper and the transform body so cache
        keys invalidate when either changes."""
        import inspect
        return (
            f"_TransformFn:{self.transform.name}:"
            + inspect.getsource(type(self).__call__)
            + inspect.getsource(self.transform.fn)
        )

    def __repr__(self) -> str:
        return f"_TransformFn({self.transform.name!r})"


def _make_fn(transform: Transform) -> Callable:
    return _TransformFn(transform)


def _register_compiled(registry, cid: str, source_id: str, transform: Transform,
                       ident_params: dict, params: dict) -> None:
    source_schema = registry.get(source_id)[0]
    inputs = {"src": source_id, **transform.extra_inputs}
    shape = source_schema.shape if transform.shape == "inherit" else transform.shape
    unit = source_schema.unit if transform.unit == "inherit" else transform.unit
    label = transform.label or f"{source_schema.label or source_id} [{transform.name}]"
    decorator = registry.metric(
        id=cid,
        label=label,
        inputs=inputs,
        shape=shape,
        unit=unit,
        parameters=params,
    )
    decorator(_make_fn(transform))


def compile_pipeline(source_id: str, steps, *, id: str | None = None,
                     registry=None) -> str:
    """Compile a chain of transforms over ``source_id`` → final concrete id.

    ``steps`` is a list of ``{"transform": name, "params": {...identity...}}``
    (``params`` optional). Each step registers an intermediate metric whose
    ``src`` is the previous step's id, building a static Metric-Graph chain. The
    final metric declares the union of every step's compute params. Idempotent.
    ``id`` overrides the final id (e.g. to pin a legacy literal id). ``registry``
    defaults to the shared ``default_registry`` (tests pass a throwaway one)."""
    registry = registry if registry is not None else default_registry
    if not registry.has(source_id):
        raise ValueError(f"compile: unknown source metric '{source_id}'")
    if not steps:
        raise ValueError("compile: at least one transform step is required")

    current = source_id
    union_params: dict = {}
    n = len(steps)
    for i, step in enumerate(steps):
        tname = step["transform"] if isinstance(step, dict) else step
        ident = (step.get("params") if isinstance(step, dict) else None) or {}
        transform = TRANSFORMS.get(tname)
        if transform is None:
            raise ValueError(
                f"compile: unknown transform '{tname}'. Known: {sorted(TRANSFORMS)}"
            )
        union_params.update(transform.compute_params)
        is_last = i == n - 1
        cid = id if (is_last and id) else f"{current}__{tname}{_id_suffix(ident)}"
        if not registry.has(cid):
            _register_compiled(
                registry, cid, current, transform, ident,
                params=(dict(union_params) if is_last else dict(transform.compute_params)),
            )
        current = cid
    return current


def compile_transform(source_id: str, name: str, *, params: dict | None = None,
                      id: str | None = None, registry=None) -> str:
    """Compile a single ``{source, transform, params}`` → concrete metric id."""
    return compile_pipeline(
        source_id, [{"transform": name, "params": params or {}}], id=id, registry=registry
    )
