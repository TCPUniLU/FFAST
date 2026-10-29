"""Element reference tables for the Headless Core.

Pure per-element data (Z-indexed): default atom colors, covalent radii and the
derived bond-distance matrix, and element-symbol <-> atomic-number maps. Used by
the visualization scene builder, dataset loaders, and remote-dataset bond
inference. No Qt, no configuration — see ADR 0026 (headless-core boundary).
"""

from ffast.chemistry.atoms import (
    atomColors,
    covalentBonds,
    covalentRadii,
    zIntToZStr,
    zStrToZInt,
)

__all__ = [
    "atomColors",
    "covalentBonds",
    "covalentRadii",
    "zIntToZStr",
    "zStrToZInt",
]
