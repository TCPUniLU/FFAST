"""``.xyz`` and ``.extxyz`` files as a Level 2 reader (ADR 0095, step 4).

Today's fast parser (``ffast.io.extxyz_arrays``) behind the contract: the frame
list is the frame index (``ffast.io.frame_index``, shared with the Load
dialog's frame count), ``parse`` is ``parse_block``. A file it cannot read
raises ``NotFast``; FFAST then reads the file with the ASE reader
(``fallback``), as before.

With the package ``ffast-xyz`` installed, ``parse_into`` is the fast parser
(ADR 0100): Rust fills FFAST's arrays on threads, by the same rules, and hands
a file it cannot match exactly to ``parse``.

Names follow the ASE reader and the old key probe: header keys and columns
that mention energy or force, then the literal ``energy`` / ``forces`` (what an
extxyz file's ``energy=`` and ``forces`` column are for ASE: its calculator).
"""
from __future__ import annotations

import functools
import os

import numpy as np
from ase.calculators.calculator import all_properties
from ase.io.extxyz import REV_PROPERTY_NAME_MAP, per_config_properties

from ffast.io import extxyz_arrays as xa
from ffast.io import frame_index
from ffast.loaders.ase import _RESERVED_ARRAY_KEYS


class XyzReader:
    name = "xyz"
    extensions = ["*.xyz", "*.extxyz"]
    package = None            # bundled
    hidden = True             # not a Type of its own: "ase (auto)" resolves to it for these files
    fallback = "ase (auto)"   # reads what this parser cannot (NotFast)
    cache_version = 1         # bump when parse() changes the arrays it makes

    def __init__(self, path):
        self.path = path

    def contents(self):
        """What the first frames' headers list (their atoms are not parsed)."""
        keys, layouts = xa.header_info(_first_block(self.path), 0)
        columns = {}
        for layout in layouts:
            for name, kind, _, n in layout:
                if name not in ("species", "Z", "pos"):
                    columns.setdefault(name, (2 if n > 1 else 1, n))
        names = _names(keys, columns)
        return {
            "positions": True,
            "numbers": True,
            "cell": True,
            "energies": names["energies"],
            "forces": names["forces"],
            "frame_fields": names["frame_fields"],
            "atom_fields": sorted(names["atom_columns"]),
            "units": {},
        }

    @staticmethod
    def find_frames(path):
        """Where each frame starts and its atom count: the frame index, from
        its saved copy when the file has not changed."""
        index = frame_index.get_frame_index(path)
        return index.starts, index.atom_counts()

    @staticmethod
    def energies(path, starts):
        """The energies on the header lines of the frames starting at
        ``starts``: {name: one float per frame, NaN where a frame has none}.
        Only each frame's second line is read, never its atoms."""
        import mmap
        out = {}
        n = len(starts)
        if n == 0:
            return out
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            for i, start in enumerate(np.asarray(starts).tolist()):
                head = mm.find(b"\n", start) + 1
                end = mm.find(b"\n", head)
                for name, value in xa.header_energies(mm[head:end if end >= 0 else len(mm)]).items():
                    column = out.get(name)
                    if column is None:
                        column = out[name] = np.full(n, np.nan)
                    column[i] = value
        return out

    @staticmethod
    def parse_into(path, starts, ends, counts, first_frame, arrays, threads):
        """The fast parser (ADR 0100): ``ffast_xyz.parse_into`` fills the
        arrays; its header keys and new columns get the names ``parse``
        gives them."""
        import ffast_xyz
        columns, keys = [], {}               # (file column, view); file column -> key
        for key, view in arrays.items():
            if key.startswith("forces:"):
                name = key[len("forces:"):]
            elif key.startswith("atom:"):
                name = _FILE_NAME.get(key[len("atom:"):], key[len("atom:"):])
            else:
                continue
            columns.append((name, view))
            keys[name] = key
        state = _file_state(path)
        try:
            got = ffast_xyz.parse_into(
                path, starts, ends, counts, int(first_frame), arrays["numbers"], arrays["positions"],
                arrays.get("cell"), columns, state["essential"], state["extras"], *_fast_rules(),
                int(threads))
        except ffast_xyz.NotFast as exc:
            raise xa.NotFast(str(exc)) from None
        state["extras"].update(got["extras"])
        names = _names(list(dict.fromkeys([*got["values"], *got["texts"]])), {})
        out = {"frame": {}, "text": {}, "atom_new": {}, "filled": [keys[c] for c in got["seen"]]}
        for found, target in ((got["values"], out["frame"]), (got["texts"], out["text"])):
            for key, pair in found.items():
                if key in names["energies"]:
                    target[f"energy:{key}"] = pair
                if key in names["frame_fields"]:
                    target[f"frame:{key}"] = pair            # float64: FFAST joins frame: keys so
        for column, (rows, values) in got["new"].items():
            if values.ndim == 2:
                out["atom_new"][f"forces:{column}"] = (rows, values)
            else:
                out["atom_new"][f"atom:{REV_PROPERTY_NAME_MAP.get(column, column)}"] = (rows, values)
        return out

    @staticmethod
    def parse(data, first_frame):
        fa = xa.parse_block(data, first_frame, all_values=True)
        names = _names(list(fa.values), {k: (c.ndim, c.shape[-1] if c.ndim == 2 else 1)
                                         for k, c in fa.columns.items()})
        out = {"n_atoms": fa.counts, "numbers": fa.numbers, "positions": fa.positions,
               "cell": fa.cells}
        for key in names["energies"]:
            out[f"energy:{key}"] = fa.values[key]          # as parsed: a whole-number
        for key in names["forces"]:                        # energy stays integer, as for ASE
            out[f"forces:{key}"] = fa.columns[key]
        for key in names["frame_fields"]:
            out[f"frame:{key}"] = fa.values[key].astype(np.float64)
        for key, column in names["atom_columns"].items():
            out[f"atom:{key}"] = fa.columns[column].astype(np.float64)
        return out


def _names(value_keys, columns):
    """The energy, force and field names of frames whose headers give the
    numbers ``value_keys`` and whose atom lines have ``columns``
    ({name: (ndim, width)}), in the order the ASE reader lists them: named
    ones first, then the calculator's. ``atom_columns`` maps a field's ASE name
    to its column."""
    energies = [k for k in value_keys
                if "energy" in k.lower() and k not in per_config_properties and k != "energy"]
    if "energy" in value_keys:
        energies.append("energy")
    forces = [k for k, (ndim, width) in columns.items()
              if ndim == 2 and width == 3 and "force" in k.lower() and k != "forces"]
    if "forces" in columns:
        forces.append("forces")
    atom_columns = {}
    for name, (ndim, _) in columns.items():
        ase_name = REV_PROPERTY_NAME_MAP.get(name, name)
        if ndim == 1 and ase_name not in all_properties and ase_name not in _RESERVED_ARRAY_KEYS:
            atom_columns[ase_name] = name
    return {
        "energies": energies,
        "forces": forces,
        "frame_fields": sorted(k for k in value_keys if k not in per_config_properties),
        "atom_columns": atom_columns,
    }


_FILE_NAME = {ase: name for name, ase in REV_PROPERTY_NAME_MAP.items()}   # an atom field's column


@functools.lru_cache(maxsize=1)
def _fast_rules():
    """What the fast parser needs to name things as the Python parser does:
    element symbols, ASE's names for columns, and the ASE names that are no
    atom field."""
    from ase.data import atomic_numbers
    return (dict(atomic_numbers), dict(REV_PROPERTY_NAME_MAP),
            set(all_properties) | set(_RESERVED_ARRAY_KEYS))


@functools.lru_cache(maxsize=64)
def _state_of(path, size, mtime_ns):
    _, layouts = xa.header_info(_first_block(path, 1), 0)
    essential, extras = xa._signature(layouts[0])
    return {"essential": [(name, kind, n) for name, kind, n in essential], "extras": dict(extras)}


def _file_state(path):
    """What the fast parser checks a file's frames against, kept per file (and
    its size and time) across groups and loads: frame 0's columns every frame
    must list (``_signature``), and the type of each column a frame may lack,
    as far as the file has been read. A frame that lists other columns, or
    another type for one, hands the file to the Python parser, which refuses
    it whenever both fall in one of its batches."""
    st = os.stat(path)
    return _state_of(os.path.abspath(path), st.st_size, st.st_mtime_ns)


CONTENTS_FRAMES = 100    # frames whose columns and header keys contents() lists


def _first_block(path, frames=CONTENTS_FRAMES):
    """The text of the first ``frames`` frames, without scanning the file.
    Frame 0 must be a frame; a broken or unfinished one after it only ends the
    look (the key probe has never read past frame 0)."""
    chunks = []
    with open(path, "rb") as f:
        for i in range(frames):
            line = f.readline()
            while line and not line.strip():
                line = f.readline()
            if not line:
                break
            try:
                natoms = int(line.strip())
            except ValueError:
                if i == 0:
                    raise xa.NotFast(f"{path} does not start with an atom count") from None
                break
            body = [f.readline() for _ in range(natoms + 1)]
            if not all(body):
                if i == 0:
                    raise xa.NotFast(f"frame 1 of {path} ends early")
                break
            chunks.append(line + b"".join(body))
    data = b"".join(chunks)
    return data if data.endswith(b"\n") else data + b"\n"
