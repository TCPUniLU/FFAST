"""Hovering over sidebar text cut by the panel's width shows all of it.

The sidebar is narrow, so a long name ("Energy,dataset,prediction.xyz") or
info value ("3, 5, 7-9, ... (50 frames)") was cut with no way to read it.
Text that fits shows no hover text.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from UI.Templates.display import cutText  # noqa: E402

LONG = "Energy MAE timeline,dataset,prediction.xyz"


@pytest.fixture(scope="module")
def qapp():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _sized(widget, width):
    widget.setFixedWidth(width)
    widget.show()
    return widget


def test_cut_text_is_given_in_full(qapp):
    assert cutText(_sized(QtWidgets.QLabel(LONG), 40)) == LONG
    assert cutText(_sized(QtWidgets.QLineEdit(LONG), 40)) == LONG


def test_text_that_fits_needs_no_hover(qapp):
    assert cutText(_sized(QtWidgets.QLabel("100"), 400)) is None
    assert cutText(_sized(QtWidgets.QLabel(""), 10)) is None
