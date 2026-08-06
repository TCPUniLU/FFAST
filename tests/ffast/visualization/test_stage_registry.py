import pytest
from pydantic import ValidationError
from ffast.visualization.stages.models import StageSchema
from ffast.visualization.stages.registry import StageRegistry


@pytest.fixture
def registry():
    return StageRegistry()


def test_register_and_retrieve(registry):
    @registry.stage(
        id="ffast.test",
        inputs={"x": "frame.positions"},
        outputs={"y": "(N,3)"},
    )
    def fn(x):
        return x

    decl, func = registry.get("ffast.test")
    assert decl.id == "ffast.test"
    assert decl.inputs == {"x": "frame.positions"}
    assert decl.outputs == {"y": "(N,3)"}
    assert func is fn


def test_parameters_stored(registry):
    @registry.stage(
        id="ffast.paramtest",
        inputs={},
        outputs={"y": "float"},
        parameters={"scale": {"type": "float", "default": 1.0, "role": "present"}},
    )
    def fn():
        pass

    decl, _ = registry.get("ffast.paramtest")
    assert "scale" in decl.parameters
    assert decl.parameters["scale"].default == 1.0
    assert decl.parameters["scale"].role == "present"


def test_compute_and_present_roles_stored(registry):
    @registry.stage(
        id="ffast.roles",
        inputs={},
        outputs={},
        parameters={
            "norm": {"type": "choice", "choices": ["l1", "l2"], "default": "l2", "role": "compute"},
            "cmap": {"type": "choice", "choices": ["hot", "cold"], "default": "hot", "role": "present"},
        },
    )
    def fn():
        pass

    decl, _ = registry.get("ffast.roles")
    assert decl.parameters["norm"].role == "compute"
    assert decl.parameters["cmap"].role == "present"


def test_duplicate_id_rejected(registry):
    @registry.stage(id="ffast.dup", inputs={}, outputs={})
    def fn1():
        pass

    with pytest.raises(ValueError, match="already registered"):
        @registry.stage(id="ffast.dup", inputs={}, outputs={})
        def fn2():
            pass


def test_unnamespaced_id_rejected(registry):
    with pytest.raises(ValueError, match="dot"):
        @registry.stage(id="no_namespace", inputs={}, outputs={})
        def fn():
            pass


def test_function_still_callable_after_registration(registry):
    @registry.stage(id="ffast.passthrough", inputs={}, outputs={})
    def fn(x):
        return x * 3

    assert fn(4) == 12


def test_list_stages(registry):
    @registry.stage(id="ffast.a", inputs={}, outputs={})
    def a():
        pass

    @registry.stage(id="ffast.b", inputs={}, outputs={})
    def b():
        pass

    assert set(registry.list_stages()) == {"ffast.a", "ffast.b"}


def test_get_unknown_raises(registry):
    with pytest.raises(KeyError):
        registry.get("ffast.nonexistent")


def test_stage_schema_forbids_extra_fields():
    with pytest.raises(ValidationError):
        StageSchema.model_validate({
            "id": "ffast.x",
            "inputs": {},
            "outputs": {},
            "unexpected_field": "oops",
        })


def test_builtin_stages_registered():
    """Exact set, deliberately — every registered stage must have a live caller.

    ADR 0049 removed six stages that were registered and tested but reached by
    nothing in production (``bond_indices``, ``bond_positions``,
    ``selection_mask``, ``value_colors``, ``force_arrows``, ``frame``). An
    equality assertion is what makes a re-introduced dead stage fail here; the
    previous ``issubset`` check would have let one back in silently.
    """
    from ffast.visualization.stages.registry import _default_registry
    assert set(_default_registry.list_stages()) == {
        # driven directly by scene_builder
        "ffast.atom_positions",
        "ffast.atom_sizes",
        "ffast.atom_colors",
        "ffast.atom_labels",
        "ffast.unit_cell_edges",
        "ffast.atom_filter",
        # driven from elsewhere: transforms and metric coloring
        "ffast.kabsch_alignment",
        "ffast.displacement_stats",
    }


def test_parameter_scope_defaults_to_view(registry):
    @registry.stage(
        id="ffast.scope_default",
        inputs={},
        outputs={},
        parameters={"s": {"type": "float", "default": 1.0, "role": "compute"}},
    )
    def fn():
        pass

    decl, _ = registry.get("ffast.scope_default")
    assert decl.parameters["s"].scope == "view"


def test_parameter_scope_explicit(registry):
    @registry.stage(
        id="ffast.scope_explicit",
        inputs={},
        outputs={},
        parameters={
            "a": {"type": "float", "default": 1.0, "role": "compute", "scope": "session"},
            "b": {"type": "bool", "default": False, "role": "compute", "scope": "view_dataset"},
        },
    )
    def fn():
        pass

    decl, _ = registry.get("ffast.scope_explicit")
    assert decl.parameters["a"].scope == "session"
    assert decl.parameters["b"].scope == "view_dataset"


def test_parameter_scope_rejects_unknown_value(registry):
    with pytest.raises(ValidationError):
        @registry.stage(
            id="ffast.scope_bad",
            inputs={},
            outputs={},
            parameters={"s": {"type": "float", "default": 1.0, "role": "compute", "scope": "galaxy"}},
        )
        def fn():
            pass


# ── resolve_parameters ───────────────────────────────────────────────────────
#
# The catalog stays the single home for declared defaults after ADR 0049 removed
# the executor: scene_builder calls stage functions directly but resolves their
# parameters through here, so a default is never restated at a call site.

def test_resolve_parameters_returns_declared_defaults(registry):
    @registry.stage(
        id="ffast.styled",
        inputs={"z": "frame.elements"},
        outputs={"out": "..."},
        parameters={
            "scale": {"type": "float", "default": 2.5, "role": "present"},
            "mode": {"type": "choice", "choices": ["a", "b"], "default": "a", "role": "present"},
        },
    )
    def styled(z, *, scale=2.5, mode="a"):
        return z

    assert registry.resolve_parameters("ffast.styled") == {"scale": 2.5, "mode": "a"}


def test_resolve_parameters_overlays_caller_values(registry):
    @registry.stage(
        id="ffast.styled",
        inputs={"z": "frame.elements"},
        outputs={"out": "..."},
        parameters={"scale": {"type": "float", "default": 1.0, "role": "present"}},
    )
    def styled(z, *, scale=1.0):
        return z

    assert registry.resolve_parameters("ffast.styled", {"scale": 9.0}) == {"scale": 9.0}


def test_resolve_parameters_ignores_unknown_keys(registry):
    """A view's stored parameters outlive the stage that read them."""
    @registry.stage(
        id="ffast.styled",
        inputs={"z": "frame.elements"},
        outputs={"out": "..."},
        parameters={"scale": {"type": "float", "default": 1.0, "role": "present"}},
    )
    def styled(z, *, scale=1.0):
        return z

    resolved = registry.resolve_parameters(
        "ffast.styled", {"scale": 3.0, "retired_param": "ignore me"}
    )
    assert resolved == {"scale": 3.0}


def test_resolve_parameters_empty_for_parameterless_stage(registry):
    @registry.stage(
        id="ffast.plain", inputs={"z": "frame.elements"}, outputs={"out": "..."},
    )
    def plain(z):
        return z

    assert registry.resolve_parameters("ffast.plain") == {}
    assert registry.resolve_parameters("ffast.plain", {"nope": 1}) == {}


def test_resolve_parameters_unknown_stage_raises(registry):
    with pytest.raises(KeyError):
        registry.resolve_parameters("ffast.nonexistent")


def test_scene_builder_resolves_live_stage_defaults():
    """The three parameterised stages scene_builder drives resolve cleanly."""
    import ffast.visualization.stages.builtin  # noqa: F401
    from ffast.visualization.stages.registry import _default_registry

    assert _default_registry.resolve_parameters("ffast.atom_sizes") == {"scale": 1.0}
    assert _default_registry.resolve_parameters("ffast.atom_colors") == {"dimming": 1.0}
    assert _default_registry.resolve_parameters("ffast.atom_labels") == {"mode": "index"}
