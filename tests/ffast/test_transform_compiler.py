"""Phase 5a: {metric, transform, params} → Transform Metric compiler (ADR 0021).

Isolated tests run against a throwaway registry so the global default_registry
isn't polluted; one parity test compiles against the real built-ins.
"""
import numpy as np
import pytest

from ffast.metrics import dims
from ffast.metrics.executor import InProcessExecutor
from ffast.metrics.registry import MetricRegistry
from ffast.metrics.transforms import (
    TRANSFORMS,
    compile_pipeline,
    compile_transform,
)


def _src_registry():
    """Fresh registry with one per-frame source metric over a raw input."""
    r = MetricRegistry()

    @r.metric(
        id="t.series",
        inputs={"reference": "reference.energies"},
        shape=(dims.N_frames,),
        unit="energy",
    )
    def series(reference):
        return np.asarray(reference, dtype=float)

    return r


# --- id scheme -------------------------------------------------------------- #
def test_deterministic_id():
    r = _src_registry()
    assert compile_transform("t.series", "smooth", registry=r) == "t.series__smooth"
    assert compile_transform("t.series", "mirror_kde", registry=r) == "t.series__mirror_kde"
    assert (
        compile_pipeline("t.series", ["smooth", "abs"], registry=r)
        == "t.series__smooth__abs"
    )


def test_idempotent_reregister():
    r = _src_registry()
    first = compile_transform("t.series", "smooth", registry=r)
    n = len(r.list_metrics())
    second = compile_transform("t.series", "smooth", registry=r)
    assert first == second
    assert len(r.list_metrics()) == n  # no duplicate registration, no raise


def test_identity_params_hash_suffix():
    r = _src_registry()
    a = compile_transform("t.series", "smooth", params={"k": 1}, registry=r)
    b = compile_transform("t.series", "smooth", params={"k": 2}, registry=r)
    assert a != b
    assert a.startswith("t.series__smooth__p") and b.startswith("t.series__smooth__p")


def test_unknown_source_and_transform_raise():
    r = _src_registry()
    with pytest.raises(ValueError):
        compile_transform("t.nope", "smooth", registry=r)
    with pytest.raises(ValueError):
        compile_transform("t.series", "no_such_transform", registry=r)


# --- registry wiring -------------------------------------------------------- #
def test_static_graph_edge_to_source():
    r = _src_registry()
    cid = compile_transform("t.series", "smooth", registry=r)
    assert r.freeze() == []
    assert r.dependencies_of(cid) == {"t.series"}


def test_compute_params_surfaced_on_final_metric():
    r = _src_registry()
    cid = compile_transform("t.series", "smooth", registry=r)
    schema, _ = r.get(cid)
    assert "window" in schema.parameters
    assert schema.parameters["window"].role == "compute"


def test_shape_and_unit_policy():
    r = _src_registry()
    smooth = r.get(compile_transform("t.series", "smooth", registry=r))[0]
    assert smooth.shape == (dims.N_frames,) and smooth.unit == "energy"  # inherit

    density = r.get(compile_transform("t.series", "mirror_kde", registry=r))[0]
    assert density.shape == (dims.curve_xy, dims.grid)

    scalar = r.get(compile_transform("t.series", "mean_abs", registry=r))[0]
    assert scalar.shape == (dims.scalar,)


def test_pipeline_intermediate_declares_only_own_params():
    r = _src_registry()
    # shift carries `shifted`, smooth carries `window`; final declares the union.
    final = compile_pipeline(
        "t.series", ["smooth", "abs"], registry=r
    )
    inter = r.get("t.series__smooth")[0]
    last = r.get(final)[0]
    assert set(inter.parameters) == {"window"}
    assert set(last.parameters) == {"window"}  # abs adds none; union from smooth


# --- numeric behaviour ------------------------------------------------------ #
def test_smooth_window_one_is_identity():
    r = _src_registry()
    cid = compile_transform("t.series", "smooth", registry=r)
    ex = InProcessExecutor(r)
    out = ex.run(cid, {"reference": [1.0, 2.0, 3.0]}, {"window": 1})
    np.testing.assert_allclose(out.values, [1.0, 2.0, 3.0])


def test_smooth_window_two_averages():
    r = _src_registry()
    cid = compile_transform("t.series", "smooth", registry=r)
    ex = InProcessExecutor(r)
    out = ex.run(cid, {"reference": [1.0, 3.0, 5.0]}, {"window": 2})
    np.testing.assert_allclose(out.values, [2.0, 4.0])  # valid-mode convolution


def test_mirror_kde_emits_2_by_grid():
    r = _src_registry()
    cid = compile_transform("t.series", "mirror_kde", registry=r)
    ex = InProcessExecutor(r)
    out = ex.run(cid, {"reference": list(np.random.RandomState(0).randn(50))}, {})
    arr = np.asarray(out.values)
    assert arr.ndim == 2 and arr.shape[0] == 2 and arr.shape[1] == 200
    assert np.all(arr[0] >= 0)  # mirror-KDE x grid starts at 0


# --- parity with the hand-written Phase-0/4 metric -------------------------- #
def test_pipeline_matches_legacy_energy_error_smoothed():
    """shift → smooth → abs over ffast.energy_difference reproduces the legacy
    ffast.energy_error_smoothed body (compiled against the real built-ins)."""
    import ffast.metrics.builtin  # noqa: F401 — register energy metrics
    from ffast.metrics.registry import default_registry

    pid = compile_pipeline("ffast.energy_difference", ["shift", "smooth", "abs"])
    ex = InProcessExecutor(default_registry)
    inputs = {"reference": [1.0, 2.0, 3.0], "predicted": [1.5, 2.5, 3.5]}
    params = {"window": 1, "shifted": False}

    compiled = ex.run(pid, inputs, params)
    legacy = ex.run("ffast.energy_error_smoothed", inputs, params)
    assert not hasattr(compiled, "traceback"), getattr(compiled, "traceback", "")
    np.testing.assert_allclose(compiled.values, legacy.values, atol=1e-10)
    np.testing.assert_allclose(compiled.values, [0.5, 0.5, 0.5], atol=1e-10)


def test_catalog_has_expected_transforms():
    for name in ("smooth", "abs", "mirror_kde", "abs_kde", "value_kde",
                 "mean_abs", "rmse", "shift"):
        assert name in TRANSFORMS
