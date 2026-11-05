"""Write LAMMPS dumps in the layout FFAST's include file makes LAMMPS write.

Header order follows LAMMPS's ``DumpCustom::header_item``: ``UNITS`` (first
frame only), ``TIME``, ``TIMESTEP``, ``NUMBER OF ATOMS``, ``BOX BOUNDS``,
``ATOMS``. Floats use ``%.17g`` as ``dump_modify format float %.17g`` does.
"""

from __future__ import annotations

import numpy as np

ASPIRIN_ELEMENTS = ("C", "C", "C", "C", "C", "C", "C", "O", "O", "O", "C",
                    "C", "O", "H", "H", "H", "H", "H", "H", "H", "H")
TYPE_ORDER = ("C", "H", "O")


def frame_text(step: int, positions, elements, *, dt: float = 0.0005,
               units: str | None = None) -> str:
    """One frame; ``units`` given only for the first frame, as LAMMPS does."""
    pos = np.asarray(positions, dtype=np.float64)
    lines = []
    if units is not None:
        lines += ["ITEM: UNITS", units]
    lines += ["ITEM: TIME", "%.16g" % (step * dt)]
    lines += ["ITEM: TIMESTEP", str(step)]
    lines += ["ITEM: NUMBER OF ATOMS", str(len(pos))]
    lo, hi = pos.min(axis=0) - 1.0, pos.max(axis=0) + 1.0
    lines += ["ITEM: BOX BOUNDS ss ss ss"] + ["%.17g %.17g" % (a, b) for a, b in zip(lo, hi)]
    lines += ["ITEM: ATOMS id type element x y z"]
    for i, (e, (x, y, z)) in enumerate(zip(elements, pos)):
        lines.append("%d %d %s %.17g %.17g %.17g" % (i + 1, TYPE_ORDER.index(e) + 1, e, x, y, z))
    return "\n".join(lines) + "\n"


def dump_text(frames, elements=ASPIRIN_ELEMENTS, *, every: int = 100,
              units: str = "metal", dt: float = 0.0005) -> str:
    """A whole dump: ``frames`` is a sequence of (n_atoms, 3) arrays."""
    return "".join(
        frame_text(k * every, p, elements, dt=dt, units=units if k == 0 else None)
        for k, p in enumerate(frames)
    )


def random_frames(n_frames: int, n_atoms: int = len(ASPIRIN_ELEMENTS), seed: int = 0):
    rng = np.random.default_rng(seed)
    base = rng.uniform(30.0, 40.0, size=(n_atoms, 3))
    return [base + rng.normal(0.0, 0.05, size=base.shape) for _ in range(n_frames)]
