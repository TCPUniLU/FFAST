"""The desktop timeline draws a table per structure and element as a line per
element (ADR 0064 amendment 2026-10-24): the element's colour, a line style per
series, gaps for empty cells, and the element picker chooses. The plot widget
is a stand-in that records what it is asked to draw."""
import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore  # noqa: E402

from ffast.chemistry import atomColors  # noqa: E402
from UI.panels import PANEL_KINDS  # noqa: E402

N = np.nan


class _Result:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=float)


class _Dataset:
    def getElements(self):
        return np.array([6, 1, 1])               # columns H, C


class _Panel:
    def __init__(self):
        self.lines = []

    def plot(self, x, y, pen=None, autoColor=None, label=None, autoLabel=None, **kwargs):
        self.lines.append({"x": list(x), "y": [None if np.isnan(v) else v for v in y],
                           "colour": pen.color().name(), "style": pen.style(), "label": label,
                           "connect": kwargs.get("connect")})


def _series(values):
    return {"dataset": _Dataset(), "model": None, "dataEntry": {"m.t": _Result(values)}}


TABLE_A = [[0.1, N], [0.2, 0.5], [N, 0.6]]
TABLE_B = [[0.3, 0.4], [N, N], [0.7, 0.8]]


def _colour(z):
    from PySide6.QtGui import QColor
    return QColor(*(int(c) for c in atomColors[z][:3])).name()


def test_a_line_per_element_and_a_style_per_series():
    panel = _Panel()
    kind = PANEL_KINDS["timeline"]
    for data in (_series(TABLE_A), _series(TABLE_B)):
        kind.draw(panel, data, {"y": "m.t"})      # tables wait for draw_overlay
    assert panel.lines == []
    kind.draw_overlay(panel, [_series(TABLE_A), _series(TABLE_B)], {"y": "m.t"})
    got = [(l["label"], l["colour"], l["style"], l["y"]) for l in panel.lines]
    solid, dash = QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenStyle.DashLine
    assert got == [
        ("H — __NAME__", _colour(1), solid, [0.1, 0.2, None]),
        ("C — __NAME__", _colour(6), solid, [None, 0.5, 0.6]),
        ("H — __NAME__", _colour(1), dash, [0.3, None, 0.7]),
        ("C — __NAME__", _colour(6), dash, [0.4, None, 0.8]),
    ]
    assert all(l["connect"] == "finite" for l in panel.lines)      # gaps stay gaps


def test_the_element_picker_chooses_the_lines():
    class _Picker:
        def getSelectedAtomInfo(self):
            return {"C": {"index": 6}}

    panel = _Panel()
    PANEL_KINDS["timeline"].draw_overlay(panel, [_series(TABLE_A)],
                                         {"y": "m.t", "selector_obj": _Picker()})
    assert [(l["label"], l["y"]) for l in panel.lines] == [("C", [None, 0.5, 0.6])]


def test_a_per_structure_metric_draws_as_before():
    panel = _Panel()
    PANEL_KINDS["timeline"].draw_overlay(panel, [_series([0.1, 0.2])], {"y": "m.t"})
    assert panel.lines == []
