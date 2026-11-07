"""Small extxyz files for the read-once tests (plan 2026-11-02, ADR 0076)."""
from pathlib import Path

UNIFORM = """3
Properties=species:S:1:pos:R:3:forces:R:3 Lattice="5.0 0.1 0.0 0.2 6.0 0.0 0.3 0.4 7.0" pbc="T F T" energy=-10.5 charge=0
O 0.0 0.0 0.1 0.01 0.02 0.03
H 0.75 0.0 -0.5 -0.01 0.0 0.1
H -0.75 0.0 -0.5 0.0 -0.02 -0.13
3
Properties=species:S:1:pos:R:3:forces:R:3 Lattice="5.0 0.1 0.0 0.2 6.0 0.0 0.3 0.4 7.0" pbc="T F T" energy=-11 charge=0
O 0.0 0.01 0.1 0.11 0.02 0.03
H 0.76 0.0 -0.5 -0.11 0.0 0.1
H -0.74 0.0 -0.51 0.0 -0.02 -0.13
3
Properties=species:S:1:pos:R:3:forces:R:3 Lattice="5.0 0.1 0.0 0.2 6.0 0.0 0.3 0.4 7.0" pbc="T F T" energy=-10.25 charge=0
O 0.0 0.02 0.1 0.21 0.02 0.03
H 0.77 0.0 -0.5 -0.21 0.0 0.1
H -0.73 0.0 -0.52 0.0 -0.02 -0.13
"""

# Atom counts change; no Lattice; lower-case symbols; a quoted value that
# contains "energy=999"; whole-number energies (ASE keeps them as int64);
# numeric header fields (gap, charge) and a per-atom field (q); a second
# energy/force pair (energy_mace, forces_mace) as an in-file prediction.
VARIABLE = """2
Properties=species:S:1:pos:R:3:forces:R:3:forces_mace:R:3:q:R:1 note="old energy=999 here" energy=-5 energy_mace=-5.5 gap=1.25 charge=0 tag=abc
c 0.0 0.0 0.0 0.1 0.2 0.3 0.11 0.21 0.31 0.5
o 1.1 0.0 0.0 -0.1 -0.2 -0.3 -0.11 -0.21 -0.31 -0.5
3
Properties=species:S:1:pos:R:3:forces:R:3:forces_mace:R:3:q:R:1 note="x" energy=-7 energy_mace=-7.25 gap=2.5 charge=1 tag=def
N 0.0 0.0 0.0 0.1 0.0 0.0 0.12 0.0 0.0 0.1
H 1.0 0.0 0.0 0.0 0.1 0.0 0.0 0.12 0.0 0.2
H 0.0 1.0 0.0 0.0 0.0 0.1 0.0 0.0 0.12 0.3
1
Properties=species:S:1:pos:R:3:forces:R:3:forces_mace:R:3:q:R:1 note="y" energy=-1 energy_mace=-1.5 gap=0.75 charge=0 tag=ghi
Ar 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0 0.0
"""

# Frame 2 adds a per-atom number (spin): the fast reader keeps it, with NaN
# where a frame has none.
COLUMNS_CHANGE = """2
Properties=species:S:1:pos:R:3:forces:R:3 energy=-1.0
H 0.0 0.0 0.0 0.1 0.0 0.0
H 0.0 0.0 0.74 -0.1 0.0 0.0
2
Properties=species:S:1:pos:R:3:forces:R:3 energy=-1.1
H 0.0 0.0 0.0 0.2 0.0 0.0
H 0.0 0.0 0.75 -0.2 0.0 0.0
2
Properties=species:S:1:pos:R:3:forces:R:3:spin:R:1 energy=-1.2
H 0.0 0.0 0.0 0.3 0.0 0.0 0.5
H 0.0 0.0 0.76 -0.3 0.0 0.0 -0.5
"""

# Plain xyz: a free-text comment line, no Properties. ASE reads it.
PLAIN = """2
two hydrogens
H 0.0 0.0 0.0
H 0.0 0.0 0.74
2
two hydrogens again
H 0.0 0.0 0.0
H 0.0 0.0 0.75
"""

# Frame 2 has no forces: the fast reader refuses, ASE reads it, with a warning.
FORCES_CHANGE = """2
Properties=species:S:1:pos:R:3:forces:R:3 energy=-1.0
H 0.0 0.0 0.0 0.1 0.0 0.0
H 0.0 0.0 0.74 -0.1 0.0 0.0
2
Properties=species:S:1:pos:R:3:forces:R:3 energy=-1.1
H 0.0 0.0 0.0 0.2 0.0 0.0
H 0.0 0.0 0.75 -0.2 0.0 0.0
2
Properties=species:S:1:pos:R:3 energy=-1.2
H 0.0 0.0 0.0
H 0.0 0.0 0.76
"""

FILES = {
    "uniform.extxyz": UNIFORM,
    "variable.extxyz": VARIABLE,
    "columns_change.extxyz": COLUMNS_CHANGE,
    "forces_change.extxyz": FORCES_CHANGE,
    "plain.xyz": PLAIN,
}

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "data" / "variable-sized molecular"
# The original SPICE pair retains its golden fingerprints after relocation.
REPO_FILES = [_EXAMPLES / "dataset.xyz", _EXAMPLES / "prediction.xyz"]
