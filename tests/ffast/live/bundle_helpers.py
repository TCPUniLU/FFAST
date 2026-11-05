"""Synthetic aspirin training data and bundles for live-run tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
INITIAL = HERE / "aspirin_initial.xyz"


def initial_atoms():
    from ase.io import read

    return read(str(INITIAL))


def write_frames(path: Path, n: int, seed: int, sigma: float = 0.02) -> Path:
    """``n`` frames of aspirin with small random displacements, extxyz."""
    from ase.io import write

    base = initial_atoms()
    rng = np.random.default_rng(seed)
    frames = []
    for _ in range(n):
        a = base.copy()
        a.positions = base.positions + rng.normal(0.0, sigma, size=base.positions.shape)
        frames.append(a)
    write(str(path), frames, format="extxyz")
    return path


def make_bundle(tmp_path: Path, every: int = 100) -> tuple[Path, Path]:
    """Run ``ffast bundle`` on synthetic data; return (bundle dir, include file)."""
    from ffast.live.cli import main

    train = write_frames(tmp_path / "train.xyz", 300, seed=1)
    valid = write_frames(tmp_path / "valid.xyz", 150, seed=2)
    rc = main(["bundle", "--train", str(train), "--valid", str(valid),
               "--elements", "C H O", "-o", str(tmp_path / "aspirin"), "--every", str(every)])
    assert rc == 0
    return tmp_path / "aspirin.bundle", tmp_path / "aspirin.lmp"
