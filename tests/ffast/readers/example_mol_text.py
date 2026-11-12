import numpy as np
from ase.data import atomic_numbers


class MolTextReader:
    """The same format as a Level 2 reader: FFAST finds the frames, keeps
    their list, and hands whole frames' text to parse(), in worker processes
    for a big file."""

    name = "mol-text"
    extensions = ["*.molt"]
    package = "ffast-mol"

    def __init__(self, path):
        self.path = path

    def contents(self):
        return {"positions": True, "numbers": True, "energies": ["dft"],
                "units": {"energy": "eV"}}

    @staticmethod
    def find_frames(path):
        """One pass: where each frame starts (and where the last one ends),
        and how many atoms each has."""
        starts, counts = [0], []
        with open(path, "rb") as f:
            while (head := f.readline()).strip():
                count = int(head)
                for _ in range(count + 1):          # the energy line, then the atoms
                    f.readline()
                counts.append(count)
                starts.append(f.tell())
        return np.array(starts), np.array(counts)

    @staticmethod
    def parse(data, first_frame):
        """Whole frames, as bytes, to the same dict read() returns."""
        lines = [line.split() for line in data.decode().splitlines() if line.strip()]
        counts, energies, atoms, i = [], [], [], 0
        while i < len(lines):
            count = int(lines[i][0])
            counts.append(count)
            energies.append(float(lines[i + 1][0]))
            atoms += lines[i + 2:i + 2 + count]
            i += 2 + count
        return {
            "n_atoms": np.array(counts),
            "numbers": np.array([atomic_numbers[a[0]] for a in atoms]),
            "positions": np.array([a[1:4] for a in atoms], dtype=float),
            "energy:dft": np.array(energies),
        }
