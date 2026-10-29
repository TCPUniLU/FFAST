from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Union


@dataclass(frozen=True)
class Dim:
    """Semantic output dimension descriptor.

    fixed_size=None means variable / runtime-determined.
    fixed_size=3 means always exactly 3 (e.g. xyz).
    fixed_size=(3,3) means a 3x3 block.

    User A can define custom dims:
        my_dim = Dim("my_lab.custom", fixed_size=None)
    """
    name: str
    fixed_size: Optional[Union[int, tuple]] = None

    def __repr__(self) -> str:
        return f"dims.{self.name}"


def shape_to_str(shape) -> str:
    """Serialize a shape (Dim | tuple[Dim, ...] | str) to a compact string."""
    if isinstance(shape, Dim):
        return shape.name
    if isinstance(shape, tuple):
        parts = [d.name if isinstance(d, Dim) else str(d) for d in shape]
        if len(parts) == 1:
            return parts[0]
        return "(" + ", ".join(parts) + ")"
    return str(shape)


scalar     = Dim("scalar",     fixed_size=None)
N_frames   = Dim("N_frames",   fixed_size=None)
N_atoms    = Dim("N_atoms",    fixed_size=None)
N_elements = Dim("N_elements", fixed_size=None)
xyz        = Dim("xyz",        fixed_size=3)
voigt      = Dim("voigt",      fixed_size=6)
tensor_3x3 = Dim("tensor_3x3", fixed_size=(3, 3))

# Density-curve shapes (ADR 0021): a density/KDE Transform Metric emits a
# (2, G) array — row 0 the x grid, row 1 the density — so the density Panel
# Kind draws it without recomputing. ``grid`` resolution is runtime-determined.
curve_xy   = Dim("curve_xy",   fixed_size=2)
grid       = Dim("grid",       fixed_size=None)
