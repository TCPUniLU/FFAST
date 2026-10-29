"""SUB makes subsets of what a plot draws, but not of a live subset.

Regression: a desktop plot draws every active dataset, the subset its own SUB
just made included, so the next refresh made a subset of that subset. On a
zoom every subset's frames were worked out first and then applied in turn:
the SUB shrank, and its own subset was then given frames the smaller SUB did
not have. The server failed to announce it ("index 8 is out of bounds for
axis 0 with size 3"). A live subset changes with the zoom, so it is never a
SUB's parent; a frozen or atom subset keeps its frames and still is.
"""
import os
import types

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from ase import Atoms  # noqa: E402

from ffast.loaders.ase import aseDatasetLoader  # noqa: E402
from ffast.loaders.dataset import DatasetView  # noqa: E402
from UI.Plots import BasicPlotWidget  # noqa: E402


def test_sub_makes_no_subset_of_a_live_subset():
    parent = aseDatasetLoader("p.xyz", atomsList=[Atoms("H2O") for _ in range(10)])
    parent.initialise()
    live = DatasetView.subset(parent, None, np.array([2, 3]), "Energy")
    frozen = DatasetView.frozenSubset(parent, None, np.array([4, 5]), "Energy")
    atoms = DatasetView.atomSubset(parent, [0, 1])
    plot = types.SimpleNamespace(
        name="Energy", env=None,
        getWatchedData=lambda: [{"dataset": d, "model": None}
                                for d in (parent, live, frozen, atoms)],
        getDatasetSubIndices=lambda dataset, model: np.array([0, 1]),
    )
    parents = [d for d, _, _ in BasicPlotWidget.getValidSubDatasets(plot)]
    assert parents == [parent, frozen, atoms]
