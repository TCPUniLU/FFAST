"""Files ASE reads, as a reader (ADR 0095, step 3), Level 1.

``.xyz`` and ``.extxyz`` are not taken yet (``can_read``): they keep today's
fast path through the frame index until step 4 moves them to Level 2.

Energy and force names are listed the way the key probe did: the keys of
``atoms.info`` and ``atoms.arrays`` that mention energy or force, then the
literal ``energy`` / ``forces`` when a calculator holds the values (the name
the page has always sent for "calculator (built-in)").
"""
from __future__ import annotations

import numpy as np

from ffast.loaders.ase import available_field_keys

_XYZ = (".xyz", ".extxyz")


class AseReader:
    name = "ase (auto)"
    extensions = ["*"]
    package = None            # bundled

    @staticmethod
    def can_read(path):
        return not str(path).lower().endswith(_XYZ)

    def __init__(self, path):
        self.path = path
        self._count = None
        self._first = None
        self._traj = None        # .traj files are read by frame number
        self._iter = None        # other formats front to back
        self._pos = 0            # the frame _iter gives next

    # ── what the file holds ────────────────────────────────────────────────

    def _is_traj(self):
        return str(self.path).lower().endswith(".traj")

    def _trajectory(self):
        if self._traj is None:
            from ase.io.trajectory import Trajectory
            self._traj = Trajectory(self.path)
        return self._traj

    def n_frames(self):
        if self._count is None:
            if self._is_traj():
                self._count = len(self._trajectory())
            else:
                import ase.io
                self._count = sum(1 for _ in ase.io.iread(self.path, index=":"))
        return self._count

    def _first_frame(self):
        if self._first is None:
            self._first = self._atoms([0])[0]
        return self._first

    def contents(self):
        first = self._first_frame()
        energies = [k for k in first.info if "energy" in k.lower()]
        forces = [k for k in first.arrays if "force" in k.lower()]
        if _calc(first.get_potential_energy) is not None and "energy" not in energies:
            energies.append("energy")
        if _calc(first.get_forces) is not None and "forces" not in forces:
            forces.append("forces")
        frame_fields, atom_fields = available_field_keys([first])
        return {
            "positions": True,
            "numbers": True,
            "cell": bool(first.cell.any() or first.pbc.any()),
            "energies": energies,
            "forces": forces,
            "frame_fields": frame_fields,
            "atom_fields": atom_fields,
            "units": {},
        }

    # ── the frames ─────────────────────────────────────────────────────────

    def _atoms(self, frames):
        """The Atoms of ``frames`` (increasing). A trajectory is read by
        number, any other format front to back, starting again when asked for
        a frame it has passed."""
        if self._is_traj():
            traj = self._trajectory()
            return [traj[int(i)] for i in frames]
        import ase.io
        out = []
        for i in frames:
            i = int(i)
            if self._iter is None or i < self._pos:
                self._iter, self._pos = ase.io.iread(self.path, index=":"), 0
            while self._pos < i:
                next(self._iter)
                self._pos += 1
            out.append(next(self._iter))
            self._pos += 1
        return out

    def read(self, frames):
        atoms = self._atoms(frames)
        c = self.contents()
        out = {
            "n_atoms": np.array([len(a) for a in atoms], dtype=np.int64),
            "numbers": np.concatenate([a.numbers for a in atoms]),
            "positions": np.concatenate([a.positions for a in atoms]),
        }
        if c["cell"]:
            out["cell"] = np.array([np.asarray(a.cell) for a in atoms], dtype=np.float64)
        for key in c["energies"]:
            out[f"energy:{key}"] = np.array([_energy(a, key) for a in atoms], dtype=np.float64)
        for key in c["forces"]:
            out[f"forces:{key}"] = np.concatenate([_forces(a, key) for a in atoms])
        for key in c["frame_fields"]:
            out[f"frame:{key}"] = np.array([_scalar(a.info.get(key)) for a in atoms], dtype=np.float64)
        for key in c["atom_fields"]:
            out[f"atom:{key}"] = np.concatenate([_per_atom(a, key) for a in atoms])
        return out


def _calc(fn):
    """A calculator's answer, or None when the frame has none."""
    try:
        return fn()
    except Exception:
        return None


def _scalar(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _energy(atoms, key):
    if key in atoms.info:
        return _scalar(atoms.info[key])
    value = _calc(atoms.get_potential_energy) if key == "energy" else None
    return np.nan if value is None else float(value)


def _forces(atoms, key):
    if key in atoms.arrays:
        return np.asarray(atoms.arrays[key], dtype=np.float64)
    value = _calc(atoms.get_forces) if key == "forces" else None
    return np.full((len(atoms), 3), np.nan) if value is None else np.asarray(value, dtype=np.float64)


def _per_atom(atoms, key):
    value = atoms.arrays.get(key)
    if value is None or np.shape(value) != (len(atoms),):
        return np.full(len(atoms), np.nan)
    return np.asarray(value, dtype=np.float64)
