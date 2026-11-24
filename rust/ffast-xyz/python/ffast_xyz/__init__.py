"""Fast parser for extended-XYZ files (ADR 0100)."""

from ffast_xyz import _ffast_xyz
from ffast_xyz._ffast_xyz import (FIND_END, FIND_MORE, FIND_PYTHON, FrameFinder, NotFast, contract, energies,
                                  find_frames, parse_into, timings)

# FFAST uses the parser only when this number is the one it expects.
CONTRACT = 1
# parse_into writes NaN where a frame lacks a column. False for a library
# built before the flag (an editable install whose library is not rebuilt).
FILLS_MISSING = bool(getattr(_ffast_xyz, "FILLS_MISSING", False))

__all__ = ["CONTRACT", "FILLS_MISSING", "FIND_END", "FIND_MORE", "FIND_PYTHON", "FrameFinder", "NotFast",
           "contract", "energies", "find_frames", "parse_into", "timings"]
