"""The bond graph of a reference, found over its frames (plan step 4).

One geometry is a poor source for the bonds: a thermal frame can squeeze two
non-bonded atoms close or stretch a real bond (stop proof, 2026-11-06: a
first training frame gave DetectAna's covalent-radii search a C–C "bond" of
2.23 Å, and every live frame then counted it as broken). Here RDKit finds the
bonds of each frame (``rdDetermineBonds.DetermineConnectivity``, connectivity
only) and a bond is kept when it is found in more than half of the frames.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

UNSURE = (0.1, 0.9)   # a bond found in this share of frames is reported as unsure


def frame_bonds(symbols: list[str], positions: np.ndarray) -> set[tuple[int, int]]:
    """The bonds RDKit finds in one geometry, as sorted 0-based pairs."""
    from rdkit import Chem
    from rdkit.Chem import rdDetermineBonds
    from rdkit.Geometry import Point3D

    mol = Chem.RWMol()
    for s in symbols:
        mol.AddAtom(Chem.Atom(s))
    conf = Chem.Conformer(len(symbols))
    for i, p in enumerate(np.asarray(positions, dtype=float)):
        conf.SetAtomPosition(i, Point3D(*map(float, p)))
    mol.AddConformer(conf)
    rdDetermineBonds.DetermineConnectivity(mol, useHueckel=False)
    out = set()
    for b in mol.GetBonds():
        i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
        out.add((min(i, j), max(i, j)))
    return out


def consensus_bonds(symbols: list[str], frames: np.ndarray) -> tuple[list[tuple[int, int]], dict]:
    """(bonds found in more than half of ``frames``, {pair: share} of the unsure ones).

    Raises ImportError naming the package when RDKit is missing.
    """
    try:
        import rdkit  # noqa: F401
    except ImportError as e:
        raise ImportError("RDKit finds the bonds of a reference: pip install rdkit") from e
    frames = np.asarray(frames, dtype=float)
    counts: Counter = Counter()
    for pos in frames:
        counts.update(frame_bonds(symbols, pos))
    n = len(frames)
    share = {pair: c / n for pair, c in counts.items()}
    bonds = sorted(p for p, s in share.items() if s > 0.5)
    unsure = {p: round(s, 3) for p, s in sorted(share.items()) if UNSURE[0] <= s <= UNSURE[1]}
    return bonds, unsure
