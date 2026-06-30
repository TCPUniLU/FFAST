"""Tab-level control registry (ADR 0021, Phase 5g).

The ``energy_shift`` tab control is the last piece the declarative Basic Errors
tab needed: one checkbox driving the shared ``shifted`` compute-param across every
energy Panel and suffixing their titles. Tested against duck-typed panels (the
control only needs ``hasParam``/``setSharedParam``/``titleLabel``) so no real
Panel/handler stack is required — just an offscreen QApplication for the checkbox.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from UI.controls import TAB_CONTROLS, make_tab_control  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeTitle:
    def __init__(self, text):
        self._text = text

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text


class _FakePanel:
    """Minimal stand-in: declares `shifted` (or not) and records applied params."""

    def __init__(self, title, has_shifted):
        self.titleLabel = _FakeTitle(title)
        self._has_shifted = has_shifted
        self.applied = {}

    def hasParam(self, pname):
        return pname == "shifted" and self._has_shifted

    def setSharedParam(self, pname, value):
        applied = self.hasParam(pname)
        if applied:
            self.applied[pname] = value
        return applied


def test_energy_shift_registered():
    assert "energy_shift" in TAB_CONTROLS


def test_energy_shift_drives_only_energy_panels(qapp):
    energy_a = _FakePanel("Energy MAE distribution", has_shifted=True)
    energy_b = _FakePanel("Energy Scatter", has_shifted=True)
    forces = _FakePanel("Forces MAE distribution", has_shifted=False)

    checkbox = make_tab_control("energy_shift", None, [energy_a, energy_b, forces])

    checkbox.setChecked(True)
    assert energy_a.applied == {"shifted": True}
    assert energy_b.applied == {"shifted": True}
    assert forces.applied == {}  # no `shifted` param → untouched
    assert energy_a.titleLabel.text() == "Energy MAE distribution (shifted)"
    assert forces.titleLabel.text() == "Forces MAE distribution"  # title unchanged

    checkbox.setChecked(False)
    assert energy_a.applied == {"shifted": False}
    assert energy_a.titleLabel.text() == "Energy MAE distribution"  # suffix removed


def test_unknown_tab_control_raises(qapp):
    with pytest.raises(ValueError):
        make_tab_control("nope", None, [])
