"""Typed symbolic input ref constants for metric declarations.

Use via:
    from ffast.metrics import inputs as I
    @metric(..., inputs={"reference": I.reference_forces, ...})

A Python metric declares offsets via optional_inputs=["offsets"]; a formula
that groups by structure (ADR 0064) names it as a required input.

Beyond the closed set below, a metric may reference an arbitrary **Dataset
Field** by key-in-the-ref (ADR 0023): ``{reference,prediction}.{info,atoms}.<key>``
(e.g. ``reference.atoms.charges``, ``prediction.info.dipole``). These are
validated by pattern rather than frozenset membership — see ``is_valid_ref``.
"""

import re
from dataclasses import dataclass

from ffast.metrics import dims


@dataclass(frozen=True)
class RawInput:
    """Canonical formula-facing metadata for one fixed raw metric input."""

    ref: str
    shape: tuple
    label: str
    variable: str
    side: str


RAW_INPUTS = (
    RawInput("reference.energies", (dims.N_frames,), "Energy", "E",
             "reference"),
    RawInput("reference.forces", (dims.N_atoms, dims.xyz), "Forces", "F",
             "reference"),
    RawInput("reference.positions", (dims.N_atoms, dims.xyz), "Positions", "R",
             "reference"),
    RawInput("reference.masses", (dims.N_atoms,), "Masses", "m", "reference"),
    RawInput("prediction.energies", (dims.N_frames,), "Energy", "E",
             "prediction"),
    RawInput("prediction.forces", (dims.N_atoms, dims.xyz), "Forces", "F",
             "prediction"),
)
RAW_INPUT_BY_REF = {item.ref: item for item in RAW_INPUTS}

reference_energies  = "reference.energies"
reference_forces    = "reference.forces"
reference_stress    = "reference.stress"
reference_positions = "reference.positions"
reference_elements  = "reference.elements"
reference_masses    = "reference.masses"
# Whether the dataset has a unit cell: one bool for the whole dataset.
reference_periodic  = "reference.periodic"

prediction_energies = "prediction.energies"
prediction_forces   = "prediction.forces"
prediction_stress   = "prediction.stress"

selection_indices   = "selection.indices"

# Reserved dataset-topology ref: a per-structure atom-count array (ADR 0042
# Decision 4). Auto-wired as the reserved Expression Variable ``n_atoms`` and
# resolved by InputResolver (np.diff(offsets) for variable datasets,
# count-per-structure for uniform).
n_atoms = "n_atoms"

# Where each structure's atoms start (ADR 0058 step 6); a formula that groups
# by structure gets it without naming it (ADR 0064 section 6).
offsets = "offsets"

ALL_VALID_REFS: frozenset = frozenset({
    reference_energies,
    reference_forces,
    reference_stress,
    reference_positions,
    reference_elements,
    reference_masses,
    reference_periodic,
    prediction_energies,
    prediction_forces,
    prediction_stress,
    selection_indices,
    n_atoms,
    offsets,
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
