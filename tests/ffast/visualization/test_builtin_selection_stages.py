import numpy as np

from ffast.visualization.stages.builtin.selection_stages import atom_filter


# --- selection_mask ---

def test_atom_filter_keeps_listed():
    pos = np.zeros((3, 3))
    mask = atom_filter(pos, indices=[0, 2])
    assert mask.tolist() == [True, False, True]


def test_atom_filter_empty_keeps_all():
    pos = np.zeros((3, 3))
    assert atom_filter(pos, indices=[]).tolist() == [True, True, True]


def test_atom_filter_none_keeps_all():
    pos = np.zeros((2, 3))
    assert atom_filter(pos).tolist() == [True, True]


def test_atom_filter_invert_hides_listed():
    pos = np.zeros((3, 3))
    mask = atom_filter(pos, indices=[1], invert=True)
    assert mask.tolist() == [True, False, True]


def test_atom_filter_drops_out_of_range():
    pos = np.zeros((2, 3))
    mask = atom_filter(pos, indices=[0, 9])
    assert mask.tolist() == [True, False]
