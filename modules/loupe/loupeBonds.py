from config.userConfig import getConfig
from utils import cleanBondIdxsArray
from UI.loupeProperties import AtomSelectionBase, ClientFeature

DEPENDENCIES = ["loupeCamera"]


class BondSelect(AtomSelectionBase):
    multiselect = 2
    label = "Bond Selection"

    def __init__(self, canvas, **kwargs):
        super().__init__(canvas, **kwargs)

        self.bonds = []

    def selectCallback(self):
        if len(self.selectedPoints) != 2:
            return

        loupe = self.canvas.loupe
        bonds = loupe.settings.get("fixedBondIndices")

        bonds = set(bonds)
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

    # SELECT BONDS BTN

    def selectBonds():
        loupe.setActiveAtomSelectTool(BondSelect)

    selectButton = PushButton("Select")
    selectButton.setToolTip(
        "Click to manually add/remove bonds in the visualiser"
    )
    selectButton.clicked.connect(selectBonds)
    container.layout.addWidget(selectButton)

    loupe.addSidebarPane("BONDS", pane)


def loadLoupe(UIHandler, loupe):
    addSettings(UIHandler, loupe)
    addSettingsPane(UIHandler, loupe)


CLIENT_FEATURES = [ClientFeature(stage_id="ffast.bond_positions", widget_factory=loadLoupe)]
