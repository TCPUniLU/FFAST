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
