"""Attribution of one live frame: DetectAna's RBC ÷ threshold, labelled for the list."""

from __future__ import annotations

import numpy as np
import pytest

from ffast.live.attribution import frame_attribution, group_label
from tests.ffast.live.bundle_helpers import initial_atoms, make_bundle


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    import detectana

    return detectana.load(make_bundle(tmp_path_factory.mktemp("b"))[0])


def _stretched():
    """The first C–H bond pulled 0.4 Å longer."""
    atoms = initial_atoms()
    pos = atoms.get_positions().copy()
    sym = atoms.get_chemical_symbols()
    c, h = 0, 14
    assert (sym[c], sym[h]) == ("C", "H")
    u = pos[h] - pos[c]
    pos[h] += 0.4 * u / np.linalg.norm(u)
    return pos


def test_group_labels_read_like_chemistry():
    assert group_label("bonds:bond_C0-C2_Å") == "bond C0-C2"
    assert group_label("angles:angle_C2-C0-C5_rad") == "angle C2-C0-C5"
    assert group_label("dihedrals:dih_C3-C2-C0-C5") == "dihedral C3-C2-C0-C5"
    assert group_label("ring:ring_planarity_rmsd_Å") == "ring planarity RMSD"


def test_a_stretched_bond_is_the_strongest_group_of_the_bonds_test(bundle):
    got = frame_attribution(bundle, _stretched(), test=("bonds", "T2"))
    assert got["test"] == ["bonds", "T2"]
    top = got["groups"][0]
    assert top["label"] == "bond C0-H14" and top["atoms"] == [0, 14] and top["size"] == 1
    assert got["bond_strength"]["0-14"] == pytest.approx(top["strength"])
    assert got["atom_strength"][14] == pytest.approx(top["strength"])
    assert len(got["atom_strength"]) == 21 and len(got["groups"]) == 10
    strengths = [g["strength"] for g in got["groups"]]
    assert strengths == sorted(strengths, reverse=True)


def test_values_are_rbc_divided_by_the_roles_threshold(bundle):
    pos = _stretched()
    got = frame_attribution(bundle, pos, test=("bonds", "T2"), role="centroid")
    att = bundle.attribute(pos[None], "bonds", score="T2")
    limit = bundle.thresholds("bonds")["T2"]["centroid"]
    assert got["groups"][0]["strength"] == pytest.approx(att.strength[0].max() / limit)
    assert got["ratio"] == pytest.approx(float(bundle.score(pos[None])["bonds"]["T2"][0]) / limit)


def test_without_a_test_the_frames_worst_test_is_explained(bundle):
    got = frame_attribution(bundle, _stretched())
    ratios = [t["ratio"] for t in got["tests"]]
    assert ratios == sorted(ratios, reverse=True)
    assert got["test"] == [got["tests"][0]["track"], got["tests"][0]["score"]]
    with pytest.raises(ValueError):
        frame_attribution(bundle, _stretched(), test=("ring", "SPE"))
