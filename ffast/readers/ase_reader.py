"""Files ASE reads, as a reader (ADR 0095, step 3), Level 1.

``.xyz`` and ``.extxyz`` are not taken yet (``can_read``): they keep today's
fast path through the frame index until step 4 moves them to Level 2.

Energy and force names are listed the way the key probe did: the keys of
``atoms.info`` and ``atoms.arrays`` that mention energy or force, then the
literal ``energy`` / ``forces`` when a calculator holds the values (the name
the page has always sent for "calculator (built-in)").
"""
from __future__ import annotations

import os

import numpy as np

from ffast.loaders.ase import available_field_keys

_XYZ = (".xyz", ".extxyz")
_BUNDLE = ".ffast.json"     # a portable bundle (ADR 0110): the bundle reader's


_NO_CELL = {"cell": False}      # the cell is not written, as ASE's export never wrote it
_ONE_STRUCTURE = {"frames": 1, "energies": 0, "forces": 0, "cell": False, "frame_fields": False,
                  "atom_fields": False}


class AseReader:
    name = "ase (auto)"
    extensions = ["*"]
    package = None            # bundled
    # What Export can write (ADR 0095, decision 11), in the dialog's order.
    write_formats = [
        {"order": 0, "format": "extxyz", "label": "Extended XYZ", "ext": ".extxyz", "can_write": dict(_NO_CELL)},
        {"order": 1, "format": "xyz", "label": "XYZ", "ext": ".xyz", "can_write": dict(_NO_CELL)},
        {"order": 2, "format": "db", "label": "ASE database", "ext": ".db", "can_write": {
            **_NO_CELL, "energies": 1, "forces": 1, "frame_fields": False, "atom_fields": False}},
        {"order": 3, "format": "traj", "label": "ASE trajectory", "ext": ".traj", "can_write": {
            **_NO_CELL, "forces": 1, "atom_fields": False}},
        {"order": 4, "format": "vasp", "label": "VASP POSCAR (one structure)", "ext": ".vasp",
         "can_write": dict(_ONE_STRUCTURE)},
        {"order": 5, "format": "dftb", "label": "DFTB+ gen (one structure)", "ext": ".gen",
         "can_write": dict(_ONE_STRUCTURE)},
    ]

    @staticmethod
    def can_read(path):
        return not str(path).lower().endswith(_XYZ + (_BUNDLE,))

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

    @staticmethod
    def open_writer(path, contents, format=None):
        return _AseWriter(path, format)

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
            out[f"energy:{key}"] = np.array([_energy(a, key) for a in atoms])   # an integer stays one, as ASE keeps it
        for key in c["forces"]:
            out[f"forces:{key}"] = np.concatenate([_forces(a, key) for a in atoms])
        for key in c["frame_fields"]:
            out[f"frame:{key}"] = np.array([_scalar(a.info.get(key)) for a in atoms], dtype=np.float64)
        for key in c["atom_fields"]:
            out[f"atom:{key}"] = np.concatenate([_per_atom(a, key) for a in atoms])
        return out


_APPENDING = {".extxyz": "extxyz", ".xyz": "extxyz", ".traj": "traj"}   # formats written chunk by chunk


class _AseWriter:
    """Frame by frame, each frame with its own elements, writing the energies
    and forces it has. The reference's ``energy`` and ``forces`` go to the
    calculator, the other names to ``atoms.info`` and ``atoms.arrays``. A frame
    with no atoms is left out and counted in ``skipped``. Formats that append
    (extxyz, traj) are written chunk by chunk; the others when closed."""

    def __init__(self, path, format):
        self.path = path
        ext = os.path.splitext(path)[1].lower()
        self.format = format
        self.appending = (format or _APPENDING.get(ext)) in _APPENDING.values()
        if self.appending and format is None:
            self.format = _APPENDING.get(ext)
        self.atoms, self.skipped, self.started = [], 0, False

    def write(self, chunk):
        from ase import Atoms
        from ase.calculators.calculator import Calculator

        class FakeCalc(Calculator):
            def __init__(self):
                super().__init__()   # the .traj and .db writers read `parameters`

        offsets = np.concatenate([[0], np.cumsum(chunk["n_atoms"])])
        made = []
        for k in range(len(chunk["n_atoms"])):
            a, b = int(offsets[k]), int(offsets[k + 1])
            if a == b:
                self.skipped += 1
                continue
            atoms = Atoms(numbers=chunk["numbers"][a:b], positions=chunk["positions"][a:b])
            results = {}
            for key, value in chunk.items():
                kind, _, name = key.partition(":")
                if kind == "energy" and not np.isnan(value[k]):
                    if name == "energy":
                        results["energy"] = float(value[k])
                    else:
                        atoms.info[name] = float(value[k])
                elif kind == "forces" and not np.isnan(value[a:b]).any():
                    if name == "forces":
                        results["forces"] = np.asarray(value[a:b])
                    else:
                        atoms.arrays[name] = np.asarray(value[a:b])
                elif kind == "frame" and not np.isnan(value[k]):
                    atoms.info[name] = float(value[k])
                elif kind == "atom" and not np.isnan(value[a:b]).any():
                    atoms.arrays[name] = np.asarray(value[a:b])
            if results:
                atoms.calc = FakeCalc()
                atoms.calc.results = results
            made.append(atoms)
        if self.appending:
            self._append(made)
        else:
            self.atoms.extend(made)

    def _append(self, atoms):
        import ase.io
        if atoms:
            ase.io.write(self.path, atoms, format=self.format, append=self.started)
            self.started = True

    def close(self):
        import ase.io
        if self.appending:
            if not self.started:                        # nothing to write: an empty file, as before
                ase.io.write(self.path, [], format=self.format)
        else:
            ase.io.write(self.path, self.atoms, format=self.format)


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
        value = atoms.info[key]
    else:
        value = _calc(atoms.get_potential_energy) if key == "energy" else None
    return value if isinstance(value, (int, float, np.integer, np.floating)) else np.nan


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
