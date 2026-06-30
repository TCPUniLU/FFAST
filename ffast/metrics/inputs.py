"""Typed symbolic input ref constants for metric declarations.

Use via:
    from ffast.metrics import inputs as I
    @metric(..., inputs={"reference": I.reference_forces, ...})

offsets is declared via optional_inputs=["offsets"], not inputs.

Beyond the closed set below, a metric may reference an arbitrary **Dataset
Field** by key-in-the-ref (ADR 0023): ``{reference,prediction}.{info,atoms}.<key>``
(e.g. ``reference.atoms.charges``, ``prediction.info.dipole``). These are
validated by pattern rather than frozenset membership — see ``is_valid_ref``.
"""

import re

reference_energies  = "reference.energies"
reference_forces    = "reference.forces"
reference_stress    = "reference.stress"
reference_positions = "reference.positions"
reference_elements  = "reference.elements"
reference_masses    = "reference.masses"

prediction_energies = "prediction.energies"
prediction_forces   = "prediction.forces"
prediction_stress   = "prediction.stress"

selection_indices   = "selection.indices"

ALL_VALID_REFS: frozenset = frozenset({
    reference_energies,
    reference_forces,
    reference_stress,
    reference_positions,
    reference_elements,
    reference_masses,
    prediction_energies,
    prediction_forces,
    prediction_stress,
    selection_indices,
})

# Dataset Field refs (ADR 0023): {reference,prediction}.{info,atoms}.<key>.
# `info` → per-frame scalar (Frame Field); `atoms` → per-atom scalar (Atom Field).
_FIELD_REF_RE = re.compile(r"^(reference|prediction)\.(info|atoms)\.(.+)$")


def parse_field_ref(ref):
    """Return ``(side, kind, key)`` for a Dataset Field ref, else ``None``.

    side ∈ {'reference','prediction'}, kind ∈ {'info','atoms'}.
    """
    if not isinstance(ref, str):
        return None
    m = _FIELD_REF_RE.match(ref)
    if m is None:
        return None
    return m.group(1), m.group(2), m.group(3)


def is_field_ref(ref) -> bool:
    return parse_field_ref(ref) is not None


def is_valid_ref(ref) -> bool:
    """A ref is valid if it is in the closed set or matches the field pattern."""
    return ref in ALL_VALID_REFS or is_field_ref(ref)
