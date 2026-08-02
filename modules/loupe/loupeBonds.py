from ffast.config.user import getConfig
from utils import cleanBondIdxsArray
from UI.loupe.visual import AtomSelectionBase
from UI.clientFeatures import ClientFeature

DEPENDENCIES = ["loupeCamera"]


class BondSelect(AtomSelectionBase):
    multiselect = 2
    label = "Bond Selection"
    toolbarName = "Bonds"
    paneName = "BONDS"

    def __init__(self, canvas, **kwargs):
        super().__init__(canvas, **kwargs)

        self.bonds = []

    def selectCallback(self):
        if len(self.selectedPoints) != 2:
            return

        loupe = self.canvas.loupe
        bonds = loupe.settings.get("fixedBondIndices")

        # The default view shows dynamic (distance-computed) bonds while the
        # fixed set is empty. Editing an empty set would collapse the view to
        # the single picked bond, so seed it with the currently-shown bonds —
        # then picking an existing bond removes just that one, and picking a
        # new pair adds it. (Also avoids set(None) on the None default.)
        if not bonds:
            dataset = loupe.getSelectedDataset()
            try:
                bonds = dataset.getBondIndices(loupe.index)
            except Exception:
                bonds = []
        bonds = set(tuple(sorted((int(a), int(b)))) for a, b in bonds)
        (p1, p2) = self.selectedPoints
        p1, p2 = int(p1), int(p2)
        if p1 < p2:
            sel = (p1, p2)
        else:
            sel = (p2, p1)
        if sel in bonds:
            bonds.remove(sel)
        else:
            bonds.add(sel)

        self.clearSelection()
        self.updateBonds(bonds)

    def updateBonds(self, bonds):
        bonds = list(bonds)

        self.canvas.loupe.settings.setParameter(
            "fixedBondIndices", bonds, refresh=True
        )


def addSettings(UIHandler, loupe):
    settings = loupe.settings
    settings.addParameters(
        **{
            "bondType": ["Fixed", "applyBonds"],
            "bondWidth": [100],
            "bondColor": [getConfig("loupeBondsColor")],
            "fixedBondIndices": [None, "applyBonds"],
        }
    )


def addSettingsPane(UIHandler, loupe):
    from UI.Templates import SettingsPane, PushButton

    settings = loupe.settings

    pane = SettingsPane(UIHandler, loupe.settings, parent=loupe)

    # Bond width + colour (moved off the top menu). bondWidth/bondColor changes
    # are picked up live via the adapter restyle hook (window._ensureAdapterHooks).
    pane.addSetting(
        "Slider",
        "Bond width",
        settingsKey="bondWidth",
        nMin=10,
        nMax=100,
        toolTip="Thickness of the bond cylinders",
    )

    bondColorBtn = PushButton("Bond colour…")
    bondColorBtn.setToolTip("Pick the bond colour")

    def _pickBondColor():
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor
        current = QColor(settings.get("bondColor", getConfig("loupeBondsColor", "#404040")))
        color = QColorDialog.getColor(current, loupe, "Select Bond Colour")
        if color.isValid():
            settings.setParameter("bondColor", color.name(), refresh=True)

    bondColorBtn.clicked.connect(_pickBondColor)
    pane.layout.addWidget(bondColorBtn)

    pane.addSetting(
        "ComboBox",
        f"Bonds Type",
        settingsKey="bondType",
        items=["Fixed", "Dynamic"],
        toolTip="Change how bonds are generated",
    )

    s = pane.addSetting(
        "CodeBox",
        "Bond Indices",
        settingsKey="fixedBondIndices",
        validationFunc=cleanBondIdxsArray,
    )
    s.setHideCondition(lambda: settings.get("bondType") != "Fixed")
    s.setFixedHeight(200)

    ## ADD BONDS BUTTONS
    container = pane.addSetting(
        "Container", "Bonds Indices Container", layout="horizontal"
    )
    container.setHideCondition(lambda: settings.get("bondType") != "Fixed")

    # DYNAMIC FILL BTN
    def bondsDynamicFill():
        dataset = loupe.getSelectedDataset()
        idxs = dataset.getBondIndices(loupe.index)
        loupe.settings.setParameter("fixedBondIndices", idxs, refresh=True)

    dynamicFillBtn = PushButton("Dynamic")
    dynamicFillBtn.setToolTip(
        "Click to fill the bond indices based on current pairwise distances"
    )
    dynamicFillBtn.clicked.connect(bondsDynamicFill)
    container.layout.addWidget(dynamicFillBtn)

    # Bond picking is armed from the shared pick toolbar (ADR 0039).

    loupe.addSidebarPane("BONDS", pane)


def loadLoupe(UIHandler, loupe):
    addSettings(UIHandler, loupe)
    addSettingsPane(UIHandler, loupe)


CLIENT_FEATURES = [ClientFeature(stage_id="ffast.bond_positions", widget_factory=loadLoupe, tool_class=BondSelect)]
