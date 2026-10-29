import logging
from UI.loupe.visual import AtomSelectionBase
from UI.clientFeatures import ClientFeature

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
    toolbarName = "Align"
    paneName = "ALIGNMENT"

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
    # ADR 0040: the align controls (checkbox + indices) live in the ALIGNMENT
    # pane (loupeViewSettings); picking is armed from the shared toolbar
    # (ADR 0039). This module only attaches the picker behaviours above.


def loadLoupe(UIHandler, loupe):
    addSettings(UIHandler, loupe)


CLIENT_FEATURES = [ClientFeature(widget_factory=loadLoupe, tool_class=AtomAlignSelect)]
