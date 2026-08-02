from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSizePolicy
import logging
from functools import partial
from ffast.core.events import EventChildClass
from UI.Templates.base import Widget, ComboBox, LineEdit
from UI.Templates.interactive import Slider, CodeTextEdit, CodeLineEdit

logger = logging.getLogger("FFAST")


class SettingsWidgetBase(Widget, EventChildClass):

    hideFunc = None
    callbackFunc = None
    quiet = False

    def __init__(
        self,
        handler,
        name,
        settings=None,
        settingsKey=None,
        hasLabel=True,
        layout="horizontal",
        fixedHeight=True,
        parent=None,
        labelWidth=140,
        **kwargs,
    ):
        super().__init__(layout=layout, parent=parent, **kwargs)
        self.handler = handler
        self.name = name

        if parent is None:
            logger.exception(
                f"SettingsWidget {self} was not given pane as parent: parent = {parent}"
            )
        self.paneParent = parent

        self.settings = settings
        self.settingsKey = settingsKey
        self.hasSettingsKey = (settings is not None) and (
            settingsKey is not None
        )

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        if fixedHeight:
            self.setFixedHeight(40)

        if hasLabel:
            self.label = QtWidgets.QLabel(str(name), parent=self)
            self.layout.addWidget(self.label)
            self.label.setFixedWidth(labelWidth)
            self.layout.addStretch()

        # update the widget if parameter changes
        if self.hasSettingsKey:
            settings.addParameterActions(
                settingsKey, partial(self.setDefault, quiet=True)
            )

    def setHideCondition(self, func):
        self.hideFunc = func

    def updateVisibility(self):
        if (self.hideFunc is not None) and self.hideFunc():
            self.hide()
        else:
            self.show()

        self.forceUpdateParent()

    def setCallback(self, func):
        self.callbackFunc = func

    def callback(self):
        self.paneParent.updateVisibilities()
        if self.quiet:
            return
        if self.hasSettingsKey:
            self.settings.setParameter(self.settingsKey, self.getValue())
        if self.callbackFunc is not None:
            self.callbackFunc()

    def setDefault(self, quiet=False):
        if not self.hasSettingsKey:
            return
        if quiet:
            self.quiet = True
            self.setValue(self.settings.get(self.settingsKey, None))
            self.quiet = False
        else:
            self.setValue(self.settings.get(self.settingsKey, None))

    def setValue(self, *args):
        self._setValue(*args)
        self.paneParent.updateVisibilities()

    def getValue(self, *args):
        return self._getValue(*args)


class SettingsCheckBox(SettingsWidgetBase):
    def __init__(self, *args, settings=None, settingsKey=None, **kwargs):
        super().__init__(
            *args, settings=settings, settingsKey=settingsKey, **kwargs
        )

        self.checkBox = QtWidgets.QCheckBox("", self)
        self.setDefault()
        self.layout.addWidget(self.checkBox)

        self.checkBox.stateChanged.connect(self.callback)

    def _getValue(self):
        return self.checkBox.isChecked()

    def _setValue(self, b):
        self.checkBox.setChecked(bool(b) if b is not None else False)


class SettingsComboBox(SettingsWidgetBase):
    def __init__(
        self,
        *args,
        settings=None,
        settingsKey=None,
        isNumber=False,
        items=(),
        **kwargs,
    ):
        super().__init__(
            *args, settings=settings, settingsKey=settingsKey, **kwargs
        )
        self.isNumber = isNumber
        self.comboBox = ComboBox(parent=self)
        self.comboBox.setMinimumWidth(150 - 8)

        # doesnt work as Id want it to
        self.comboBox.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Expanding
        )

        self.setItems(items)
        self.setDefault()

        self.comboBox.currentIndexChanged.connect(self.callback)
        self.layout.addWidget(self.comboBox)

    def setItems(self, items):
        self.items = []
        self.comboBox.clear()
        self.comboBox.addItems(items)

    def addItems(self, items):
        self.items = self.items + items
        self.comboBox.addItems(items)

    def _getValue(self):
        t = self.comboBox.currentText()
        if self.isNumber:
            return float(t)
        else:
            return str(t)

    def _setValue(self, value):
        self.comboBox.setCurrentText(value)


class SettingsCodeBox(SettingsWidgetBase):
    def __init__(
        self,
        *args,
        settings=None,
        settingsKey=None,
        validationFunc=None,
        labelDirection="vertical",
        singleLine=False,
        **kwargs,
    ):
        super().__init__(
            *args,
            settings=settings,
            settingsKey=settingsKey,
            layout=labelDirection,
            fixedHeight=False,
            **kwargs,
        )

        if not singleLine:
            self.codeBox = CodeTextEdit(
                parent=self, validationFunc=validationFunc
            )
        else:
            self.codeBox = CodeLineEdit(
                parent=self, validationFunc=validationFunc
            )

        self.codeBox.setReturnCallback(self.callback)

        self.layout.addWidget(self.codeBox)
        self.layout.setSpacing(8)

    def _setValue(self, value):
        self.codeBox.setCode(value)

    def _getValue(self):
        return self.codeBox.getValue()


class SettingsLineEdit(SettingsWidgetBase):
    def __init__(
        self,
        *args,
        settings=None,
        settingsKey=None,
        isFloat=False,
        isInt=False,
        nMin=0,
        nMax=99999,
        **kwargs,
    ):
        super().__init__(
            *args, settings=settings, settingsKey=settingsKey, **kwargs
        )

        self.isFloat = isFloat
        self.isInt = isInt

        self.lineEdit = LineEdit("?", parent=self)
        self.lineEdit.setFixedWidth(150 - 8)
        self.lineEdit.setMaxLength(10)

        if self.isInt:
            val = QtGui.QIntValidator(nMin, nMax)
            self.lineEdit.setValidator(val)
        elif self.isFloat:
            val = QtGui.QDoubleValidator(
                nMin, nMax, 4, notation=QtGui.QDoubleValidator.StandardNotation
            )
            # Set locale to C (uses period as decimal separator)
            from PySide6.QtCore import QLocale
            val.setLocale(QLocale.c())
            self.lineEdit.setValidator(val)

        self.setDefault()

        self.lineEdit.setOnEdit(self.callback)
        self.layout.addWidget(self.lineEdit)

    def _getValue(self):
        t = self.lineEdit.text()
        if self.isFloat:
            logging.info(f"Getting float value {t} from SettingsLineEdit {self}")
            return float(t)
        elif self.isInt:
            return int(t)
        else:
            return str(t)

    def _setValue(self, value):
        self.lineEdit.setText(str(value))


class SettingsContainer(SettingsWidgetBase):
    def __init__(self, *args, **kwargs):
        super().__init__(
            self, *args, hasLabel=False, fixedHeight=False, **kwargs
        )


class SettingsSlider(SettingsWidgetBase):
    def __init__(
        self,
        *args,
        settings=None,
        settingsKey=None,
        nMin=0,
        nMax=99999,
        **kwargs,
    ):
        super().__init__(
            *args, settings=settings, settingsKey=settingsKey, **kwargs
        )

        self.slider = Slider(parent=self)
        self.slider.setCallbackFunc(self.onSlide)

        self.slider.setMinMax(nMin, nMax)

        self.setDefault()
        self.layout.addWidget(self.slider)

    def _getValue(self):
        return self.slider.getValue()

    def _setValue(self, value):
        return self.slider.setValue(value, quiet=True)

    def onSlide(self, *args):
        self.callback()


class SettingsPane(Widget, EventChildClass):
    def __init__(self, UIHandler, settings, **kwargs):
        self.handler = UIHandler
        super().__init__(layout="vertical", **kwargs)
        EventChildClass.__init__(self)
        self.settingsWidgets = {}
        self.settings = settings
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(8)

    def addSetting(
        self,
        typ,
        name,
        manualLayout=False,
        insertIndex=None,
        settingsKey=None,
        toolTip=None,
        **kwargs,
    ):

        if settingsKey is None:
            logger.warn(
                f"Adding setting of name {name} and type {typ} to SettingsPane, but no settings key given."
            )

        if name in self.settingsWidgets:
            logger.error(
                f"Tried to add setting of name {name} and type {typ} but name already taken in pane."
            )
            return

        if typ == "ComboBox":
            el = SettingsComboBox(
                self.handler,
                name,
                settingsKey=settingsKey,
                settings=self.settings,
                parent=self,
                **kwargs,
            )

        elif typ == "LineEdit":
            el = SettingsLineEdit(
                self.handler,
                name,
                settingsKey=settingsKey,
                settings=self.settings,
                parent=self,
                **kwargs,
            )

        elif typ == "CheckBox":
            el = SettingsCheckBox(
                self.handler,
                name,
                settingsKey=settingsKey,
                settings=self.settings,
                parent=self,
                **kwargs,
            )

        elif typ == "CodeBox":
            el = SettingsCodeBox(
                self.handler,
                name,
                settingsKey=settingsKey,
                settings=self.settings,
                parent=self,
                **kwargs,
            )

        elif typ == "Container":
            el = SettingsContainer(self.handler, name, parent=self, **kwargs)

        elif typ == "Slider":
            el = SettingsSlider(
                self.handler,
                name,
                settingsKey=settingsKey,
                settings=self.settings,
                parent=self,
                **kwargs,
            )

        else:
            logger.error(
                f"Tried to make setting for SettingsPane {self} but type {typ} not recognised"
            )
            return

        if not manualLayout:
            if insertIndex is None:
                self.layout.addWidget(el)
            else:
                self.layout.insertWidget(insertIndex, el)
            self.settingsWidgets[name] = el

        if toolTip is not None:
            el.setToolTip(toolTip)

        self.updateVisibilities()

        return el

    def getSettingValue(self, name):
        el = self.settingsWidgets.get(name, None)
        if el is None:
            return None
        return el.getValue()

    def addFromParameterSchema(self, params: dict) -> dict:
        """Add controls for each entry in *params* (key → ParameterSchema).

        Mapping:
          BoolParameter   → CheckBox
          ChoiceParameter → ComboBox (static choices)
          IntParameter    → Slider when both min+max set, else LineEdit (isInt)
          FloatParameter  → LineEdit (isFloat)
          StringParameter → LineEdit

        Returns a dict of {key: widget} for the added controls.
        """
        from ffast.metrics.models import (
            BoolParameter, ChoiceParameter, FloatParameter,
            IntParameter, StringParameter,
        )
        added = {}
        for key, param in params.items():
            lbl = param.label or key
            tip = param.description or None
            if isinstance(param, BoolParameter):
                w = self.addSetting("CheckBox", lbl, settingsKey=key, toolTip=tip)
            elif isinstance(param, ChoiceParameter):
                w = self.addSetting("ComboBox", lbl, settingsKey=key, items=param.choices, toolTip=tip)
            elif isinstance(param, IntParameter):
                if param.min is not None and param.max is not None:
                    w = self.addSetting("Slider", lbl, settingsKey=key, nMin=param.min, nMax=param.max, toolTip=tip)
                else:
                    w = self.addSetting("LineEdit", lbl, settingsKey=key, isInt=True,
                        nMin=param.min if param.min is not None else 0,
                        nMax=param.max if param.max is not None else 99999,
                        toolTip=tip)
            elif isinstance(param, FloatParameter):
                w = self.addSetting("LineEdit", lbl, settingsKey=key, isFloat=True,
                    nMin=param.min if param.min is not None else -1e9,
                    nMax=param.max if param.max is not None else 1e9,
                    toolTip=tip)
            elif isinstance(param, StringParameter):
                w = self.addSetting("LineEdit", lbl, settingsKey=key, toolTip=tip)
            else:
                continue
            if w is not None:
                added[key] = w
        return added

    def updateVisibilities(self):
        for _, v in self.settingsWidgets.items():
            v.updateVisibility()
