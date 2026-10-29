"""What the sidebar and the top panel's hover text say about a dataset or model.

Both read one description, so they never disagree. A subset also says which
of its parent's frames or atoms it holds, in the parent's numbering.
"""
import types

from ase import Atoms
import numpy as np

from client.object_info import describe, tooltipHtml
from ffast.loaders.ase import aseDatasetLoader
from ffast.loaders.dataset import DatasetView, frameRanges


def _parent(n=100):
    parent = aseDatasetLoader("water.xyz", atomsList=[Atoms("H2O") for _ in range(n)])
    parent.initialise()
    return parent


def _info(dataset):
    return dict(dataset.getInfo())


# ── frame numbers, as a person reads them ──────────────────────────────────

def test_frames_are_shown_as_ranges():
    assert frameRanges(np.arange(20, 50)) == "20-49"
    assert frameRanges([3, 5, 7, 8, 9]) == "3, 5, 7-9"
    assert frameRanges([4]) == "4"


def test_a_long_list_of_ranges_is_cut():
    assert frameRanges(np.arange(0, 100, 2), limit=3) == "0, 2, 4, … (50 frames)"


# ── what each kind says ─────────────────────────────────────────────────────

def test_a_frame_subset_says_which_parent_frames_it_holds():
    parent = _parent()
    live = DatasetView.subset(parent, None, np.arange(20, 50), "Energy")
    assert _info(live)["Frames"] == "20-49 (30 of 100)"
    frozen = DatasetView.frozenSubset(parent, None, np.array([3, 5, 6]), "Energy")
    assert _info(frozen)["Frames"] == "3, 5-6 (3 of 100)"


def test_an_atom_subset_says_which_atoms_it_keeps():
    atoms = DatasetView.atomSubset(_parent(), [0, 2])
    assert _info(atoms)["Atoms"] == "0, 2"
    assert "Frames" not in _info(atoms)


def test_describe_gives_the_kind_name_and_the_sidebars_lines():
    parent = _parent()
    live = DatasetView.subset(parent, None, np.arange(20, 50), "Energy")
    live.initialise()
    title, kind, pairs = describe(live)
    assert kind == "SubDataset"
    assert title == live.getDisplayName()
    assert pairs == live.getBaseInfo() + live.getInfo()


def test_a_model_is_described_by_its_own_lines():
    model = types.SimpleNamespace(
        modelName="Prediction", getDisplayName=lambda: "prediction.xyz",
        getInfo=lambda: [("Energy key", "MACE_energy")])
    assert describe(model) == ("prediction.xyz", "Prediction", [("Energy key", "MACE_energy")])


def test_the_hover_text_lists_every_line():
    model = types.SimpleNamespace(
        modelName="Prediction", getDisplayName=lambda: "pred <1>.xyz",
        getInfo=lambda: [("Energy key", "MACE_energy")])
    html = tooltipHtml(model)
    assert "pred &lt;1&gt;.xyz" in html            # names are escaped
    assert "Prediction" in html and "Energy key" in html and "MACE_energy" in html
