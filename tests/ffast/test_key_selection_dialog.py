"""The desktop key dialog names its calculator option <Use Energy> / <Use Force>,
and picking it still asks the loader for the calculator's value ("")."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from UI.KeySelectionDialog import KeySelectionDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def test_the_first_options_are_use_energy_and_use_force(qapp):
    dialog = KeySelectionDialog(["REF_energy"], ["REF_forces"])
    assert dialog.energy_ref_combo.itemText(0) == "<Use Energy>"
    assert dialog.force_ref_combo.itemText(0) == "<Use Force>"


def test_picking_them_selects_the_calculator(qapp):
    dialog = KeySelectionDialog(["REF_energy"], ["REF_forces"])
    dialog.energy_ref_combo.setCurrentIndex(0)
    dialog.force_ref_combo.setCurrentIndex(0)
    selection = dialog.getSelection()
    assert selection["energy_ref"] == ""
    assert selection["force_ref"] == ""


def test_prediction_rows_use_the_same_labels(qapp):
    dialog = KeySelectionDialog(["MACE_energy"], ["MACE_forces"])
    dialog._addPredictionRow()              # what "Add Prediction" does
    energy_combo, force_combo, *_ = dialog.pred_rows[0]
    assert energy_combo.itemText(0) == "<Use Energy>"
    energy_combo.setCurrentIndex(0)
    force_combo.setCurrentIndex(0)
    assert dialog.getSelection()["predictions"][0][:2] == ("", "")
