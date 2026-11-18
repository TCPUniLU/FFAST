"""Reproducible warm-file XYZ memory check; this is not a disk-speed benchmark.

Run from the repository with its environment: python benchmarks/xyz_memory.py
"""

import argparse
import hashlib
import json
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import psutil

from ffast.io import budget as resources
from ffast.io import frame_cache, frame_index
from ffast.io.extxyz_arrays import NotEnoughMemory
from ffast.metrics.expr import _ExprFn, _OFFSETS
from ffast.readers import chunks
from ffast.readers.dataset import ReaderDataset
from ffast.readers.xyz_reader import XyzReader


def run(folder, frames, atoms, ram_mib):
    path = folder / "large.xyz"
    rhash, fhash = hashlib.md5(), hashlib.md5()
    forces = np.tile([0.1, 0.2, 0.3], (atoms, 1))
    with path.open("w") as out:
        for i in range(frames):
            out.write(
                f"{atoms}\nProperties=species:S:1:pos:R:3:forces:R:3 energy={i}\n"
            )
            out.write(f"C {i}.125 0.25 -1.5 0.1 0.2 0.3\n" * atoms)
            rhash.update(np.tile([i + 0.125, 0.25, -1.5], (atoms, 1)))
            fhash.update(forces)
    expected = hashlib.md5(
        b"".join(
            [
                hashlib.md5(np.full(atoms, 6, dtype=np.int64)).digest(),
                rhash.digest(),
                hashlib.md5(np.arange(frames, dtype=np.int64)).digest(),
                fhash.digest(),
            ]
        )
    ).hexdigest()
    frame_index.CACHE_DIR = folder / "index"
    frame_cache.CACHE_DIR = folder / "parsed"
    owner = resources.configure(
        ram_bytes=ram_mib * 2**20, parsed_bytes=0, result_bytes=0
    )
    process = psutil.Process()
    baseline = process.memory_info().rss
    peak = [baseline]
    finished = threading.Event()

    def sample():
        while not finished.wait(0.005):
            peak[0] = max(peak[0], process.memory_info().rss)

    monitor = threading.Thread(target=sample)
    monitor.start()
    start = time.perf_counter()
    try:
        arrays = chunks.read_arrays(
            XyzReader, str(path), workers=4, use_cache=False
        )
        ds = ReaderDataset(str(path), arrays, "xyz")
        seconds = time.perf_counter() - start
        assert ds.getFingerprint() == expected
        assert (
            ds.memory_mode == "saving"
        ), "Choose a larger file or smaller RAM budget"
        frame = frames // 2
        assert ds.getCoordinates(frame)[0, 0] == frame + 0.125
        rejected = False
        try:
            np.asarray(ds.F_flat)
        except NotEnoughMemory:
            rejected = True
        assert (
            rejected
        ), "Choose a larger file to exercise materialization refusal"
        result = _ExprFn(
            "mean(norm(F), by=structure)", {"F": "reference.forces"}, {"F": 1}
        )(F=ds.F_flat, **{_OFFSETS: ds.getOffsets()})
        for start in range(0, frames, 1000):
            np.testing.assert_allclose(
                result[start : start + 1000], np.linalg.norm(forces[0])
            )
    finally:
        finished.set()
        monitor.join()
    return {
        "file_MiB": round(path.stat().st_size / 2**20, 2),
        "frames": frames,
        "atoms": frames * atoms,
        "parsed_core_MiB": round(
            (frames * atoms * 56 + frames * 8) / 2**20, 2
        ),
        "ram_budget_MiB": ram_mib,
        "load_seconds": round(seconds, 2),
        "rss_base_MiB": round(baseline / 2**20, 2),
        "rss_peak_MiB": round(peak[0] / 2**20, 2),
        "rss_growth_MiB": round((peak[0] - baseline) / 2**20, 2),
        "accounted_MiB": round((owner.used + owner.cache_bytes) / 2**20, 2),
        "fingerprint_matches": True,
        "full_materialization_refused": rejected,
        "streamed_analysis_matches": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=50_000)
    parser.add_argument("--atoms", type=int, default=40)
    parser.add_argument("--ram-mib", type=int, default=8)
    args = parser.parse_args()
    if min(args.frames, args.atoms, args.ram_mib) <= 0:
        parser.error("frames, atoms and RAM must be positive")
    with tempfile.TemporaryDirectory(prefix="ffast-memory-bench-") as tmp:
        print(
            json.dumps(
                run(Path(tmp), args.frames, args.atoms, args.ram_mib), indent=2
            )
        )
