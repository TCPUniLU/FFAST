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
    (at least a few frames, so chunking is tested). A Level 2 reader (with
    ``find_frames`` and ``parse``) is checked on those instead of
    ``n_frames`` and ``read``."""
    if _is_level2(reader_class):
        return _check_level2(reader_class, sample_file)
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

    if callable(getattr(reader_class, "open_writer", None)):
        _check_round_trip(reader_class, sample_file, whole, contents)


def _check_round_trip(cls, sample_file, whole, contents) -> None:
    """Write what was read, read it back: the numbers must be the same. The
    first format of ``write_formats`` that can hold the sample is tried."""
    import os
    import tempfile

    if "positions" not in whole or "numbers" not in whole:
        return                       # a file of values only (a prediction): nothing to write alone
    n = len(whole["n_atoms"])
    variable = len(set(np.asarray(whole["n_atoms"]).tolist())) > 1
    sets = len(contents.get("energies") or [])
    for info in getattr(cls, "write_formats", []):
        can = info.get("can_write") or {}
        if (can.get("frames", n) < n or (variable and can.get("variable_atoms", True) is False)
                or (can.get("energies") is not None and can["energies"] < max(sets, 0))):
            continue
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "roundtrip" + info["ext"])
            writer = _call(f"open_writer() for {info['format']}", cls.open_writer, path, contents,
                           info["format"])
            _call("write()", writer.write, whole)
            _call("close()", writer.close)
            back = _call(f"reading back the {info['format']} file", _read_all, cls, path, n)
        skip = {"cell"} if can.get("cell") is False else set()
        for key in whole:
            if key in skip or key not in back:
                continue
            a, b = np.asarray(back[key]), np.asarray(whole[key])
            same = (np.allclose(a, b, rtol=1e-6, atol=1e-6, equal_nan=True)
                    if _is_float(b) and a.shape == b.shape else np.array_equal(a, b))   # text keeps ~8 digits
            _expect(same,
                    f"'{key}' differs between what was written and read back "
                    f"({info['format']}): the writer and the reader must agree")
        return


def _read_all(cls, path, n):
    """Every frame of ``path``: through ``read`` (Level 1), or ``parse`` of
    the whole file (Level 2)."""
    if not _is_level2(cls):
        return cls(path).read(list(range(n)))
    starts, _ = cls.find_frames(path)
    with open(path, "rb") as f:
        f.seek(int(starts[0]))
        return cls.parse(f.read(int(starts[-1]) - int(starts[0])), 0)


def _is_level2(cls) -> bool:
    return callable(getattr(cls, "find_frames", None)) or callable(getattr(cls, "parse", None))


def _check_level2(cls, sample_file) -> None:
    import pickle

    name = getattr(cls, "name", None)
    _expect(isinstance(name, str) and name.strip(), "the reader needs a name: a non-empty string")
    ext = getattr(cls, "extensions", None)
    _expect(isinstance(ext, (list, tuple)) and ext and all(isinstance(e, str) for e in ext),
            "extensions must be a list of patterns such as [\"*.foo\"]")
    for method in ("contents", "find_frames", "parse"):
        _expect(callable(getattr(cls, method, None)), f"a Level 2 reader needs a {method}() method")
    try:
        pickle.dumps(cls.parse)
        pickle.dumps(cls)
    except Exception as exc:
        raise AssertionError(
            f"parse() runs in worker processes, so it must be picklable (a staticmethod "
            f"or a top-level function, on a class that can be imported): {exc}") from exc

    contents = _call("contents()", _call("the constructor", cls, sample_file).contents)
    _expect(isinstance(contents, dict), "contents() must return a dict")
    found = _call("find_frames()", cls.find_frames, sample_file)
    _expect(isinstance(found, tuple) and len(found) == 2,
            "find_frames() must return (starts, atom counts)")
    starts, counts = np.asarray(found[0]), np.asarray(found[1])
    n = len(counts)
    _expect(n >= 1 and len(starts) == n + 1 and (np.diff(starts) > 0).all(),
            f"find_frames() must return {n + 1} increasing starts for {n} frames "
            f"(the last is where the last frame ends); got {len(starts)}")
    _expect(np.issubdtype(counts.dtype, np.integer) and (counts >= 0).all(),
            "the atom counts must be whole numbers, one per frame")

    def parse(a, b):
        with open(sample_file, "rb") as f:
            f.seek(int(starts[a]))
            data = f.read(int(starts[b]) - int(starts[a]))
        return _call(f"parse() of frames {a} to {b - 1}", cls.parse, data, a)

    whole = parse(0, n)
    _check_arrays(whole, n, contents, f"parse() of all {n} frames")
    _expect(np.array_equal(whole["n_atoms"], counts),
            "parse() gives a different atom count per frame than find_frames()")
    from ffast.readers.text import per_atom

    pieces = [parse(i, i + 1) for i in range(n)]
    for key in whole:
        # A frame range that has no value for a key may leave the key out: NaN there.
        sample = np.asarray(whole[key])
        parts = []
        for piece, count in zip(pieces, counts):
            if key in piece:
                parts.append(np.asarray(piece[key]))
            else:
                rows = int(count) if per_atom(key) else 1
                parts.append(np.full((rows,) + sample.shape[1:], np.nan))
        joined = np.concatenate(parts)
        _expect(np.array_equal(joined, sample, equal_nan=_is_float(whole[key])),
                f"'{key}' differs when frames are parsed one at a time and all at once: "
                f"parse() must give the same values for a frame whatever else is parsed with it")

    if callable(getattr(cls, "energies", None)):
        _check_energies(cls, sample_file, starts[:-1], whole)

    if callable(getattr(cls, "open_writer", None)):
        _check_round_trip(cls, sample_file, whole, contents)


def _check_energies(cls, sample_file, starts, whole) -> None:
    """``energies(path, starts)`` (ADR 0096): one float per frame for each
    name, the same values ``parse`` gives as ``energy:<name>``, and the same
    for a frame whatever other frames are asked for with it (FFAST splits the
    frames across worker processes)."""
    import pickle

    try:
        pickle.dumps(cls.energies)
    except Exception as exc:
        raise AssertionError(
            f"energies() runs in worker processes, so it must be picklable (a staticmethod "
            f"or a top-level function, on a class that can be imported): {exc}") from exc
    n = len(starts)

    def ask(frames, what):
        got = _call(what, cls.energies, sample_file, frames)
        _expect(isinstance(got, dict), f"{what} must return a dict of name -> array")
        for name, values in got.items():
            arr = np.asarray(values)
            _expect(arr.shape == (len(frames),) and _is_float(arr),
                    f"energies() must give '{name}' as floats, one value per frame "
                    f"({len(frames)}); got shape {arr.shape} and dtype {arr.dtype}")
        return got

    every = ask(starts, f"energies() of all {n} frames")
    half = n // 2
    first, second = ask(starts[:half], "energies() of the first half"), ask(starts[half:], "energies() of the second half")
    for name, values in every.items():
        parts = [np.asarray(p.get(name, np.full(len(s), np.nan)), dtype=float)
                 for p, s in ((first, starts[:half]), (second, starts[half:]))]
        _expect(np.array_equal(np.concatenate(parts), np.asarray(values, dtype=float), equal_nan=True),
                f"energies() gives other values of '{name}' when the frames are asked for in two "
                f"halves: a frame's energy must not depend on the frames asked with it")
    for name, values in every.items():
        key = f"energy:{name}"
        _expect(key in whole,
                f"energies() lists '{name}', but parse() returns no '{key}': list only the "
                f"energies parse() gives")
        _expect(np.array_equal(np.asarray(values, dtype=float), np.asarray(whole[key], dtype=float),
                               equal_nan=True),
                f"energies() and parse() give different values of '{name}': energies() must "
                f"read the same numbers parse() does")


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
