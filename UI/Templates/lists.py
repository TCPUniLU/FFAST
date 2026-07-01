from PySide6 import QtWidgets
from PySide6.QtWidgets import QSizePolicy, QWidget
from config.uiConfig import configStyleSheet
from utils import rgbToHex
import logging
from UI.Templates.base import Widget
from UI.Templates.interactive import WidgetButton

logger = logging.getLogger("FFAST")


class ObjectListItem(Widget):
    def __init__(self, handler, id, color=None, layout="vertical", **kwargs):
        super().__init__(color=color, layout=layout, **kwargs)
        self.handler = handler
        self.id = id


class ObjectList(Widget):
    def __init__(self, handler, widgetType, color=None, **kwargs):
        super().__init__(color=color, layout="vertical", **kwargs)
        self.handler = handler
        self.widgetType = widgetType
        self.widgets = {}
        self.layout.setSpacing(2)
        self.objectsRemoved = set()

    def newObject(self, id, **kwargs):
        if id in self.widgets:
            logger.error(
                f"ID {id} already exists for ObjectList {self} and widgetType {self.widgetType}."
            )
            return

        if id in self.objectsRemoved:
            self.objectsRemoved.remove(id)

        w = self.widgetType(self.handler, id, parent=self, **kwargs)
        self.widgets[id] = w
        self.layout.addWidget(w)

        self.forceUpdateParent()

    def getWidget(self, id):
        return self.widgets.get(id, None)

    def removeObject(self, id):
        if id is self.objectsRemoved:
            return

        w = self.getWidget(id)

        if w is None:
            return

        del self.widgets[id]

        self.layout.removeWidget(w)
        w.prepareDeletion()

        self.objectsRemoved.add(id)

        self.forceUpdateParent()


class FlexibleHList(Widget):

    currentNElementsPerRow = 0
    needsReadjusting = False

    def __init__(self, elementSize=150, **kwargs):
        super().__init__(layout="horizontal", **kwargs)
        self.gridWidget = Widget(parent=self, layout="grid")
        self.gridWidget.layout.setSpacing(5)
        self.spacerWidget = Widget(parent=self)
        self.gridLayout = self.gridWidget.layout

        self.gridWidget.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred
        )

        self.spacerWidget.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Ignored
        )

        self.layout.addWidget(self.gridWidget)
        self.layout.addWidget(self.spacerWidget)
        self.elementSize = elementSize
        self.widgets = []

    def resizeEvent(self, event):
        if self.frozen:
            return

        if self.needsReadjusting:
            self.updateMaximumWidth()
            self.adjustLayout()

        elif self.nElementsPerRow() != self.currentNElementsPerRow:
            self.adjustLayout()

        QWidget.resizeEvent(self, event)

    def adjustLayout(self):
        self.removeWidgets()
        self.readdWidgets()
        self.currentNElementsPerRow = self.nElementsPerRow()
        self.needsReadjusting = False

    def removeWidgets(self, clear=False):
        for w in self.widgets:
            self.gridLayout.removeWidget(w)

        if clear:
            for w in self.widgets:
                w.prepareDeletion()
            self.widgets = []

    def nElementsPerRow(self):
        return self.width() // self.elementSize

    def indexToGridIndices(self, index):
        nelpr = self.nElementsPerRow()
        if nelpr == 0:
            return None, None
        return divmod(index, self.nElementsPerRow())

    def readdWidgets(self):
        for iw in range(len(self.widgets)):
            w = self.widgets[iw]
            i, j = self.indexToGridIndices(iw)
            if i is None:
                continue
            self.gridLayout.addWidget(w, i, j)

    def addWidget(self, w):
        index = len(self.widgets)
        i, j = self.indexToGridIndices(index)

        self.widgets.append(w)
        w.setMaximumWidth(self.elementSize)

        if i is None:
            self.needsReadjusting = True
            return

        self.gridLayout.addWidget(w, i, j)
        self.updateMaximumWidth()

    def updateMaximumWidth(self):
        self.gridWidget.setMaximumWidth(len(self.widgets) * self.elementSize)


class ListCheckButton(WidgetButton):
    colorCircleStyleSheet = """
        background-color: @COLOR;
        border-radius: 10;
    """
    styleSheet = """
        @OBJECT{border-radius:9px;}
        @OBJECT:hover{background-color:@BGColor4}
        @OBJECT[checked=true]{background-color:@BGColor4}
        @OBJECT[checked=true]:hover{background-color:@BGColor5}
    """

    def __init__(self, *args, label="N/A", color=(255, 255, 255), **kwargs):
        super().__init__(
            *args,
            layout="horizontal",
            color="transparent",
            styleSheet=self.styleSheet,
            **kwargs,
        )

        self.name = label
        self.color = color

        self.colorCircle = Widget(parent=self, color="transparent")
        self.colorCircle.setFixedHeight(20)
        self.colorCircle.setFixedWidth(20)

        self.layout.setContentsMargins(2, 2, 2, 2)
        self.layout.setSpacing(5)

        self.label = QtWidgets.QLabel("?", parent=self)
        self.label.setFixedHeight(20)

        self.layout.addWidget(self.colorCircle)
        self.layout.addWidget(self.label)

        self.applyStyle()
        self.setOnClick(self.updateParent)

    def applyStyle(self):
        ss = self.colorCircleStyleSheet.replace(
            "@COLOR", rgbToHex(*self.getColor())
        )
        self.colorCircle.setStyleSheet(configStyleSheet(ss))

        self.label.setText(self.getLabel())

    def getColor(self):
        return self.color

    def getLabel(self):
        return self.name

    def updateParent(self):
        self.parent.update(self)


class FlexibleListSelector(Widget):
    def __init__(
        self,
        *args,
        elementSize=150,
        label=None,
        singleSelection=False,
        **kwargs,
    ):
        super().__init__(*args, layout="horizontal", **kwargs)

        self.singleSelection = singleSelection

        if label is not None:
            self.label = QtWidgets.QLabel(parent=self)
            self.label.setText(label)
            self.layout.addWidget(self.label)
            self.label.setFixedWidth(120)
            self.label.setObjectName("titleLabel")

        self.list = FlexibleHList(elementSize=elementSize, parent=self)
        self.layout.addWidget(self.list)

    def removeWidgets(self, **kwargs):
        return self.list.removeWidgets(**kwargs)

    def addWidget(self, w, *args, **kwargs):
        w.parent = self
        return self.list.addWidget(w, *args, **kwargs)

    def getWidgets(self):
        return self.list.widgets

    def getSelectedWidgets(self):
        return [w for w in self.getWidgets() if w.checked]

    def setOnUpdate(self, func):
        self.updateFunc = func

    def update(self, widget):

        if self.singleSelection and len(self.getSelectedWidgets()) > 1:
            for w in self.getWidgets():
                if w is widget:
                    continue
                if w.checked:
                    w.setChecked(False, quiet=True)

        self.updateFunc()

    def removeWidgets(self, *args, **kwargs):
        return self.list.removeWidgets(*args, **kwargs)
