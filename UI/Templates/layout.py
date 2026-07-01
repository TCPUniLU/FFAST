from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSizePolicy
from config.uiConfig import getIcon
import logging
from UI.Templates.base import Widget

logger = logging.getLogger("FFAST")


class ExpandingScrollArea(QtWidgets.QScrollArea):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.setWidgetResizable(True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.setMaximumHeight(0)
        self.setMinimumHeight(0)

    contentWidget = None

    def setContent(self, widget):
        self.setWidget(widget)
        widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.contentWidget = widget
        self.setMaximumHeight(16777215)

    def sizeHint(self):
        return self.contentWidget.sizeHint()

    def resizeEvent(self, *args):
        QtWidgets.QScrollArea.resizeEvent(self, *args)

    def showEvent(self, event):
        super().showEvent(event)
        # Defer size update so the layout pass has completed before we
        # ask PyQtGraph viewports to repaint (fixes blank plots on first
        # tab switch).
        QtCore.QTimer.singleShot(0, self.forceUpdateSize)

    def forceUpdateSize(self):
        self.resizeEvent(QtGui.QResizeEvent(self.size(), QtCore.QSize()))
        if self.contentWidget is not None:
            self.contentWidget.update()


class HorizontalExpandingScrollArea(QtWidgets.QScrollArea):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.setWidgetResizable(True)
        # horizontal then vertical
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.setMaximumWidth(0)
        self.setMinimumWidth(0)

    contentWidget = None

    def setContent(self, widget):
        self.setWidget(widget)
        widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.contentWidget = widget
        self.setMaximumWidth(16777215)

    def sizeHint(self):
        w = 0
        if self.contentWidget is not None:
            w = self.contentWidget.width()
        return QtCore.QSize(w, super().sizeHint().height() + 10)

    def resizeEvent(self, *args):
        self.setMinimumHeight(self.contentWidget.sizeHint().height() + 10)
        self.setMaximumHeight(self.contentWidget.sizeHint().height() + 10)
        QtWidgets.QScrollArea.resizeEvent(self, *args)

    def forceUpdateSize(self):
        self.contentWidget.forceUpdateSize()
        self.resizeEvent(QtGui.QResizeEvent(self.size(), QtCore.QSize()))


class HorizontalContainerScrollArea(Widget):
    def __init__(self, **kwargs):
        super().__init__(layout="vertical", **kwargs)
        self.scrollArea = HorizontalExpandingScrollArea()
        self.content = Widget(parent=self, layout="horizontal")
        self.scrollArea.setContent(self.content)
        self.layout.addWidget(self.scrollArea)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def addContent(self, widget):
        self.content.layout.addWidget(widget)

    def addStretch(self):
        self.content.layout.addStretch()

    def resizeEvent(self, *args):
        Widget.resizeEvent(self, *args)
        self.scrollArea.setMaximumWidth(self.width())


class CollapseButton(QtWidgets.QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setObjectName("collapseButton")
        self.setIcon(QtGui.QIcon(getIcon("expanded")))
        self.clicked.connect(self.onClick)

    collapsingWidget = None
    callbackFunc = None

    def setCollapsingWidget(self, widget):
        self.collapsingWidget = widget
        self.updateIcon()
        self.setCollapsed()

    def updateIcon(self):
        if self.collapsingWidget.isVisible():
            self.setIcon(QtGui.QIcon(getIcon("expanded")))
        else:
            self.setIcon(QtGui.QIcon(getIcon("collapsed")))

    def onClick(self):
        if self.isExpanded():
            self.setCollapsed()
        else:
            self.setExpanded()

    def setCollapsed(self):
        self.collapsingWidget.hide()
        self.updateIcon()
        self.updateSize()
        if self.callbackFunc is not None:
            self.callbackFunc()

    def setExpanded(self):
        self.collapsingWidget.show()
        self.updateIcon()
        self.updateSize()
        if self.callbackFunc is not None:
            self.callbackFunc()

    def isExpanded(self):
        return self.collapsingWidget.isVisible()

    def updateSize(self):
        Widget.forceUpdateParent(self)

    def setCallback(self, func):
        self.callbackFunc = func


class CollapsibleWidget(Widget):
    def __init__(
        self, handler, name="N/A", titleHeight=25, widget=None, **kwargs
    ):
        super().__init__(layout="vertical", **kwargs)
        self.handler = handler
        self.titleHeight = titleHeight

        self.titleButton = CollapseButton(name)
        self.titleButton.setFixedHeight(self.titleHeight)

        self.layout.addWidget(self.titleButton)

        if widget is None:
            self.scrollWidget = Widget(parent=self, layout="vertical")
            self.scrollLayout = self.scrollWidget.layout
        else:
            self.scrollWidget = widget

        self.scrollArea = ExpandingScrollArea()
        self.scrollArea.setContent(self.scrollWidget)

        self.scrollWidget.setMaximumWidth(self.titleButton.width())

        self.layout.addWidget(self.scrollArea)

        self.titleButton.setCollapsingWidget(self.scrollArea)

    def sizeHint(self):
        return QtCore.QSize(
            super().sizeHint().width(), super().sizeHint().height()
        )

    def isExpanded(self):
        return self.titleButton.isExpanded()

    def setCollapsed(self):
        self.titleButton.setCollapsed()

    def setExpanded(self):
        self.titleButton.setExpanded()

    def forceUpdateLayout(self):
        QtCore.QTimer.singleShot(0, self._forceUpdateLayout)

    def _forceUpdateLayout(self):
        self.scrollArea.setMaximumHeight(10)
        self.scrollArea.setMaximumHeight(100000)
        self.scrollWidget.setMaximumHeight(10)
        self.scrollWidget.setMaximumHeight(100000)

        self.scrollArea.adjustSize()
        self.scrollWidget.adjustSize()

    def setCallback(self, func):
        self.titleButton.setCallback(func)

    def resizeEvent(self, event):
        Widget.resizeEvent(self, event)
        self.scrollWidget.setMaximumWidth(self.titleButton.width())


class ContentBar(Widget):
    def __init__(self, handler, **kwargs):
        super().__init__(color="@BGColor1", **kwargs)
        self.handler = handler

        self.layout = QtWidgets.QVBoxLayout()
        self.layout.setSpacing(0)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.layout)

        self.setFixedWidth(300)
        self.layout.addStretch()

        self.widgets = {}

    def addContent(self, name, widget=None, callback=None):
        content = CollapsibleWidget(
            self.handler, name=name, widget=widget, parent=self
        )
        self.layout.insertWidget(self.layout.count() - 1, content)
        self.widgets[name] = content
        return content

    def setCollapsed(self, name):
        self.widgets[name].setCollapsed()

    def setExpanded(self, name):
        self.widgets[name].setExpanded()

    def setContentVisibility(self, name, vis):
        widget = self.widgets.get(name)
        if widget is None:
            return

        if vis:
            widget.show()
        else:
            widget.hide()
