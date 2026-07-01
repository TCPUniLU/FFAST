from PySide6 import QtCore, QtWidgets
import logging
from UI.Templates.base import Widget

logger = logging.getLogger("FFAST")


class BasicLabelWidget(Widget):
    def __init__(self, spacing=0, **kwargs):
        super().__init__(layout="vertical", **kwargs)

        self.layout.setContentsMargins(spacing, spacing, spacing, spacing)

        self.label = QtWidgets.QLabel("", parent=self)
        self.layout.addWidget(self.label)

    def setText(self, s):
        self.label.setText(s)


class InfoWidget(Widget):
    nRows = 0
    styleSheet = '''
        QLabel#LeftLabel{
            font-weight: bold;
        }
        QLabel{
            qproperty-alignment: AlignLeft;
        }
        """
    '''

    def __init__(self, **kwargs):
        super().__init__(**kwargs, styleSheet=self.styleSheet)
        self.layout = QtWidgets.QGridLayout()
        self.setLayout(self.layout)

    def setInfo(self, *args):
        # ADD LABELS
        nRows = len(args)
        for i in range(self.nRows, nRows):
            labelLeft = QtWidgets.QLabel("", parent=self)
            labelLeft.setObjectName("LeftLabel")
            self.layout.addWidget(labelLeft, i, 0)

            labelRight = QtWidgets.QLabel("", parent=self)
            labelRight.setObjectName("RightLabel")
            self.layout.addWidget(labelRight, i, 1)

        # REMOVE LABELS
        for i in range(nRows, self.nRows):
            labelLeft = self.layout.itemAtPosition(i, 0)
            self.layout.removeWidget(labelLeft.widget())

            labelRight = self.layout.itemAtPosition(i, 1)
            self.layout.removeWidget(labelRight.widget())

        for i in range(nRows):
            sLeft, sRight = args[i]

            labelLeft = self.layout.itemAtPosition(i, 0).widget()
            labelRight = self.layout.itemAtPosition(i, 1).widget()

            labelLeft.setText(sLeft)
            labelRight.setText(sRight)

        self.nRows = nRows


class TableView(Widget):
    """Simple view-only table.

    Note: internally everything is column major! Accessing externally is row.
    """

    tableSize = (0, 0)
    headerLeft = True
    headerTop = True
    spacing = 10
    nHeaderChars = 20

    borderQSS = "1px inset @BGColor5"

    styleSheet = """
    """

    headerLabelStyleSheet = """
        QLabel{
            font-weight: bold;
        }

        QWidget#tableHeaderLabelLeft{
            border-top: @borderQSS;
        }

        QWidget#tableHeaderLabelTop{
            border-left: @borderQSS;
        }

    """

    labelStyleSheet = """
        @OBJECT{
            border-top: @borderQSS;
            border-left: @borderQSS;
        }
    """

    def __init__(
        self, headerLeft=True, headerTop=True, spacing=None, **kwargs
    ):
        kwargs.update(layout="horizontal")
        super().__init__(styleSheet=self.styleSheet, **kwargs)

        if spacing is not None:
            self.spacing = spacing
        else:
            spacing = self.spacing

        self.headerLabelStyleSheet = self.headerLabelStyleSheet.replace(
            "@borderQSS", self.borderQSS
        )
        self.styleSheet = self.styleSheet.replace("@borderQSS", self.borderQSS)
        self.labelStyleSheet = self.labelStyleSheet.replace(
            "@borderQSS", self.borderQSS
        )

        self.headerLeft = headerLeft
        self.headerTop = headerTop

        self.labels = []
        self.headerTopLabels = []
        self.headerLeftLabels = []
        self.cornerLabel = BasicLabelWidget(
            parent=self,
            widgetName="tableHeaderLabelCorner",
            styleSheet=self.headerLabelStyleSheet,
            spacing=self.spacing,
            color="transparent",
        )
        self.columnWidgets = []
        self.headerLeftWidget = Widget(parent=self, layout="vertical")
        self.headerLeftWidget.layout.addWidget(self.cornerLabel)
        self.layout.addWidget(self.headerLeftWidget)

    def setSize(self, nRows, nCols):
        self.tableSize = (nRows, nCols)
        self.updateHeaders()

        # create labels if necessary
        for col in range(0, nCols):
            for row in range(len(self.labels[col]), nRows):
                label = BasicLabelWidget(
                    parent=self.columnWidgets[col],
                    widgetName="tableHeaderLabel",
                    styleSheet=self.labelStyleSheet,
                    spacing=self.spacing,
                    color="transparent",
                )
                # need transparent color for borders to show, idk why and honestly I dont care to find out

                self.labels[col].append(label)
                self.columnWidgets[col].layout.addWidget(label)

        # hide/show those needed
        for col in range(len(self.labels)):
            for row in range(len(self.labels[col])):

                if (row >= nRows) or (col >= nCols):
                    self.hideLabel(self.labels[col][row])
                else:
                    self.showLabel(self.labels[col][row])

        self.forceUpdateParent()  # doesnt do anything, idk whyyyy

    def showLabel(self, label):
        label.show()

    def hideLabel(self, label):
        label.hide()

    def updateHeaders(self):
        nRows, nCols = self.tableSize

        # CREATE LEFT HEADERS
        for row in range(len(self.headerLeftLabels), nRows):
            label = BasicLabelWidget(
                parent=self.headerLeftWidget,
                widgetName="tableHeaderLabelLeft",
                styleSheet=self.headerLabelStyleSheet,
                spacing=self.spacing,
                color="transparent",
            )
            self.headerLeftLabels.append(label)
            self.headerLeftWidget.layout.addWidget(label)

        # CREATE TOP HEADERS
        for col in range(len(self.columnWidgets), nCols):
            self.columnWidgets.append(Widget(parent=self, layout="vertical"))
            self.layout.addWidget(self.columnWidgets[col])
            self.labels.append([])

            label = BasicLabelWidget(
                parent=self.columnWidgets[col],
                widgetName="tableHeaderLabelTop",
                styleSheet=self.headerLabelStyleSheet,
                spacing=self.spacing,
                color="transparent",
            )
            self.headerTopLabels.append(label)
            self.columnWidgets[col].layout.addWidget(label)

        # CORNER LABEL
        if not self.headerTop:
            self.cornerLabel.hide()
        else:
            self.cornerLabel.show()

        # HIDE/SHOW LEFT HEADERS
        for row in range(len(self.headerLeftLabels)):
            if self.headerLeft and row < nRows:
                self.showLabel(self.headerLeftLabels[row])
            else:
                self.hideLabel(self.headerLeftLabels[row])

        # HIDE/SHOW TOP HEADERS
        for col in range(len(self.headerTopLabels)):
            if self.headerTop and col < nCols:
                self.showLabel(self.headerTopLabels[col])
            else:
                self.hideLabel(self.headerTopLabels[col])

    def setValue(self, row, col, s):
        nRows, nCols = self.tableSize

        if (row > nRows) or (col > nCols):
            logger.error(
                f"Tried to set table value at ({row},{col}) but size is {self.tableSize}"
            )

        label = self.labels[col][row]
        label.setText(s)

    def setLeftHeader(self, row, s):
        self.headerLeftLabels[row].setText(s[: self.nHeaderChars])

    def setTopHeader(self, col, s):
        self.headerTopLabels[col].setText(s[: self.nHeaderChars])
