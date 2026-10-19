"""
How much slower is reading one frame under ADR 0058 decision B?

Today a variable dataset reads the whole file into flat arrays at load, and a
frame is a slice of those arrays. Under decision B a frame is read through
AtomsList, the way the uniform loader already works. This script times both,
on the same file, for the three things the 3D view asks per frame:
coordinates, elements, forces.

Run from the repo root:
    ~/.venvs/ffast_env/bin/python docs/research/bench_frames.py
"""
import logging
import os
import time
import tracemalloc

import ase.io
import numpy as np
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.core.data_types import AtomsList
from ffast.loaders.ase import VariableASEDatasetLoader

logging.disable(logging.CRITICAL)  # the loaders log every chunk read

PATH = "temp/bench_variable.xyz"  # temp/ is ignored by git; the file is made on first run
N_FRAMES = 5000
RANDOM_READS = 200   # random reads per run; a cache miss re-reads the file, so keep this small
SMALL_CHUNK = 500    # frames per chunk when pretending the file is too big for one chunk
REPEATS = 3          # each timing is repeated; the fastest run is reported (least disturbed by other programs)


# ---- part 1: a test file, made once ---------------------------------------

def make_file():
    rng = np.random.default_rng(0)  # fixed seed: the same file every time
    frames = []
    for _ in range(N_FRAMES):
        n = int(rng.integers(10, 60))  # different sizes: a variable dataset
        atoms = Atoms(numbers=rng.integers(1, 9, size=n),
                      positions=rng.normal(size=(n, 3)) * 5)
        atoms.calc = SinglePointCalculator(
            atoms, energy=float(rng.normal()), forces=rng.normal(size=(n, 3)))
        frames.append(atoms)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    ase.io.write(PATH, frames, format="extxyz")


# ---- part 2: the two ways of reading one frame ----------------------------

def read_today(dataset, i):
    """Variable loader today: slices of arrays already in memory."""
    return dataset.getCoordinates(i), dataset.getElements(i), dataset.getForces(i)


def read_option_b(atoms_list, i):
    """Decision B: ask the file reader for frame i."""
    atoms = atoms_list[i]
    return atoms.get_positions(), atoms.numbers, atoms.get_forces()


# ---- part 3: timing helpers -----------------------------------------------

def best_time(fn):
    """Seconds taken by fn(), fastest of REPEATS runs."""
    times = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return min(times)


def per_read_us(read, source, frames):
    """Microseconds per frame read, reading `frames` in the given order."""
    seconds = best_time(lambda: [read(source, int(i)) for i in frames])
    return seconds / len(frames) * 1e6


def held_mb(build):
    """Memory still held by what build() returns, in MB."""
    tracemalloc.start()
    obj = build()
    held, _peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return obj, held / 1e6


# ---- part 4: run ----------------------------------------------------------

def main():
    if not os.path.exists(PATH):
        print(f"making {PATH} ...")
        make_file()
    print(f"file: {os.path.getsize(PATH) / 1e6:.1f} MB, {N_FRAMES} frames\n")

    rng = np.random.default_rng(1)
    in_order = np.arange(N_FRAMES)
    random_order = rng.integers(0, N_FRAMES, size=RANDOM_READS)

    start = time.perf_counter()
    today, today_mb = held_mb(lambda: VariableASEDatasetLoader(PATH, atomsList=AtomsList(PATH)))
    today_load = time.perf_counter() - start

    start = time.perf_counter()
    whole, whole_mb = held_mb(lambda: AtomsList(PATH))
    whole_load = time.perf_counter() - start

    small = AtomsList(PATH, atoms_chunk=SMALL_CHUNK)

    rows = [
        ("today (arrays in memory)", read_today, today, today_load, today_mb),
        ("B, file fits in one chunk", read_option_b, whole, whole_load, whole_mb),
        (f"B, big file ({SMALL_CHUNK}-frame chunks)", read_option_b, small, None, None),
    ]

    print(f"{'':38}{'load s':>8}{'held MB':>9}{'in order µs':>13}{'random µs':>11}")
    for name, read, source, load_s, mb in rows:
        ordered = per_read_us(read, source, in_order)
        rand = per_read_us(read, source, random_order)
        load = f"{load_s:8.2f}" if load_s is not None else f"{'-':>8}"
        held = f"{mb:9.1f}" if mb is not None else f"{'-':>9}"
        print(f"{name:38}{load}{held}{ordered:13.1f}{rand:11.1f}")


if __name__ == "__main__":
    main()
