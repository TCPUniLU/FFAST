"""A toy Level 1 reader over a tiny text format, for the reader tests.

A ``.toy`` file has one JSON object per line, one line per frame::

    {"numbers": [8, 1, 1], "positions": [[0,0,0], ...], "energy": -1.5,
     "forces": [[0,0,0], ...], "cell": [[10,0,0], [0,10,0], [0,0,10]]}

``energy``, ``forces`` and ``cell`` may be missing from a line.
"""
import json

import numpy as np


def write_toy(path, frames):
    with open(path, "w") as f:
        for frame in frames:
            f.write(json.dumps(frame) + "\n")
    return str(path)


def make_frames(n, *, atoms=3, variable=False, energy=True, forces=True, cell=False):
    """``n`` frames; frame i has positions filled with i (+ the atom's index)."""
    frames = []
    for i in range(n):
        count = atoms + (i % 3 if variable else 0)
        frame = {
            "numbers": [8, 1, 1, 6, 7, 9][:count] if count <= 6 else [1] * count,
            "positions": [[i + a, 0.0, 0.0] for a in range(count)],
        }
        if energy:
            frame["energy"] = float(i) * 10
        if forces:
            frame["forces"] = [[-(i + a), 0.0, 0.0] for a in range(count)]
        if cell:
            frame["cell"] = (np.eye(3) * (10 + i)).tolist()
        frames.append(frame)
    return frames


class ToyReader:
    name = "toy"
    extensions = ["*.toy"]
    package = "ffast-toy"
    asked = []          # the frames each read() was asked for, newest last

    def __init__(self, path):
        self.path = path
        with open(path) as f:
            self.frames = [json.loads(line) for line in f if line.strip()]

    def n_frames(self):
        return len(self.frames)

    def contents(self):
        has = lambda key: any(key in f for f in self.frames)
        return {
            "positions": True,
            "numbers": True,
            "cell": has("cell"),
            "energies": ["dft"] if has("energy") else [],
            "forces": ["dft"] if has("forces") else [],
            "frame_fields": [],
            "atom_fields": [],
            "units": {},
        }

    def read(self, frames):
        type(self).asked.append(list(frames))
        chosen = [self.frames[i] for i in frames]
        out = {
            "n_atoms": np.array([len(f["numbers"]) for f in chosen], dtype=np.int64),
            "numbers": np.concatenate([f["numbers"] for f in chosen]).astype(np.int64),
            "positions": np.concatenate([np.reshape(f["positions"], (-1, 3)) for f in chosen]),
        }
        has = self.contents()
        if has["cell"]:
            out["cell"] = np.array([f.get("cell", np.full((3, 3), np.nan)) for f in chosen], dtype=float)
        if has["energies"]:
            out["energy:dft"] = np.array([f.get("energy", np.nan) for f in chosen], dtype=float)
        if has["forces"]:
            out["forces:dft"] = np.concatenate([
                np.reshape(f["forces"], (-1, 3)) if "forces" in f else np.full((len(f["numbers"]), 3), np.nan)
                for f in chosen])
        return out
