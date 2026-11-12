import numpy as np
from ase.data import atomic_numbers

from ffast.readers import ReaderError


class MolReader:
    """A .mol file: for each frame, the atom count, the energy, then one
    "symbol x y z" line per atom."""

    name = "mol"                 # unique; the name in FFAST's Type list
    extensions = ["*.mol"]
    package = "ffast-mol"        # the pip name, for the "not installed" message

    def __init__(self, path):
        with open(path) as f:
            lines = [line.split() for line in f if line.strip()]
        self.frames, i = [], 0
        while i < len(lines):
            count = int(lines[i][0])
            atoms = lines[i + 2:i + 2 + count]
            if len(atoms) != count:
                raise ReaderError(f"Frame {len(self.frames)} has {len(atoms)} atoms, not {count}")
            self.frames.append((float(lines[i + 1][0]), atoms))
            i += 2 + count

    def n_frames(self):
        return len(self.frames)

    def contents(self):
        return {"positions": True, "numbers": True, "energies": ["dft"],
                "units": {"energy": "eV"}}

    def read(self, frames):
        chosen = [self.frames[i] for i in frames]
        atoms = [a for _, frame in chosen for a in frame]
        return {
            "n_atoms": np.array([len(frame) for _, frame in chosen]),
            "numbers": np.array([atomic_numbers[a[0]] for a in atoms]),
            "positions": np.array([a[1:4] for a in atoms], dtype=float),
            "energy:dft": np.array([energy for energy, _ in chosen]),
        }
