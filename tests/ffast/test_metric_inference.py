"""Signature-driven metric inference (DX layer over @metric).

Proves an inferred metric yields the same MetricSchema as the fully-declared
one, and that explicit args still win (backward compatibility).
"""
import numpy as np
import pytest

from ffast.metrics.registry import MetricRegistry
from ffast.metrics import dims, units
from ffast.metrics.signature import Ref, P
from ffast.metrics.models import MetricSchema


def _schema_tuple(decl: MetricSchema):
    """Normalised, comparable view of a schema (shape serialised to str)."""
    return (
        decl.id,
        decl.label,
        decl.description,
        decl.inputs,
        decl.optional_inputs,
        decl.serialize_shape(decl.shape),
        decl.unit,
        decl.model_dump()["parameters"],
    )


def test_inferred_matches_explicit_force_mae():
    """The real ffast.force_mae, declared two ways, must produce one schema."""
    from typing import Literal
    explicit = MetricRegistry()
    inferred = MetricRegistry()

    @explicit.metric(
        id="ffast.force_mae",
        label="Force Error (per atom)",
        description="Per-atom mean absolute force error between prediction and reference.",
        inputs={"force_difference": "ffast.force_difference"},
        shape=(dims.N_atoms,),
        unit=units.force,
        parameters={
            "norm": {"type": "choice", "choices": ["l1", "l2"], "default": "l2", "role": "compute"},
        },
    )
    def force_mae_explicit(force_difference, *, norm="l2"):
        return np.linalg.norm(force_difference, axis=-1)

    @inferred.metric(id="ffast.force_mae", unit=units.force)
    def force_mae(
        force_difference: Ref["ffast.force_difference"],
        *,
        norm: Literal["l1", "l2"] = "l2",
    ) -> (dims.N_atoms,):
        """Force Error (per atom)

        Per-atom mean absolute force error between prediction and reference.
        """
        return np.linalg.norm(force_difference, axis=-1)

    e, _ = explicit.get("ffast.force_mae")
    i, _ = inferred.get("ffast.force_mae")

    # Whole-schema equivalence (shape serialised, params normalised).
    assert _schema_tuple(e) == _schema_tuple(i)
    assert i.label == "Force Error (per atom)"
    assert i.description.startswith("Per-atom mean absolute")


def test_literal_becomes_choice_param():
    from typing import Literal
    reg = MetricRegistry()

    @reg.metric(unit=units.force, namespace="demo")
    def with_choice(
        force_difference: Ref["ffast.force_difference"],
        *,
        norm: Literal["l1", "l2"] = "l2",
    ) -> (dims.N_atoms,):
        return force_difference

    decl, _ = reg.get("demo.with_choice")
    norm = decl.model_dump()["parameters"]["norm"]
    assert norm["type"] == "choice"
    assert norm["choices"] == ["l1", "l2"]
    assert norm["default"] == "l2"
    assert norm["role"] == "compute"


def test_annotated_param_metadata():
    from typing import Annotated
    reg = MetricRegistry()

    @reg.metric(unit=units.energy, namespace="demo")
    def scaled(
        reference: Ref["reference.energies"],
        *,
        scale: Annotated[float, P(min=0.1, max=10.0, label="Error Scale")] = 1.0,
    ) -> (dims.N_frames,):
        return reference * scale

    decl, _ = reg.get("demo.scaled")
    scale = decl.model_dump()["parameters"]["scale"]
    assert scale["type"] == "float"
    assert scale["default"] == 1.0
    assert scale["min"] == 0.1
    assert scale["max"] == 10.0
    assert scale["label"] == "Error Scale"


def test_optional_input_from_default():
    reg = MetricRegistry()

    @reg.metric(unit=units.force, namespace="demo")
    def per_frame(
        force_mae: Ref["ffast.force_mae"],
        offsets=None,
    ) -> (dims.N_frames,):
        return force_mae

    decl, _ = reg.get("demo.per_frame")
    assert decl.inputs == {"force_mae": "ffast.force_mae"}
    assert decl.optional_inputs == ["offsets"]


def test_bare_string_ref_annotation():
    reg = MetricRegistry()

    @reg.metric(unit=units.energy, namespace="demo")
    def diff(
        reference: "reference.energies",
        predicted: "prediction.energies",
    ) -> (dims.N_frames,):
        return predicted - reference

    decl, _ = reg.get("demo.diff")
    assert decl.inputs == {
        "reference": "reference.energies",
        "predicted": "prediction.energies",
    }


def test_unannotated_required_input_raises():
    reg = MetricRegistry()
    with pytest.raises(ValueError, match="no ref annotation"):
        @reg.metric(unit=units.energy, namespace="demo")
        def bad(mystery) -> (dims.N_frames,):
            return mystery


def test_missing_shape_raises():
    reg = MetricRegistry()
    with pytest.raises(ValueError, match="cannot infer shape"):
        @reg.metric(unit=units.energy, namespace="demo")
        def no_shape(reference: Ref["reference.energies"]):
            return reference


def test_namespace_drives_id():
    reg = MetricRegistry()

    @reg.metric(namespace="mylab", unit=units.energy)
    def fn(reference: Ref["reference.energies"]) -> (dims.N_frames,):
        return reference

    assert reg.has("mylab.fn")


def test_jaxtyping_return_infers_dim_tuple():
    """A ``Float[np.ndarray, "N_atoms xyz"]`` return maps axis names -> dims tuple."""
    from jaxtyping import Float
    reg = MetricRegistry()

    @reg.metric(unit=units.force, namespace="demo")
    def per_atom_vec(
        reference: Ref["reference.forces"],
    ) -> Float[np.ndarray, "N_atoms xyz"]:
        return reference

    decl, _ = reg.get("demo.per_atom_vec")
    # Axis names resolve to the exact Dim objects from ffast.metrics.dims.
    assert decl.shape == (dims.N_atoms, dims.xyz)
    assert decl.serialize_shape(decl.shape) == "(N_atoms, xyz)"


def test_jaxtyping_scalar_axis_return():
    """A single-axis jaxtyping return infers a one-element dims tuple."""
    from jaxtyping import Float
    reg = MetricRegistry()

    @reg.metric(unit=units.energy, namespace="demo")
    def per_frame(
        reference: Ref["reference.energies"],
    ) -> Float[np.ndarray, "N_frames"]:
        return reference

    decl, _ = reg.get("demo.per_frame")
    assert decl.shape == (dims.N_frames,)


def test_jaxtyping_unknown_axis_name_raises():
    """An axis name that is not a known dim raises ValueError at registration."""
    from jaxtyping import Float
    reg = MetricRegistry()

    # signature._shape_from_return raises ValueError("... are not known dims ...")
    # when an axis label has no matching Dim in ffast.metrics.dims.
    with pytest.raises(ValueError, match="not known dims"):
        @reg.metric(unit=units.force, namespace="demo")
        def bad_axis(
            reference: Ref["reference.forces"],
        ) -> Float[np.ndarray, "bogus_axis xyz"]:
            return reference


def test_explicit_args_still_win_backward_compat():
    """Fully-declared metric is unchanged by the inference layer."""
    reg = MetricRegistry()

    @reg.metric(
        id="ffast.legacy",
        inputs={"reference": "reference.forces"},
        shape="per_structure_per_atom",
        unit="energy",
    )
    def legacy(reference):
        return reference

    decl, func = reg.get("ffast.legacy")
    assert decl.id == "ffast.legacy"
    assert decl.inputs == {"reference": "reference.forces"}
    assert decl.shape == "per_structure_per_atom"
    assert decl.unit == "energy"
    assert func(7) == 7
