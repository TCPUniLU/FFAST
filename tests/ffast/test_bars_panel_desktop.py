"""The desktop's Bars per element panel kind (ADR 0064 amendment): a bar per
series in each element's group, no bar for no value, and the label says so.
The plot widget is a stand-in that records what it is asked to draw."""
import numpy as np
import pytest

pytest.importorskip("PySide6")

from UI.panels import PANEL_KINDS  # noqa: E402


class _Result:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=float)


class _Dataset:
    def getElements(self):
        return np.array([8, 1, 6, 1])           # H, C, O by atomic number


class _Panel:
    def __init__(self):
        self.bars, self.ticks = [], None

    def plot(self, x, y, bars=False, width=None, autoColor=None, autoLabel=None):
        self.bars.append((list(np.round(x, 3)), list(y), width, bars))

    def setXTicks(self, x, labels):
        self.ticks = (list(x), list(labels))


def _series(values):
    return {"dataset": _Dataset(), "model": None, "dataEntry": {"m.e": _Result(values)}}


def test_bars_group_the_series_by_element_and_label_no_value():
    panel = _Panel()
    PANEL_KINDS["bars"].draw_overlay(
        panel, [_series([0.1, np.nan, 0.3]), _series([0.2, np.nan, np.nan])], {"value": "m.e"})
    (x0, y0, w0, b0), (x1, y1, w1, b1) = panel.bars
    assert b0 and b1 and w0 == w1 == 0.4
    assert (x0, y0) == ([-0.2, 1.8], [0.1, 0.3])          # carbon has no bar
    assert (x1, y1) == ([0.2], [0.2])
    assert panel.ticks == ([0, 1, 2], ["H", "C\n(no value)", "O"])


def test_the_element_picker_chooses_the_groups():
    class _Picker:
        def getSelectedAtomInfo(self):
            return {"O": {"index": 8}}

    panel = _Panel()
    PANEL_KINDS["bars"].draw_overlay(panel, [_series([0.1, 0.2, 0.3])],
                                     {"value": "m.e", "selector_obj": _Picker()})
    assert panel.bars == [([0.0], [0.3], 0.8, True)]
    assert panel.ticks == ([0], ["O"])


def test_a_desktop_panel_collects_each_warning_once():
    from UI.panels import result_warnings

    class _R:
        def __init__(self, warnings):
            self.warnings = warnings

    data = [{"dataEntry": {"a": _R(["x", "y"]), "b": _R([])}}, {"dataEntry": {"a": _R(["x"])}}]
    assert result_warnings(data) == ["x", "y"]
