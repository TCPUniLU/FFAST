from PySide6 import QtCore, QtGui, QtWidgets
from config.uiConfig import configStyleSheet
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QTabWidget, QSizePolicy
import logging

CURRENT_WIDGET_ID = 0
logger = logging.getLogger("FFAST")


class Widget(QWidget):

    frozen = False
    deleted = False

    def __init__(
        self,
        layout=None,
        color=None,
        parent=None,
        frozen=False,
        styleSheet="",
        widgetName=None,
    ):
        super().__init__(parent=parent)
        self.frozen = frozen
        if parent is None:
            logger.warn(f"Parent not being set for widget {self}")

        if widgetName is None:
            self.applyDefaultName()
        else:
            self.objectName = widgetName
            self.setObjectName(widgetName)

        self.applyDefaultLayout(layout=layout)
        self.applyDefaultStyleSheet(color=color, styleSheet=styleSheet)

    def applyDefaultName(self):
        global CURRENT_WIDGET_ID
        self.id = CURRENT_WIDGET_ID
        self.objectName = f"WIDGET_{self.id}"
        CURRENT_WIDGET_ID += 1
        self.setObjectName(self.objectName)

    def applyDefaultLayout(self, layout=None):
        if layout is None:
            return

        if layout == "vertical":
            self.layout = QtWidgets.QVBoxLayout()
        elif layout == "horizontal":
            self.layout = QtWidgets.QHBoxLayout()
        elif layout == "grid":
            self.layout = QtWidgets.QGridLayout()
        elif layout is not None:
            logger.error(
                f"Layout given to {self} but type {layout} not recognised"
            )

        self.setLayout(self.layout)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

    def applyDefaultStyleSheet(self, color=None, styleSheet=""):
        ss = styleSheet
        if color is not None:
            self.setAttribute(Qt.WA_StyledBackground, True)
            ss = ss + f"@OBJECT{{background-color:{color};}}"

        ss = ss.replace("@OBJECT", f"QWidget#{self.objectName}")
        ss = configStyleSheet(ss)

        self.setStyleSheet(ss)

    def freeze(self):
        self.frozen = True

    def unfreeze(self):
        self.frozen = False

    def prepareDeletion(self):
        self.deleted = True
        self.deleteLater()
        if hasattr(self, "isEventChild") and self.isEventChild:
            self.deleteEvents()

    def forceUpdateParent(self, depth=100, anyWidget=False):
        # Lazy import breaks the circular dep: layout.py imports base.py
        from UI.Templates.layout import (
            CollapsibleWidget,
            HorizontalExpandingScrollArea,
            ExpandingScrollArea,
        )
        d = 0
        w = self
        while w.parentWidget() is not None:
            w = w.parentWidget()
            if isinstance(w, CollapsibleWidget):
                w.forceUpdateLayout()
                w.forceUpdateSize()
                d += 1
            elif isinstance(w, HorizontalExpandingScrollArea) or isinstance(
                w, ExpandingScrollArea
            ):
                w.forceUpdateSize()
                d += 1
            elif anyWidget:
                d += 1
                Widget.forceUpdateSize(w)

            if d >= depth:
                return

    def forceUpdateSize(self):
        self.resizeEvent(QtGui.QResizeEvent(self.size(), QtCore.QSize()))


class TabWidget(QTabWidget):
    def __init__(self, parent=None, color=None, styleSheet=""):
        super().__init__(parent=parent)

        Widget.applyDefaultName(self)
        Widget.applyDefaultStyleSheet(self, color=color, styleSheet=styleSheet)


class PushButton(QtWidgets.QPushButton):
    def __init__(self, text, parent=None, color=None, styleSheet=""):
        super().__init__(text, parent=parent)

        Widget.applyDefaultName(self)
        Widget.applyDefaultStyleSheet(self, color=color, styleSheet=styleSheet)


class Label(QtWidgets.QLabel):
    def __init__(self, text, parent=None, color=None, styleSheet=""):
        super().__init__(text, parent=parent)

        Widget.applyDefaultName(self)
        Widget.applyDefaultStyleSheet(self, color=color, styleSheet=styleSheet)


class ProgressBar(QtWidgets.QProgressBar):
    def __init__(self, parent=None, color=None, styleSheet=""):
        super().__init__(parent=parent)

        Widget.applyDefaultName(self)
        Widget.applyDefaultStyleSheet(self, color=color, styleSheet=styleSheet)


class ComboBox(QtWidgets.QComboBox):
    def __init__(self, *args, color=None, styleSheet="", **kwargs):
        super().__init__(*args, **kwargs)

        Widget.applyDefaultName(self)
        Widget.applyDefaultStyleSheet(self, color=color, styleSheet=styleSheet)


class LineEdit(QtWidgets.QLineEdit):

    callbackFunc = None

    def __init__(self, *args, color=None, styleSheet="", **kwargs):
        super().__init__(*args, **kwargs)

        Widget.applyDefaultName(self)
        Widget.applyDefaultStyleSheet(self, color=color, styleSheet=styleSheet)

        self.editingFinished.connect(self.callback)

    def setOnEdit(self, func):
        self.callbackFunc = func

    def callback(self):
        self.clearFocus()
        if self.callbackFunc is not None:
            self.callbackFunc()
