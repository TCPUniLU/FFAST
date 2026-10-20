"""An empty atom box is "nothing entered", not an error.

Regression: the 3D view's atom boxes (Extract, Bonds) apply their text when
focus leaves them, and an empty box was read as Python, which failed and
printed a full "Input cannot be evaluated" traceback every time. Text that
cannot be read still says so, in one line.
"""
import logging
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from UI.Templates.interactive import CodeTextEdit  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _box(text):
    box = CodeTextEdit()
    box.setPlainText(text)
    return box


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_an_empty_box_is_read_quietly(qapp, caplog, text):
    with caplog.at_level(logging.DEBUG, logger="FFAST"):
        assert _box(text).getValue() is None
    assert caplog.records == []


def test_unreadable_text_warns_in_one_line(qapp, caplog):
    with caplog.at_level(logging.DEBUG, logger="FFAST"):
        assert _box("[1, 2").getValue() is None
    [record] = caplog.records
    assert record.levelno == logging.WARNING and record.exc_info is None
    assert "[1, 2" in record.getMessage()


def test_readable_text_is_read(qapp):
    assert _box("[1, 2, 'C']").getValue() == [1, 2, "C"]
