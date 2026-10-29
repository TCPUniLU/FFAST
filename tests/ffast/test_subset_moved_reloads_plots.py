"""2D plots load a moved subset's data by themselves.

Regression: a zoom moved the SUB and dropped its old results, but plots load
data only when the dataset selection changes, so they stayed empty until Load
was clicked or the dataset reselected. The selector now does that reselect
itself once a selected subset has been still for half a second, so a zoom
being dragged loads once, when it stops.
"""
import os
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from UI.ContentTab import DatasetModelSelector  # noqa: E402


class _Timer:
    def __init__(self):
        self.starts = 0

    def start(self):
        self.starts += 1


def _selector(selected):
    return types.SimpleNamespace(
        getSelectedKeys=lambda: ([], list(selected)), _subsetMoved=_Timer())


def test_a_selected_subset_that_moves_reloads_once_it_is_still():
    selector = _selector(["sub"])
    DatasetModelSelector.onSubsetMoved(selector, "sub")
    DatasetModelSelector.onSubsetMoved(selector, "sub")   # still zooming: wait again
    assert selector._subsetMoved.starts == 2


def test_a_subset_this_tab_does_not_show_is_left_alone():
    selector = _selector(["parent"])
    DatasetModelSelector.onSubsetMoved(selector, "sub")
    assert selector._subsetMoved.starts == 0


def test_the_wait_is_half_a_second():
    assert DatasetModelSelector.SUBSET_SETTLE_MS == 500
