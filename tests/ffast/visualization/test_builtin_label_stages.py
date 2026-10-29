import numpy as np

from ffast.visualization.stages.builtin.label_stages import atom_labels


def test_atom_labels_index_mode():
    pos = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    anchors, texts = atom_labels(pos, mode="index")
    assert np.allclose(anchors, pos)
    assert texts == ["0", "1", "2"]


def test_atom_labels_default_is_index():
    pos = np.zeros((2, 3))
    _, texts = atom_labels(pos)
    assert texts == ["0", "1"]


def test_atom_labels_element_mode():
    pos = np.zeros((3, 3))
    _, texts = atom_labels(pos, elements=np.array([1, 6, 8]), mode="element")
    assert texts == ["H", "C", "O"]


def test_atom_labels_element_mode_without_elements_falls_back_to_index():
    pos = np.zeros((2, 3))
    _, texts = atom_labels(pos, elements=None, mode="element")
    assert texts == ["0", "1"]


def test_atom_labels_anchors_are_input_positions():
    pos = np.array([[3.0, 1.0, 4.0], [1.0, 5.0, 9.0]])
    anchors, _ = atom_labels(pos, mode="index")
    assert np.allclose(anchors, pos)
