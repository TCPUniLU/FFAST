"""``check_reader``: does a reader follow the rules of ADR 0095?

An author calls it from a test::

    def test_my_reader():
        check_reader(FooReader, "tests/data/small.foo")

It raises ``AssertionError`` with a plain message naming the rule broken.
"""
from __future__ import annotations

import numpy as np

_METHODS = ("n_frames", "contents", "read")


def check_reader(reader_class, sample_file: str) -> None:
    """Check ``reader_class`` against ``sample_file``, a small file it reads
    (at least a few frames, so chunking is tested)."""
    _check_attributes(reader_class)
    reader = _call("the constructor", reader_class, sample_file)
    n = _call("n_frames()", reader.n_frames)
    _expect(isinstance(n, (int, np.integer)) and n >= 1,
            f"n_frames() must return a whole number of at least 1, got {n!r}")
    contents = _call("contents()", reader.contents)
    _expect(isinstance(contents, dict), "contents() must return a dict")

    everything = list(range(n))
    whole = _call(f"read() of all {n} frames (does n_frames() match the file?)",
                  reader.read, everything)
    _check_arrays(whole, n, contents, "read(all frames)")

    # Frames asked in chunks must equal frames asked at once.
    pieces = [_call(f"read([{i}])", reader_class(sample_file).read, [i]) for i in everything]
    for key in whole:
        joined = np.concatenate([np.asarray(p[key]) for p in pieces])
        _expect(np.array_equal(joined, np.asarray(whole[key]), equal_nan=_is_float(whole[key])),
                f"'{key}' differs when frames are asked in chunks and when asked at once. "
                f"read() must return frames in the order asked (increasing order) and give "
                f"the same values for a frame whatever else is asked with it")
    if n > 2:
        stride = list(range(0, n, 2))
        part = _call(f"read({stride})", reader_class(sample_file).read, stride)
        _check_arrays(part, len(stride), contents, f"read({stride})")


def _check_attributes(cls) -> None:
    name = getattr(cls, "name", None)
    _expect(isinstance(name, str) and name.strip(), "the reader needs a name: a non-empty string")
    ext = getattr(cls, "extensions", None)
    _expect(isinstance(ext, (list, tuple)) and ext and all(isinstance(e, str) for e in ext),
            "extensions must be a list of patterns such as [\"*.foo\"]")
    for method in _METHODS:
        _expect(callable(getattr(cls, method, None)), f"the reader needs a {method}() method")


def _check_arrays(got, n_frames, contents, what) -> None:
    _expect(isinstance(got, dict), f"{what} must return a dict of arrays")
    _expect("n_atoms" in got, f"{what} must return 'n_atoms', the atom count of each frame")
    counts = np.asarray(got["n_atoms"])
    _expect(counts.shape == (n_frames,) and np.issubdtype(counts.dtype, np.integer),
            f"'n_atoms' must be integers, one value per frame ({n_frames}); got shape {counts.shape}")
    atoms = int(counts.sum())

    promised = _promised(contents)
    for key in got:
        _expect(key in promised or key == "n_atoms",
                f"read() returned '{key}', which contents() does not list")
    for key in promised:
        _expect(key in got, f"contents() lists '{key}' but {what} did not return it")

    for key, value in got.items():
        arr = np.asarray(value)
        _expect(arr.dtype != object, f"'{key}' must be a numeric array, not objects")
        if key == "numbers":
            _expect(arr.shape == (atoms,) and np.issubdtype(arr.dtype, np.integer),
                    f"'numbers' must be integers, one value per atom ({atoms}); got shape "
                    f"{arr.shape} and dtype {arr.dtype}")
        elif key == "positions" or key.startswith("forces:"):
            _expect(arr.shape == (atoms, 3), f"'{key}' must have shape ({atoms}, 3), "
                                             f"one row per atom; its shape is {arr.shape}")
        elif key == "cell":
            _expect(arr.shape == (n_frames, 3, 3),
                    f"'cell' must have shape ({n_frames}, 3, 3); its shape is {arr.shape}")
        elif key.startswith("energy:"):
            _expect(arr.shape == (n_frames,),
                    f"'{key}' must have one value per frame ({n_frames}); its shape is {arr.shape}")
        elif key.startswith("frame:"):
            _expect(arr.shape[:1] == (n_frames,),
                    f"'{key}' must have one value per frame ({n_frames}); its shape is {arr.shape}")
        elif key.startswith("atom:"):
            _expect(arr.shape[:1] == (atoms,),
                    f"'{key}' must have one row per atom ({atoms}); its shape is {arr.shape}")


def _promised(contents) -> list[str]:
    keys = []
    if contents.get("numbers"):
        keys.append("numbers")
    if contents.get("positions"):
        keys.append("positions")
    if contents.get("cell"):
        keys.append("cell")
    keys += [f"energy:{n}" for n in contents.get("energies") or []]
    keys += [f"forces:{n}" for n in contents.get("forces") or []]
    keys += [f"frame:{n}" for n in contents.get("frame_fields") or []]
    keys += [f"atom:{n}" for n in contents.get("atom_fields") or []]
    return keys


def _is_float(value) -> bool:
    return np.issubdtype(np.asarray(value).dtype, np.floating)


def _call(what, fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        raise AssertionError(f"{what} raised {type(exc).__name__}: {exc}") from exc


def _expect(condition, message) -> None:
    if not condition:
        raise AssertionError(message)
