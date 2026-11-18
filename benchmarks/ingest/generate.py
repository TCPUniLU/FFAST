"""Seeded synthetic .extxyz files for the ingestion benchmarks (ADR 0105).

The same case, seed and GENERATOR_VERSION give the same bytes on any machine and
with any number of worker processes: the file is a sequence of chunks, each chunk
has its own random stream, and the parent writes the chunks in order.
"""

import hashlib
import json
import shutil
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

GENERATOR_VERSION = 1
SIZES = {"100MB": 100_000_000, "1GB": 1_000_000_000, "10GB": 10_000_000_000}
CLASSES = {"small": (10, 50, 32), "medium": (50, 500, 256),
           "large": (500, 10_000, 4096)}  # (min, max, constant count)
COUNTS = ("constant", "variable")
SPECIES = np.array(["H", "C", "N", "O"], dtype=object)
# A chunk holds about this many atoms (a few MB of text). Fixed 1024-frame chunks
# would be 1 GB for the large class and overshoot a 100 MB target tenfold.
CHUNK_ATOMS = 40_000
ATOMS_PER_A3 = 0.1
ROW = "%s" + " %.8f" * 9 + "\n"


@dataclass(frozen=True)
class Case:
    size: str
    atoms: str
    counts: str
    seed: int = 0

    @property
    def name(self):
        return f"{self.size}-{self.atoms}-{self.counts}-s{self.seed}-g{GENERATOR_VERSION}"


def chunk_frames(case):
    """Frames per chunk: depends on the atom class only, so it is fixed per case."""
    return max(1, CHUNK_ATOMS // CLASSES[case.atoms][2])


def make_chunk(case, k):
    """Bytes of chunk ``k`` and its (frames, atoms). Top-level so a pool can run it."""
    low, high, constant = CLASSES[case.atoms]
    rng = np.random.default_rng(np.random.SeedSequence([
        case.seed, GENERATOR_VERSION, list(CLASSES).index(case.atoms),
        COUNTS.index(case.counts), k]))
    frames = chunk_frames(case)
    if case.counts == "constant":
        sizes = np.full(frames, constant, dtype=np.int64)
    else:
        sizes = rng.integers(low, high + 1, size=frames)
    total = int(sizes.sum())
    box = (sizes / ATOMS_PER_A3) ** (1 / 3)
    box_each = np.repeat(box, sizes)
    values = np.empty((total, 10), dtype=object)
    values[:, 0] = SPECIES[rng.integers(0, 4, size=total)]
    forces = rng.normal(0.0, 1.0, size=(total, 3))
    values[:, 1:4] = rng.random((total, 3)) * box_each[:, None]
    values[:, 4:7] = forces
    values[:, 7:10] = forces + rng.normal(0.0, 0.05, size=(total, 3))
    energy = -4.0 * sizes + rng.normal(0.0, 1.0, size=frames)
    energy_pred = energy + rng.normal(0.0, 0.01, size=frames)
    parts, start = [], 0
    for i, n in enumerate(sizes.tolist()):
        length = f"{box[i]:.8f}"
        parts.append(
            f'{n}\nLattice="{length} 0 0 0 {length} 0 0 0 {length}" '
            "Properties=species:S:1:pos:R:3:forces:R:3:forces_pred:R:3 "
            f'energy={energy[i]:.8f} energy_pred={energy_pred[i]:.8f} pbc="T T T"\n')
        parts.append((ROW * n) % tuple(values[start:start + n].ravel()))
        start += n
    return "".join(parts).encode("ascii"), frames, total


def _matches(manifest, case, target, path):
    return (manifest.get("generator_version") == GENERATOR_VERSION
            and manifest.get("seed") == case.seed
            and manifest.get("size") == case.size
            and manifest.get("atoms") == case.atoms
            and manifest.get("counts") == case.counts
            and manifest.get("target_bytes") == target
            and path.is_file() and path.stat().st_size == manifest.get("bytes"))


def ensure(case, folder, *, workers=4, target_bytes=None):
    """Return ``(path, manifest)``; reuse the file when its manifest still matches."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = SIZES[case.size] if target_bytes is None else target_bytes
    path = folder / f"{case.name}.extxyz"
    manifest_path = folder / f"{case.name}.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            manifest = {}
        if _matches(manifest, case, target, path):
            return path, manifest
    free = shutil.disk_usage(folder).free
    if free < 1.2 * target:
        raise RuntimeError(
            f"not enough free disk in {folder}: {free / 1e9:.1f} GB free, "
            f"{1.2 * target / 1e9:.1f} GB needed for {case.name}")
    manifest_path.unlink(missing_ok=True)
    started = time.perf_counter()
    temp = path.with_name(path.name + ".tmp")
    digest = hashlib.sha256()
    written = frames = atoms = 0
    try:
        with temp.open("wb") as out:
            for data, n_frames, n_atoms in _chunks(case, workers):
                out.write(data)
                digest.update(data)
                written += len(data)
                frames += n_frames
                atoms += n_atoms
                if written >= target:
                    break
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    manifest = {"generator_version": GENERATOR_VERSION, "seed": case.seed,
                "size": case.size, "atoms": case.atoms, "counts": case.counts,
                "target_bytes": target, "bytes": written, "frames": frames,
                "atoms_total": atoms, "sha256": digest.hexdigest(),
                "properties": "standard", "chunk_frames": chunk_frames(case),
                "seconds": time.perf_counter() - started}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path, manifest


def _chunks(case, workers):
    """Chunks in order. Extra chunks already submitted when the caller stops are dropped."""
    if workers <= 1:
        k = 0
        while True:
            yield make_chunk(case, k)
            k += 1
    window = workers * 2
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {}
        try:
            for k in range(window):
                pending[k] = pool.submit(make_chunk, case, k)
            k = 0
            while True:
                yield pending.pop(k).result()
                pending[k + window] = pool.submit(make_chunk, case, k + window)
                k += 1
        finally:
            for future in pending.values():
                future.cancel()
