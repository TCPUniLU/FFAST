"""An empty atom box is "nothing entered", not an error.

Regression: the 3D view's atom boxes (Extract, Bonds) apply their text when
focus leaves them, and an empty box was read as Python, which failed and
printed a full "Input cannot be evaluated" traceback every time. Text that
cannot be read still says so, in one line.

Only the code box's own messages are checked, on the "FFAST" logger itself:
the suite runs many tests in one process, and other code logs to "FFAST" and
resets the root logger's handlers. (This test once failed in a full run and
never alone; the cause was not found.)
"""
import contextlib
import logging
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from UI.Templates import interactive  # noqa: E402
from UI.Templates.interactive import CodeTextEdit  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        if record.pathname == interactive.__file__:
            self.records.append(record)


@contextlib.contextmanager
def _box_messages():
    """The code box's messages, caught on its own logger, so a reset of the
    root logger's handlers elsewhere cannot hide them."""
    log = logging.getLogger("FFAST")
    handler, level = _Records(), log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        log.removeHandler(handler)
        log.setLevel(level)


def _box(text):
    box = CodeTextEdit()
    box.setPlainText(text)
    return box


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_an_empty_box_is_read_quietly(qapp, text):
    with _box_messages() as records:
        assert _box(text).getValue() is None
    assert records == []


def test_unreadable_text_warns_in_one_line(qapp):
    with _box_messages() as records:
        assert _box("[1, 2").getValue() is None
    [record] = records
    assert record.levelno == logging.WARNING and record.exc_info is None
    assert "[1, 2" in record.getMessage()


def test_readable_text_is_read(qapp):
    assert _box("[1, 2, 'C']").getValue() == [1, 2, "C"]
