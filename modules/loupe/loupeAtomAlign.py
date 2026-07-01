import logging
from UI.loupeProperties import AtomSelectionBase, ClientFeature

logger = logging.getLogger("FFAST")

# loupeViewSettings owns the alignAtoms*/alignAtomsConfIndex settings (server-wired
# via "applyAtomAlign"); loupeCamera owns originCenterOfMass. Depend on both so those
# keys exist before this module attaches its picker behaviour + controls.
DEPENDENCIES = ["loupeAtoms", "loupeCamera", "loupeViewSettings"]


def cleanAlignAtomsIndices(arr):
    try:
        s = set([int(x) for x in arr])
        t = tuple(s)
        if len(t) != 3:
            raise ValueError

    except Exception as e:
        logger.exception(
            f"Tried to clean indices arr, but failed for: {e}. Array/List needs contain 3 dinstinct integers"
        )
        return False, None

    return True, list(s)


class AtomAlignSelect(AtomSelectionBase):
    multiselect = 3
    label = "Align Atoms Selection"

    def __init__(self, canvas, **kwargs):
        super().__init__(canvas, **kwargs)
        self.atoms = []

    def selectCallback(self):
        N = len(self.selectedPoints)
        self.updateInfo()
        if N != 3:
            return

        self.applySelectedAtoms()
        self.clearSelection()
        self.canvas.setActiveAtomSelectTool(None)

    def applySelectedAtoms(self):
        self.canvas.loupe.settings.setParameter(
            "alignAtomsIndices", self.selectedPoints
        )

    def getInfoLabel(self):
        N = len(self.selectedPoints)
        return f"Select{3-N} more points"


def addSettings(UIHandler, loupe):
    from UI.Templates import PushButton

    def updateAlignAtomsConfIndex():
        index = loupe.canvas.index
        loupe.settings.setParameter("alignAtomsConfIndex", index)

    ## SETTINGS — the alignAtoms*/alignAtomsConfIndex keys are registered and
    ## server-wired by loupeViewSettings ("applyAtomAlign"). This module only
    ## attaches the picker-specific behaviours: snap the reference frame to the
    ## current index when the indices change, and keep alignment mutually
    ## exclusive with centre-of-mass tracking.
    settings = loupe.settings
    settings.addParameterActions(
        "alignAtomsIndices", updateAlignAtomsConfIndex
    )

    ## MAKE IT EXCLUSIVE WITH COM
    settings.addParameterActions(
        "alignAtoms",
        lambda: settings.setParameter("originCenterOfMass", False)
        if settings.get("alignAtoms")
        else None,
    )
    settings.addParameterActions(
        "originCenterOfMass",
        lambda: settings.setParameter("alignAtoms", False)
        if settings.get("originCenterOfMass")
        else None,
    )

    ## SETTINGS PANE (legacy — hidden; use Kabsch alignment instead)
    pane = loupe.getSettingsPane("ATOMS")
    alignCheckBox = pane.addSetting(
        "CheckBox",
        "Align Atoms",
        settingsKey="alignAtoms",
        toolTip="Select 3 atoms to visualise on a fixed plane",
    )
    alignCheckBox.setHideCondition(lambda: True)

    container = pane.addSetting(
        "Container", "Align Atoms Indices Container", layout="horizontal"
    )
    container.setHideCondition(lambda: not settings.get("alignAtoms"))
    container.setFixedHeight(30)

    codeBox = pane.addSetting(
        "CodeBox",
        "Indices",
        settingsKey="alignAtomsIndices",
        validationFunc=cleanAlignAtomsIndices,
        labelDirection="horizontal",
        singleLine=True,
    )
    container.layout.addWidget(codeBox)
    codeBox.setToolTip("Set 3 atom indices to visualise on a fixed plane")

    ## SELECT BUTTON
    def selectAlignAtomIndices():
        loupe.setActiveAtomSelectTool(AtomAlignSelect)

    selectButton = PushButton("Select")
    selectButton.clicked.connect(selectAlignAtomIndices)
    selectButton.setToolTip(
        "Manually select 3 atoms to visualise on a fixed plane"
    )
    container.layout.addWidget(selectButton)


def loadLoupe(UIHandler, loupe):
    addSettings(UIHandler, loupe)


CLIENT_FEATURES = [ClientFeature(stage_id=None, widget_factory=loadLoupe, tool_class=AtomAlignSelect)]
