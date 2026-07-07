"""Tests for ffast.visualization.pipeline.execute — catalog-driven stage execution."""
import numpy as np
import pytest

from ffast.visualization.pipeline import StageExecutionError, execute
from ffast.visualization.stages.registry import StageRegistry


@pytest.fixture
def registry():
    r = StageRegistry()

    @r.stage(
        id="ffast.double",
        inputs={"x": "frame.x"},
        outputs={"y": "doubled"},
        parameters={"factor": {"type": "float", "default": 2.0, "role": "compute"}},
    )
    def double(x, *, factor=2.0):
        return np.asarray(x) * factor

    @r.stage(
        id="ffast.add_one",
        inputs={"y": "stage.ffast.double.y"},
        outputs={"z": "plus one"},
    )
    def add_one(y):
        return np.asarray(y) + 1

    @r.stage(
        id="ffast.split",
        inputs={"x": "frame.x"},
        outputs={"lo": "min", "hi": "max"},
    )
    def split(x):
        a = np.asarray(x)
        return a.min(), a.max()

    return r


def test_executes_single_stage(registry):
    out = execute(registry, ["ffast.double"], {"frame.x": np.array([1.0, 2.0])})
    assert np.allclose(out["stage.ffast.double.y"], [2.0, 4.0])


def test_chains_stage_outputs_as_inputs(registry):
    out = execute(registry, ["ffast.add_one"], {"frame.x": np.array([1.0, 2.0])})
    # double → [2,4], add_one → [3,5]
    assert np.allclose(out["stage.ffast.add_one.z"], [3.0, 5.0])
    # upstream output also available
    assert np.allclose(out["stage.ffast.double.y"], [2.0, 4.0])


def test_parameter_override_applied(registry):
    out = execute(
        registry,
        ["ffast.double"],
        {"frame.x": np.array([1.0, 2.0])},
        parameters={"ffast.double": {"factor": 10.0}},
    )
    assert np.allclose(out["stage.ffast.double.y"], [10.0, 20.0])


def test_default_parameter_used_when_no_override(registry):
    out = execute(registry, ["ffast.double"], {"frame.x": np.array([3.0])})
    assert np.allclose(out["stage.ffast.double.y"], [6.0])


def test_unknown_parameter_override_ignored(registry):
    out = execute(
        registry,
        ["ffast.double"],
        {"frame.x": np.array([1.0])},
        parameters={"ffast.double": {"nonexistent": 99.0}},
    )
    assert np.allclose(out["stage.ffast.double.y"], [2.0])


def test_multi_output_split_into_addresses(registry):
    out = execute(registry, ["ffast.split"], {"frame.x": np.array([5.0, 1.0, 9.0])})
    assert out["stage.ffast.split.lo"] == 1.0
    assert out["stage.ffast.split.hi"] == 9.0


def test_context_preserved_in_results(registry):
    ctx = {"frame.x": np.array([1.0])}
    out = execute(registry, ["ffast.double"], ctx)
    assert "frame.x" in out


def test_failing_stage_raises_with_stage_id(registry):
    @registry.stage(id="ffast.boom", inputs={"x": "frame.x"}, outputs={"y": "..."})
    def boom(x):
        raise ValueError("kaboom")

    with pytest.raises(StageExecutionError, match="ffast.boom"):
        execute(registry, ["ffast.boom"], {"frame.x": np.array([1.0])})


def test_missing_required_external_input_wrapped_as_stage_error(registry):
    # ffast.double declares input x=frame.x and its function has no default for
    # x. When frame.x is absent from the context, the "fall back to default"
    # path leaves x unbound, so fn(**kwargs) raises TypeError — which execute()
    # must surface as a StageExecutionError naming the offending stage, not leak
    # the raw TypeError.
    with pytest.raises(StageExecutionError, match="ffast.double"):
        execute(registry, ["ffast.double"], {})  # no frame.x supplied


def test_bad_output_arity_raises(registry):
    @registry.stage(id="ffast.wrong_arity", inputs={}, outputs={"a": "..", "b": ".."})
    def wrong():
        return np.array([1.0])  # single value, but 2 outputs declared

    with pytest.raises(StageExecutionError, match="outputs"):
        execute(registry, ["ffast.wrong_arity"], {})


def test_resolve_order_drives_execution_for_builtins():
    # Real builtin chain: atom_labels depends on atom_positions.
    import ffast.visualization.stages.builtin  # noqa: F401
    from ffast.visualization.stages.registry import _default_registry

    ctx = {
        "frame.positions": np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
        "view.transforms": [],
        "frame.elements": np.array([1, 6]),
    }
    out = execute(_default_registry, ["ffast.atom_labels"], ctx)
    # atom_positions ran first (dependency), then atom_labels
    assert "stage.ffast.atom_positions.positions" in out
    assert out["stage.ffast.atom_labels.texts"] == ["0", "1"]
