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
    from ffast.visualization.stages.registry import _default_registry
    ids = _default_registry.list_stages()
    expected = {
        "ffast.atom_positions",
        "ffast.atom_sizes",
        "ffast.atom_colors",
        "ffast.bond_indices",
        "ffast.bond_positions",
        "ffast.unit_cell_edges",
        "ffast.force_arrows",
        "ffast.kabsch_alignment",
        "ffast.value_colors",
        "ffast.displacement_stats",
        # M2: frame, selection, filtering, label stages
        "ffast.frame",
        "ffast.selection_mask",
        "ffast.atom_filter",
        "ffast.atom_labels",
    }
    assert expected.issubset(set(ids))


# ── dependencies ─────────────────────────────────────────────────────────────

def test_dependencies_parsed_from_stage_inputs(registry):
    @registry.stage(id="ffast.dep_a", inputs={"x": "frame.positions"}, outputs={"y": "..."})
    def a(x):
        return x

    @registry.stage(
        id="ffast.dep_b",
        inputs={"y": "stage.ffast.dep_a.y", "z": "dataset.foo"},
        outputs={"out": "..."},
    )
    def b(y, z=None):
        return y

    decl_a, _ = registry.get("ffast.dep_a")
    decl_b, _ = registry.get("ffast.dep_b")
    assert decl_a.dependencies == set()          # only external namespaces
    assert decl_b.dependencies == {"ffast.dep_a"}  # only the stage reference


def test_builtin_label_depends_on_atom_positions():
    from ffast.visualization.stages.registry import _default_registry
    decl, _ = _default_registry.get("ffast.atom_labels")
    assert "ffast.atom_positions" in decl.dependencies


# ── resolve_order ──────────────────────────────────────────────────────────

def _chain_registry():
    r = StageRegistry()

    @r.stage(id="ffast.a", inputs={"x": "frame.positions"}, outputs={"y": "..."})
    def a(x):
        return x

    @r.stage(id="ffast.b", inputs={"y": "stage.ffast.a.y"}, outputs={"z": "..."})
    def b(y):
        return y

    @r.stage(id="ffast.c", inputs={"z": "stage.ffast.b.z", "y": "stage.ffast.a.y"}, outputs={"w": "..."})
    def c(z, y):
        return z

    return r


def test_resolve_order_topological():
    r = _chain_registry()
    order = r.resolve_order(["ffast.c"])
    # dependencies precede dependents
    assert order.index("ffast.a") < order.index("ffast.b") < order.index("ffast.c")
    assert set(order) == {"ffast.a", "ffast.b", "ffast.c"}


def test_resolve_order_no_duplicates_for_shared_dep():
    r = _chain_registry()
    order = r.resolve_order(["ffast.c"])
    assert order.count("ffast.a") == 1


def test_resolve_order_independent_target_only_pulls_its_deps():
    r = _chain_registry()
    assert r.resolve_order(["ffast.a"]) == ["ffast.a"]


def test_resolve_order_unknown_target_raises():
    r = _chain_registry()
    with pytest.raises(KeyError):
        r.resolve_order(["ffast.nonexistent"])


def test_resolve_order_missing_dependency_raises():
    r = StageRegistry()

    @r.stage(id="ffast.orphan", inputs={"y": "stage.ffast.ghost.y"}, outputs={"z": "..."})
    def orphan(y):
        return y

    with pytest.raises(KeyError, match="ghost"):
        r.resolve_order(["ffast.orphan"])


def test_resolve_order_cycle_raises():
    r = StageRegistry()

    @r.stage(id="ffast.x", inputs={"v": "stage.ffast.y.v"}, outputs={"v": "..."})
    def x(v):
        return v

    @r.stage(id="ffast.y", inputs={"v": "stage.ffast.x.v"}, outputs={"v": "..."})
    def y(v):
        return v

    with pytest.raises(ValueError, match="cycle"):
        r.resolve_order(["ffast.x"])


def test_resolve_order_builtin_bond_positions_chain():
    from ffast.visualization.stages.registry import _default_registry
    order = _default_registry.resolve_order(["ffast.bond_positions"])
    assert order.index("ffast.atom_positions") < order.index("ffast.bond_positions")
    assert order.index("ffast.bond_indices") < order.index("ffast.bond_positions")


# ── parameter scope ──────────────────────────────────────────────────────────

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
