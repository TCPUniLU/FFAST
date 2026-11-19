"""The batch set FFAST itself writes for one file: the input of every variant (ADR 0109 §1).

``make_source`` loads a file once through FFAST's own profiler, which writes the
memory-saving batches ``<identity>-<a>-<b>.npz``, and then describes them in
``source.json``. ``describe_batches`` does the describing alone, so tests can start
from hand-made batches.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np

from ffast.readers.indexed import _restore

BATCH_NAME = re.compile(r"^(?P<identity>[0-9a-f]{64})-(?P<a>-?\d+)-(?P<b>-?\d+)\.npz$")
# Columns with one row per frame; every other column has one row per atom.
FRAME_COLUMNS = ("n_atoms", "cell", "pbc")
FRAME_PREFIXES = ("energy:", "frame:")
META_COLUMNS = ("__restore__", "__omitted__")


class SourceError(Exception):
    """The batch set cannot be used; the message says why."""


def column_kind(name):
    return "frame" if name in FRAME_COLUMNS or name.startswith(FRAME_PREFIXES) else "atom"


def read_headers(path):
    """``{member: (shape, dtype)}`` of an npz, without loading the arrays."""
    found = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            with archive.open(name) as member:
                version = np.lib.format.read_magic(member)
                reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                          else np.lib.format.read_array_header_2_0)
                shape, _, dtype = reader(member)
            found[name[:-4]] = (shape, dtype)
    return found


def read_raw(path):
    """Every member of a batch as stored (species still uint8, ``__restore__`` kept)."""
    with np.load(path, allow_pickle=False) as archive:
        return {key: archive[key] for key in archive.files}


def read_restored(path):
    """A batch as FFAST gives it back to a reader: ``_restore`` applied."""
    part = read_raw(path)
    _restore(part)
    return part


def batch_files(batches_dir):
    """``[(a, b, path)]`` of the real windows (a >= 0), in order, of one identity."""
    found = []
    for path in Path(batches_dir).iterdir():
        match = BATCH_NAME.match(path.name)
        if match and int(match["a"]) >= 0:
            found.append((match["identity"], int(match["a"]), int(match["b"]), path))
    if not found:
        raise SourceError(f"no batch files in {batches_dir}")
    identities = {f[0] for f in found}
    if len(identities) > 1:
        raise SourceError(f"batches of {len(identities)} identities in {batches_dir}")
    found.sort(key=lambda f: f[1])
    return found[0][0], [(a, b, p) for _, a, b, p in found]


def describe_batches(out, batches_dir, meta=None):
    """Write ``out/source.json`` and ``out/n_atoms.npy`` for the batches in ``batches_dir``."""
    out, batches_dir = Path(out), Path(batches_dir)
    identity, files = batch_files(batches_dir)
    expect_a = 0
    omitted, per_batch, columns = set(), [], None
    n_atoms = []
    for a, b, path in files:
        if a != expect_a:
            raise SourceError(f"batches are not contiguous: expected a={expect_a}, got {a}")
        expect_a = b
        headers = read_headers(path)
        if "__omitted__" in headers:
            with np.load(path, allow_pickle=False) as archive:
                omitted |= set(archive["__omitted__"].tolist())
        names = {k for k in headers if k not in META_COLUMNS}
        columns = names if columns is None else columns & names
        counts = read_raw_member(path, "n_atoms")
        if len(counts) != b - a:
            raise SourceError(f"{path.name}: n_atoms has {len(counts)} rows for {b - a} frames")
        n_atoms.append(counts)
        per_batch.append({"a": a, "b": b, "file": path.name, "bytes": path.stat().st_size,
                          "frames": b - a, "atoms": int(counts.sum()), "headers": headers})
    n_atoms = np.concatenate(n_atoms)
    first = per_batch[0]["headers"]
    spec, dropped = {}, sorted(set().union(*(set(b["headers"]) for b in per_batch))
                               - columns - set(META_COLUMNS))
    for name in sorted(columns):
        shape, dtype = first[name]
        kind = column_kind(name)
        for batch in per_batch:
            rows = batch["frames"] if kind == "frame" else batch["atoms"]
            got = batch["headers"][name][0]
            if got[0] != rows or got[1:] != shape[1:] or batch["headers"][name][1] != dtype:
                raise SourceError(f"{batch['file']}: column {name} is {got} {batch['headers'][name][1]}, "
                                  f"expected {rows} rows of {shape[1:]} {dtype}")
        spec[name] = {"dtype": dtype.str, "tail": list(shape[1:]), "kind": kind}
    for batch in per_batch:
        batch.pop("headers")
    if not any(k.startswith("forces:") for k in spec) or "positions" not in spec:
        raise SourceError("the batches have no positions or no forces: column")
    np.save(out / "n_atoms.npy", n_atoms)
    info = {
        "identity": identity, "batches_dir": str(batches_dir.relative_to(out)),
        "batches": per_batch, "columns": spec, "columns_dropped": dropped,
        "omitted_in_some_batch": sorted(omitted),
        "frames": int(len(n_atoms)), "atoms": int(n_atoms.sum()),
        "bytes": sum(b["bytes"] for b in per_batch),
        "restore": read_raw(files[0][2]).get("__restore__", np.empty((0, 2), dtype="U1")).tolist(),
        **(meta or {}),
    }
    (out / "source.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def read_raw_member(path, key):
    with np.load(path, allow_pickle=False) as archive:
        return archive[key]


class Source:
    """A described batch set on disk."""

    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.info = json.loads((self.folder / "source.json").read_text())
        self.name = self.info.get("label") or self.folder.name
        self.real = bool(self.info.get("real"))
        self.batches = self.info["batches"]
        self.columns = self.info["columns"]
        self.restore = self.info["restore"]
        self.n_atoms = np.load(self.folder / "n_atoms.npy")
        self.atom_offsets = np.concatenate(([0], np.cumsum(self.n_atoms)))
        self.batch_starts = np.array([b["a"] for b in self.batches])
        self.frames = self.info["frames"]
        self.atoms = self.info["atoms"]
        self.atom_columns = [k for k, v in self.columns.items() if v["kind"] == "atom"]
        self.force_columns = [k for k in self.columns if k.startswith("forces:")]

    def batch_path(self, index):
        return self.folder / self.info["batches_dir"] / self.batches[index]["file"]

    def window_rows(self, index):
        """Row ranges of window ``index`` as ``(frame_start, frame_stop, atom_start, atom_stop)``."""
        batch = self.batches[index]
        return (batch["a"], batch["b"], int(self.atom_offsets[batch["a"]]),
                int(self.atom_offsets[batch["b"]]))

    def locate(self, frame):
        """``(window, local_start, local_stop, global_start, global_stop)`` of one frame's atoms."""
        window = int(np.searchsorted(self.batch_starts, frame, side="right")) - 1
        g0, g1 = int(self.atom_offsets[frame]), int(self.atom_offsets[frame + 1])
        base = int(self.atom_offsets[self.batches[window]["a"]])
        return window, g0 - base, g1 - base, g0, g1

    def frame_rows(self, window, name):
        """Rows ``(start, stop)`` of column ``name`` in window ``window`` of the Zarr arrays."""
        fa, fb, aa, ab = self.window_rows(window)
        return (fa, fb) if self.columns[name]["kind"] == "frame" else (aa, ab)


def make_source(file, out, memory_bytes=3 * 2**30, real=False, label=None, extra_args=()):
    """Load ``file`` once through FFAST's profiler and describe the batches it wrote."""
    file, out = Path(file).resolve(), Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SourceError(f"{out} already exists and is not empty")
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, FFAST_MEMORY_BYTES=str(int(memory_bytes)))
    attempts = []
    for mode in ("auto", "saving"):
        profile = out / "profile"
        shutil.rmtree(profile, ignore_errors=True)
        command = [sys.executable, "-m", "ffast.profiling", str(file), "--runs", "1",
                   "--output", str(profile), "--memory-mode", mode, *extra_args]
        done = subprocess.run(command, env=env, capture_output=True, text=True)
        batches = profile / "cache" / "parsed" / "batches"
        # The batches count when FFAST finished its scan (its manifest, <identity>.json, exists),
        # even if the profiler's later Basic Errors step stopped: only the batches are used here.
        found = batches.is_dir() and any(
            (m := BATCH_NAME.match(p.name)) and int(m["a"]) >= 0
            and (batches / f"{m['identity']}.json").is_file() for p in batches.iterdir())
        attempts.append((mode, done.returncode, found))
        if found:
            break
    else:
        tail = (done.stdout + done.stderr)[-1500:]
        raise SourceError(f"FFAST wrote no batch set for {file.name}: attempts {attempts}\n{tail}")
    shutil.move(str(profile / "cache" / "parsed"), str(out / "ffast-cache"))
    shutil.rmtree(profile / "cache", ignore_errors=True)
    meta = {"label": label or out.name, "real": bool(real), "file_name": file.name,
            "file_bytes": file.stat().st_size, "memory_bytes": int(memory_bytes),
            "memory_mode": mode, "attempts": attempts}
    return describe_batches(out, out / "ffast-cache" / "batches", meta)
