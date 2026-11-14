"""Fast parser for extended-XYZ files (ADR 0100)."""

from ffast_xyz._ffast_xyz import contract

# FFAST uses the parser only when this number is the one it expects.
CONTRACT = 1

__all__ = ["CONTRACT", "contract"]
