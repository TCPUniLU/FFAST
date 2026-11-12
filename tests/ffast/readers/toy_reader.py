"""A toy Level 1 reader over a tiny text format, for the reader tests.

A ``.toy`` file has one JSON object per line, one line per frame::

    {"numbers": [8, 1, 1], "positions": [[0,0,0], ...], "energy": -1.5,
     "forces": [[0,0,0], ...], "cell": [[10,0,0], [0,10,0], [0,0,10]]}

``energy``, ``forces`` and ``cell`` may be missing from a line. A line may also
carry ``"extra": {"mace": {"energy": -2.0, "forces": [[...]]}}``: more named
energy and force sets.
"""
import json

import numpy as np


def write_toy(path, frames):
    with open(path, "w") as f:
        for frame in frames:
            f.write(json.dumps(frame) + "\n")
    return str(path)


def make_frames(n, *, atoms=3, variable=False, energy=True, forces=True, cell=False,
                extra=(), shift=0.0, numbers=None):
    """``n`` frames; frame i has positions filled with i (+ the atom's index,
    + ``shift``). ``extra`` names more energy/force sets; ``numbers`` replaces
    every frame's atomic numbers."""
    frames = []
    for i in range(n):
        count = atoms + (i % 3 if variable else 0)
        frame = {
            "numbers": list(numbers) if numbers else
            ([8, 1, 1, 6, 7, 9][:count] if count <= 6 else [1] * count),
            "positions": [[i + a + shift, 0.0, 0.0] for a in range(count)],
        }
        if extra:
            frame["extra"] = {
                name: {"energy": 100.0 * (k + 1) + i,
                       "forces": [[0.5 * (k + 1), 0.0, float(a)] for a in range(count)]}
                for k, name in enumerate(extra)}
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

    units = {}

    def _extra_names(self):
        names = []
        for f in self.frames:
            for name in f.get("extra", {}):
                if name not in names:
                    names.append(name)
        return names

    def contents(self):
        has = lambda key: any(key in f for f in self.frames)
        extra = self._extra_names()
        return {
            "positions": True,
            "numbers": True,
            "cell": has("cell"),
            "energies": (["dft"] if has("energy") else []) + extra,
            "forces": (["dft"] if has("forces") else []) + extra,
            "frame_fields": [],
            "atom_fields": [],
            "units": dict(self.units),
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
        for name in self._extra_names():
            out[f"energy:{name}"] = np.array(
                [f.get("extra", {}).get(name, {}).get("energy", np.nan) for f in chosen], dtype=float)
            out[f"forces:{name}"] = np.concatenate([
                np.reshape(f["extra"][name]["forces"], (-1, 3)) if name in f.get("extra", {})
                else np.full((len(f["numbers"]), 3), np.nan) for f in chosen])
        if has["cell"]:
            out["cell"] = np.array([f.get("cell", np.full((3, 3), np.nan)) for f in chosen], dtype=float)
        if "dft" in has["energies"]:
            out["energy:dft"] = np.array([f.get("energy", np.nan) for f in chosen], dtype=float)
        if "dft" in has["forces"]:
            out["forces:dft"] = np.concatenate([
                np.reshape(f["forces"], (-1, 3)) if "forces" in f else np.full((len(f["numbers"]), 3), np.nan)
                for f in chosen])
        return out


class ToyTextReader:
    """A toy Level 2 reader over the same ``.toy`` lines: it lists where each
    frame (line) starts, and parses whole frames' bytes. Counts its calls."""

    name = "toytext"
    extensions = ["*.toyt"]
    package = None
    found = []         # one entry per find_frames call
    parsed = []        # (first_frame, number of frames) per parse call (this process only)

    def __init__(self, path):
        self.path = path

    def contents(self):
        return {"positions": True, "numbers": True, "cell": False, "energies": ["dft"],
                "forces": ["dft"], "frame_fields": [], "atom_fields": [], "units": {}}

    @classmethod
    def find_frames(cls, path):
        cls.found.append(path)
        starts, counts, pos = [0], [], 0
        with open(path, "rb") as f:
            for line in f:
                if not line.strip():
                    continue
                counts.append(len(json.loads(line)["numbers"]))
                pos += len(line)
                starts.append(pos)
        return np.array(starts, dtype=np.int64), np.array(counts, dtype=np.int64)

    @staticmethod
    def parse(data, first_frame):
        ToyTextReader.parsed.append((first_frame, data.count(b"\n")))
        frames = [json.loads(line) for line in data.split(b"\n") if line.strip()]
        return {
            "n_atoms": np.array([len(f["numbers"]) for f in frames], dtype=np.int64),
            "numbers": np.concatenate([f["numbers"] for f in frames]).astype(np.int64),
            "positions": np.concatenate([np.reshape(f["positions"], (-1, 3)) for f in frames]),
            "energy:dft": np.array([f["energy"] for f in frames], dtype=float),
            "forces:dft": np.concatenate([np.reshape(f["forces"], (-1, 3)) for f in frames]),
        }
