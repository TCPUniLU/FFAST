"""Control + selector registries (ADR 0021, Phase 5).

Bespoke pieces a declarative Analysis Tab needs but the generic Panel engine
should not hard-code:

* **Selectors** — tab-level data selectors that replace the default
  model/dataset selector (e.g. the element picker, which also chooses elements).
  A tab names one with ``selector = "atomic"``.
* **Controls** — panel-level control-widget factories, named in a Panel's
  ``controls`` list (reserved for knobs the auto-generated Parameter-Schema
  controls can't express).
* **Tab controls** — tab-level control-widget factories, named in a tab's
  ``controls`` list. They receive every Panel in the tab and drive a shared
  compute-param across the ones that declare it (e.g. the energy-shift toggle).

All three are looked up by name by the TOML builder; the engine stays unaware of
them. The element picker is the headline Phase-5 selector — the Atomic Errors
tab's bespoke selector, ported off the (since-retired) legacy Atomic Errors tab.
"""
import logging

logger = logging.getLogger("FFAST")

# name -> factory(UIHandler, parent=None) -> selector widget
SELECTORS = {}
# name -> factory(panel, parent=None) -> control widget
CONTROLS = {}
# name -> factory(content_tab, panels, parent=None) -> tab-level control widget
TAB_CONTROLS = {}


def register_selector(name):
    def deco(factory):
        SELECTORS[name] = factory
        return factory
    return deco


def register_control(name):
    def deco(factory):
        CONTROLS[name] = factory
        return factory
    return deco


def register_tab_control(name):
    def deco(factory):
        TAB_CONTROLS[name] = factory
        return factory
    return deco


def make_selector(name, UIHandler, parent=None):
    factory = SELECTORS.get(name)
    if factory is None:
        raise ValueError(f"Unknown selector '{name}'. Known: {sorted(SELECTORS)}")
    return factory(UIHandler, parent=parent)


def make_control(name, panel, parent=None):
    factory = CONTROLS.get(name)
    if factory is None:
        raise ValueError(f"Unknown control '{name}'. Known: {sorted(CONTROLS)}")
    return factory(panel, parent=parent)


def make_tab_control(name, content_tab, panels, parent=None):
    factory = TAB_CONTROLS.get(name)
    if factory is None:
        raise ValueError(f"Unknown tab control '{name}'. Known: {sorted(TAB_CONTROLS)}")
    return factory(content_tab, panels, parent=parent)


# --------------------------------------------------------------------------- #
# Element picker (the Atomic Errors selector)
# --------------------------------------------------------------------------- #
def _build_atomic_selector(UIHandler, parent=None):
    """Element-picker data selector: a model/dataset selector plus a per-element
    list. Single vs multi-element selection locks the other axes exactly as the
    legacy AtomicDatasetModelSelector did. Panels read ``getSelectedAtomInfo()``;
    element changes flow through the normal data-selection callback chain so
    bound Panels re-fetch and redraw."""
    from UI.ContentTab import DatasetModelSelector, ListCheckButton
    from UI.Templates import FlexibleListSelector
    from ffast.chemistry import atomColors, zIntToZStr

    class AtomLabel(ListCheckButton):
        def __init__(self, atomIndex, *args, **kwargs):
            self.atomIndex = atomIndex
            self.atomName = zIntToZStr[atomIndex]
            super().__init__(*args, color=atomColors[atomIndex],
                             label=self.atomName, **kwargs)

    class AtomicDatasetModelSelector(DatasetModelSelector):
        def __init__(self, UIHandler, parent=None):
            super().__init__(UIHandler, parent=parent)
            self.lastSelectedDatasets = set()  # instance-scoped (legacy used a class attr)
            self.atomsList = FlexibleListSelector(
                parent=self, label="Selected elements", elementSize=50
            )
            self.atomsList.setOnUpdate(self.update)
            self.layout.addWidget(self.atomsList)

        def getSelectedAtomIndices(self):
            return [x.atomIndex for x in self.atomsList.getSelectedWidgets()]

        def getSelectedAtomInfo(self):
            info = {}
            for i in self.getSelectedAtomIndices():
                info[zIntToZStr[i]] = {"index": i, "color": atomColors[i]}
            return info

        def update(self):
            modelKeys, datasetKeys = self.getSelectedKeys()
            datasetKeySet = set(datasetKeys)
            if datasetKeySet != self.lastSelectedDatasets:
                self.lastSelectedDatasets = datasetKeySet
                self.updateAtomsList()

            nTypes = len(self.getSelectedAtomIndices())
            # single element unlocks both axes; multiple elements lock to one pair
            self.atomsList.singleSelection = len(modelKeys) > 1 or len(datasetKeys) > 1
            self.modelsList.singleSelection = nTypes > 1
            self.datasetsList.singleSelection = nTypes > 1
            DatasetModelSelector.update(self)

        def updateAtomsList(self):
            self.atomsList.removeWidgets(clear=True)
            elements = set()
            for key in self.lastSelectedDatasets:
                dataset = self.handler.env.datasets.get(key)
                if dataset is not None:
                    elements |= set(dataset.getElements())
            if elements:
                self.atomsList.addWidget(AtomLabel(0, parent=self.atomsList))  # "All"
            for i in sorted(elements):
                self.atomsList.addWidget(AtomLabel(i, parent=self.atomsList))

    return AtomicDatasetModelSelector(UIHandler, parent=parent)


register_selector("atomic")(_build_atomic_selector)


# --------------------------------------------------------------------------- #
# Panel-level controls
# --------------------------------------------------------------------------- #
@register_control("smoothing")
def _build_smoothing_control(panel, parent=None):
    """One Smoothing slider driving the hidden ``window`` compute-param across
    every series of a Panel (e.g. the gyradius overlay), via the Phase-4
    ``setSharedParam`` path."""
    from UI.Templates import Slider

    slider = Slider(parent=panel, hasEditBox=True, label="Smoothing",
                    nMin=1, nMax=10000)
    slider.setToolTip("Number of points in the sliding average")
    slider.setCallbackFunc(lambda v: panel.setSharedParam("window", int(v)))
    return slider


# --------------------------------------------------------------------------- #
# Tab-level controls
# --------------------------------------------------------------------------- #
@register_tab_control("energy_shift")
def _build_energy_shift(content_tab, panels, parent=None):
    """The Basic Errors energy-shift toggle: one checkbox driving the shared
    ``shifted`` compute-param across every energy Panel (those whose bound Metric
    declares it), appending a " (shifted)" title suffix. Ported off the imperative
    Basic Errors tab; replaces its hand-wired ``setSharedParam`` loop."""
    from PySide6.QtWidgets import QCheckBox

    PARAM = "shifted"
    affected = [p for p in panels if p.hasParam(PARAM)]
    base_titles = {id(p): p.titleLabel.text() for p in affected}

    checkbox = QCheckBox("Subtract mean energy offset", parent=parent or content_tab)
    checkbox.setToolTip(
        "Remove constant energy offset by subtracting mean(E_predicted - E_true)"
    )

    def on_toggled(state):
        shifted = bool(state)
        for panel in affected:
            panel.setSharedParam(PARAM, shifted)
            panel.titleLabel.setText(
                base_titles[id(panel)] + (" (shifted)" if shifted else "")
            )

    checkbox.stateChanged.connect(on_toggled)
    return checkbox
