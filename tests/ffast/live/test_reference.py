"""Making a reference bundle from frames in memory (the web dialog's path)."""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from ffast.live.reference import ReferenceRefused, dataset_frames, elements_in_order, make_reference
from tests.ffast.live.bundle_helpers import initial_atoms


def _frames(n, seed):
    base = initial_atoms().get_positions()
    rng = np.random.default_rng(seed)
    return base + rng.normal(0.0, 0.02, size=(n,) + base.shape)


SYMBOLS = initial_atoms().get_chemical_symbols()


def test_a_bundle_and_include_are_written_and_load(tmp_path):
    import detectana

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        got = make_reference(_frames(300, 1), _frames(150, 2), SYMBOLS, tmp_path / "ref" / "aspirin")
    assert got["bundle"].endswith("aspirin.bundle") and got["include"].endswith("aspirin.lmp")
    assert got["elements"] == elements_in_order(SYMBOLS) == list(dict.fromkeys(SYMBOLS))
    assert got["lines"][0] == f"include {got['include']}"
    bundle = detectana.load(got["bundle"])
    assert bundle.n_atoms == 21 and [list(t) for t in bundle.tests] == got["tests"]
    text = open(got["include"]).read()
    assert f"Reference bundle: {got['bundle']}" in text


def _move_apart(frames, i, j, distance):
    """Atoms i and j of each frame moved along their line to ``distance`` Å apart."""
    out = frames.copy()
    for f in out:
        mid = (f[i] + f[j]) / 2
        u = (f[j] - f[i]) / np.linalg.norm(f[j] - f[i])
        f[i], f[j] = mid - u * distance / 2, mid + u * distance / 2
    return out


def _fit(train, valid, out):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return make_reference(train, valid, SYMBOLS, out)


def test_bonds_come_from_most_frames_not_a_squeezed_first_frame(tmp_path):
    # Stop proof 2026-11-06: a hot first training frame had C0–C10 at 2.23 Å, the
    # covalent-radii search called it a bond, and every live frame counted it broken.
    import detectana

    train = _frames(300, 1)
    train[:1] = _move_apart(train[:1], 0, 10, 2.2)
    got = _fit(train, _frames(150, 2), tmp_path / "squeezed")
    bonds = [tuple(b) for b in detectana.load(got["bundle"])._topology.bonds]
    assert (0, 10) not in bonds and (0, 14) in bonds
    assert got["n_bonds"] == len(bonds) == 21 and got["unsure_bonds"] == []


def test_a_reference_whose_frames_break_a_bond_is_refused(tmp_path):
    train = _frames(300, 1)
    train[:20] = _move_apart(train[:20], 0, 14, 2.6)   # C0–H14 broken in 20 of 300 frames
    with pytest.raises(ReferenceRefused, match=r"20 of 300 training frames .*C0–H14 2\.60 Å"):
        _fit(train, _frames(150, 2), tmp_path / "broken")


def test_without_rdkit_the_reference_is_refused_naming_it(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "rdkit", None)
    with pytest.raises(ReferenceRefused, match="pip install rdkit"):
        _fit(_frames(30, 1), _frames(20, 2), tmp_path / "nordkit")


@pytest.mark.parametrize("case, text", [
    ("same", "same frames"),
    ("order", "another order"),
    ("missing", "lacks"),
    ("space", "contains a space"),
])
def test_refusals(tmp_path, case, text):
    train = _frames(30, 1)
    kw = {}
    valid = train.copy() if case == "same" else _frames(20, 2)
    if case == "order":
        kw["valid_symbols"] = list(reversed(SYMBOLS))
    if case == "missing":
        kw["elements"] = ["C", "H"]
    out = tmp_path / ("a b" if case == "space" else "ok") / "x"
    with pytest.raises(ReferenceRefused, match=text):
        make_reference(train, valid, SYMBOLS, out, **kw)


class _DS:
    isVariable = False

    def __init__(self, pos, z):
        self.pos, self.z = pos, z

    def getN(self):
        return len(self.pos)

    def getElements(self, k):
        return self.z[k]

    def getCoordinates(self, idx):
        return self.pos[idx]


def test_dataset_frames_reads_symbols_and_positions():
    pos = _frames(4, 3)
    z = np.tile(initial_atoms().get_atomic_numbers(), (4, 1))
    symbols, got = dataset_frames(_DS(pos, z), "train")
    assert symbols == SYMBOLS and np.array_equal(got, pos)
    z[2, [0, 3]] = z[2, [3, 0]] if z[2, 0] != z[2, 3] else (8, 1)
    with pytest.raises(ReferenceRefused, match="frame 2"):
        dataset_frames(_DS(pos, z), "train")
