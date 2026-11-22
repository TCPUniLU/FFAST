"""Fast parser for extended-XYZ files (ADR 0100)."""

from ffast_xyz._ffast_xyz import (FIND_END, FIND_MORE, FIND_PYTHON, FrameFinder, NotFast, contract, energies,
                                  find_frames, parse_into, timings)

# FFAST uses the parser only when this number is the one it expects.
CONTRACT = 1

__all__ = ["CONTRACT", "FIND_END", "FIND_MORE", "FIND_PYTHON", "FrameFinder", "NotFast", "contract",
           "energies", "find_frames", "parse_into", "timings"]
