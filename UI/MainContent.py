from ffast.core.events import EventChildClass
from PySide6 import QtCore, QtGui, QtWidgets
from config.uiConfig import configStyleSheet
from UI.Templates import TabWidget, Widget


class MainContentTabWidget(TabWidget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Force size recalculation when switching tabs so PyQtGraph viewports
        # paint on first show (without this they stay blank until interaction).
        self.currentChanged.connect(self._onTabChanged)

    def _onTabChanged(self, index):
        widget = self.widget(index)
        if widget is None:
            return
        if hasattr(widget, "forceUpdateSize"):
            QtCore.QTimer.singleShot(0, widget.forceUpdateSize)
        else:
            QtCore.QTimer.singleShot(0, widget.update)
