from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from config.uiConfig import getIcon
import logging
import ast
import pprint
from UI.Templates.base import Widget, LineEdit

logger = logging.getLogger("FFAST")


class WidgetButton(Widget):

    checked = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.setProperty("checked", False)

    def setOnClick(self, func):
        self.updateFunc = func

    def mousePressEvent(self, event):
        self.setChecked(not self.checked)

    def setChecked(self, checked, quiet=False):
        self.checked = checked

        if not quiet:
            self.updateFunc()

        # https://wiki.qt.io/Dynamic_Properties_and_Stylesheets
        self.setProperty("checked", checked)
        self.style().unpolish(self)
        self.style().polish(self)

    def updateFunc(*args, **kwargs):
        pass


class Slider(Widget):

    callbackFunc = None
    quiet = False
    smoothing = 1

    def __init__(
        self,
        *args,
        hasEditBox=True,
        label=None,
        nMin=0,
        nMax=99999,
        interval=1,
        **kwargs,
    ):
        super().__init__(*args, **kwargs, layout="horizontal")

        self.hasEditBox = hasEditBox
        self.interval = 1

        self.slider = QtWidgets.QSlider(Qt.Horizontal, parent=self)
        self.layout.addWidget(self.slider)
        self.slider.valueChanged.connect(self.onUpdateSlider)

        if hasEditBox:
            self.lineEdit = LineEdit(parent=self)
            self.lineEdit.setFixedWidth(50)
            self.layout.addWidget(self.lineEdit)
            self.lineEdit.setOnEdit(self.onUpdateLineEdit)

        if label is not None:
            self.label = QtWidgets.QLabel(label, parent=self)
            self.layout.insertWidget(0, self.label)
            self.layout.setSpacing(8)

        self.setMinMax(nMin, nMax, interval)

    def setMinMax(self, nMin, nMax, interval=1):

        self.quiet = True

        self.nMin = nMin
        self.nMax = nMax

        self.slider.setMinimum(nMin)
        self.slider.setMaximum(nMax)
        self.slider.setTickInterval(interval)

        val = QtGui.QIntValidator(nMin, nMax)
        self.lineEdit.setValidator(val)

        self.quiet = False

    def onUpdateSlider(self, value):
        self.lineEdit.setText(str(value))
        self.callback()

    def onUpdateLineEdit(self):
        value = self.lineEdit.text()
        self.slider.setValue(int(value))
        self.callback()

    def setValue(self, value, quiet=False):
        self.quiet = quiet
        self.lineEdit.setText(str(value))
        self.slider.setValue(int(value))
        self.quiet = False

    def getValue(self):
        return self.slider.value()

    def setCallbackFunc(self, func):
        self.callbackFunc = func

    def callback(self):
        if not self.quiet and (self.callbackFunc is not None):
            self.callbackFunc(self.getValue())


class CodeLineEdit(LineEdit):

    returnCallback = None

    def __init__(self, *args, validationFunc=None, **kwargs):
        kwargs.update(color="black")
        super().__init__(*args, **kwargs)

        self.validationFunc = validationFunc
        self.setOnEdit(self.onLineEdit)

    def validate(self):
        valid, validT = True, self.getValue()
        if validT is None:
            return False, None

        if self.validationFunc is not None:
            try:
                valid, validT = self.validationFunc(validT)
            except Exception as e:
                logger.exception(
                    f"Tried validating CodeLineEdit input, but got error {e}"
                )
                return False, None

        return valid, validT

    def setReturnCallback(self, func):
        self.returnCallback = func

    def onLineEdit(self):
        validated, cleanedT = self.validate()
        if not validated:
            return
        self.clearFocus()
        self.setCode(cleanedT)
        if self.returnCallback is not None:
            self.returnCallback()

    def getValue(self):
        t = self.text()
        t = t.replace("; ", "\n").replace(";", "\n")
        code = None
        try:
            code = ast.literal_eval(t)
        except (TypeError, MemoryError, SyntaxError, ValueError):
            logger.exception("Input cannot be evaluated")

        return code

    def setCode(self, value):
        text = pprint.pformat(value, width=30)
        self.setText(text.replace("\n", "; "))


class CodeTextEdit(QtWidgets.QTextEdit):

    returnCallback = None

    def __init__(self, *args, validationFunc=None, **kwargs):
        super().__init__(*args, **kwargs)

        Widget.applyDefaultName(self)
        Widget.applyDefaultStyleSheet(self, color="black")

        self.validationFunc = validationFunc

        self.installEventFilter(self)

    def setReturnCallback(self, func):
        self.returnCallback = func

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return:
            if event.modifiers() == Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                validated, cleanedT = self.validate()
                if not validated:
                    return
                self.clearFocus()
                self.setCode(cleanedT)
                if self.returnCallback is not None:
                    self.returnCallback()
        else:
            super().keyPressEvent(event)

    def validate(self):
        valid, validT = True, self.getValue()
        if validT is None:
            return False, None

        if self.validationFunc is not None:
            try:
                valid, validT = self.validationFunc(validT)
            except Exception as e:
                logger.exception(
                    f"Tried validating CodeTextEdit input, but got error {e}"
                )
                return False, None

        return valid, validT

    def getValue(self):
        t = self.toPlainText()
        code = None
        try:
            code = ast.literal_eval(t)
        except (TypeError, MemoryError, SyntaxError, ValueError):
            logger.exception("Input cannot be evaluated")

        return code

    def setCode(self, value):
        text = pprint.pformat(value, width=30)
        self.setText(text)


class ToolCheckButton(QtWidgets.QToolButton):
    checked = False

    def __init__(self, handler, func, icon="default", **kwargs):
        super().__init__(**kwargs)
        self.handler = handler
        self.func = func
        icon = getIcon(icon)
        self.setIcon(QtGui.QIcon(icon))

        self.clicked.connect(self.onClicked)
        self.setCheckable(True)

    def onClicked(self):
        self.setChecked(not self.isChecked())

    def setChecked(self, checked):
        if self.checked == checked:
            return

        self.checked = checked
        self.onStateChanged()

        self.setChecked(checked)

    def isChecked(self):
        return self.checked

    def onStateChanged(self):
        self.func()


class ToolButton(QtWidgets.QToolButton):
    padding = 4

    def __init__(
        self, func, icon="default", padding=None, width=25, height=25, **kwargs
    ):
        super().__init__(**kwargs)
        if padding is not None:
            self.padding = padding
        self.clicked.connect(func)
        self.setIconByName(icon)
        self.setButtonSize(width, height)

    def setIconByName(self, name):
        icon = getIcon(name)
        self.setIcon(QtGui.QIcon(icon))

    def setButtonSize(self, w, h):
        self.setFixedSize(w, h)
        self.setIconSize(
            QtCore.QSize(w - self.padding * 2, h - self.padding * 2)
        )


class InfoToolButton(ToolButton):
    """Info button: shows tooltip immediately on hover and on click."""

    def __init__(self, **kwargs):
        super().__init__(self._showTooltip, icon="info", **kwargs)

    def _showTooltip(self, _checked=False):
        tip = self.toolTip()
        if tip and tip != "information":
            pos = self.mapToGlobal(self.rect().bottomLeft())
            QtWidgets.QToolTip.showText(pos, tip, self)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._showTooltip()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        QtWidgets.QToolTip.hideText()
