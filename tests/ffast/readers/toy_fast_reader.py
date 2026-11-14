"""A toy Level 2 reader with both parsers (ADR 0100): ``parse`` (the Python
parser) and ``parse_into`` (the fast parser's contract, written in Python).

Lines are the ``.toyt`` JSON lines of ``toy_reader``. A line may also carry
``energy`` as text (``"1_000"``, read by ``_number``), ``charge`` (a frame
field), ``weight`` (a per-atom field ``contents()`` lists, which a frame may
lack), ``late`` (a per-atom field ``contents()`` does not list) and
``"fast": "no"`` (``parse_into`` hands the file over).
"""
import json

import numpy as np

from ffast.io.extxyz_arrays import NotFast, _number
from tests.ffast.readers.toy_reader import ToyTextReader


def _value(value):
    return _number(value) if isinstance(value, str) else value


def _values(frames, key):
    """One value per frame as ``parse_block(all_values=True)`` gives it: as
    ``np.array`` makes it when every frame has a number, else float64 with
    NaN. A value that is not a number is not stored."""
    values = [_value(f[key]) if key in f else None for f in frames]
    if all(v is None for v in values):
        return None
    if all(v is not None for v in values):
        return np.array(values)
    return np.array([np.nan if v is None else v for v in values], dtype=np.float64)


def _column(frames, key):
    if not any(key in f for f in frames):
        return None
    return np.concatenate([np.asarray(f[key], dtype=np.float64) if key in f
                           else np.full(len(f["numbers"]), np.nan) for f in frames])


class ToyFastReader(ToyTextReader):
    name = "toyfast"
    extensions = ["*.toyq"]
    calls = []          # (first_frame, frames, threads) per parse_into call

    def contents(self):
        return {"positions": True, "numbers": True, "cell": False, "energies": ["dft"],
                "forces": ["dft"], "frame_fields": ["charge"], "atom_fields": ["weight"],
                "units": {}}

    @staticmethod
    def parse(data, first_frame):
        ToyTextReader.parsed.append((first_frame, data.count(b"\n")))
        frames = [json.loads(line) for line in data.split(b"\n") if line.strip()]
        out = {
            "n_atoms": np.array([len(f["numbers"]) for f in frames], dtype=np.int64),
            "numbers": np.concatenate([f["numbers"] for f in frames]).astype(np.int64),
            "positions": np.concatenate([np.reshape(f["positions"], (-1, 3)) for f in frames]),
            "forces:dft": np.concatenate([np.reshape(f["forces"], (-1, 3)) for f in frames]),
        }
        energy = _values(frames, "energy")
        if energy is not None:
            out["energy:dft"] = energy
        charge = _values(frames, "charge")
        if charge is not None:
            out["frame:charge"] = charge.astype(np.float64)
        for key in ("weight", "late"):
            column = _column(frames, key)
            if column is not None:
                out[f"atom:{key}"] = column
        return out

    @staticmethod
    def parse_into(path, starts, ends, counts, first_frame, arrays, threads):
        ToyFastReader.calls.append((first_frame, len(starts), threads))
        with open(path, "rb") as fh:
            frames = []
            for a, b in zip(starts.tolist(), ends.tolist()):
                fh.seek(a)
                frames.append(json.loads(fh.read(b - a)))
        numbers, texts, late, filled = {}, {}, ([], []), set()
        at = 0
        for i, f in enumerate(frames):
            if f.get("fast") == "no":
                raise NotFast(f"frame {first_frame + i}: the toy says no")
            n = int(counts[i])
            arrays["numbers"][at:at + n] = f["numbers"]
            arrays["positions"][at:at + n] = np.reshape(f["positions"], (-1, 3))
            arrays["forces:dft"][at:at + n] = np.reshape(f["forces"], (-1, 3))
            filled.add("forces:dft")
            if "weight" in f:
                arrays["atom:weight"][at:at + n] = f["weight"]
                filled.add("atom:weight")
            if "late" in f:
                late[0].extend(range(at, at + n))
                late[1].extend(f["late"])
            for key, name in (("energy", "energy:dft"), ("charge", "frame:charge")):
                if key in f:
                    rows, values = (texts if isinstance(f[key], str) else numbers).setdefault(name, ([], []))
                    rows.append(i)
                    values.append(f[key])
            at += n
        out = {"frame": {k: (np.array(r), np.array(v)) for k, (r, v) in numbers.items()},
               "text": {k: (np.array(r), v) for k, (r, v) in texts.items()},
               "filled": sorted(filled)}
        if late[0]:
            out["atom_new"] = {"atom:late": (np.array(late[0]), np.array(late[1], dtype=float))}
        return out
