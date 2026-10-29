from UI.loupe.visual import AtomSelectionBase
from UI.clientFeatures import ClientFeature
import math
from ffast.chemistry import geometry


def _degrees(value):
    """Read-out text for an angle; undefined geometry (nan) says so."""
    return "undefined (atoms in a line)" if math.isnan(value) else f"{value:.1f}"

DEPENDENCIES = ["loupeAtoms"]


class AtomInfoSelect(AtomSelectionBase):
    multiselect = 4
    label = "Atoms Info"
    toolbarName = "Info"
    paneName = None  # readout lives in the pick strip, no sidebar pane to expand
    cycle = True

    def __init__(self, canvas, **kwargs):
        super().__init__(canvas, **kwargs)

    def selectCallback(self):
        self.updateInfo()

    def applySelectedAtoms(self):
        self.canvas.loupe.settings.setParameter(
            "alignAtomsIndices", self.selectedPoints
        )

    def getInfoLabel(self):
        idxs = self.selectedPoints
        N = len(idxs)
        if N == 1:
            return self.singleAtomInfo(*idxs)
        elif N == 2:
            return self.distanceInfo(*idxs)
        elif N == 3:
            return self.angleInfo(*idxs)
        elif N == 4:
            return self.dihedralInfo(*idxs)

    def singleAtomInfo(self, i):
        R = self.canvas.getCurrentR()[i]
        z = self.canvas.dataset.getElementsName()[i]

        return f"Atom {i} / Element {z} / ({R[0]:.2f},{R[1]:.2f},{R[2]:.2f})"

    def distanceInfo(self, i, j):
        R = self.canvas.getCurrentR()
        z = self.canvas.dataset.getElementsName()

        d = geometry.distance(R[i], R[j])

        return f"Atoms {i},{j} / Elements {z[i]},{z[j]} / Distance: {d:.2f}"

    def angleInfo(self, i, j, k):
        R = self.canvas.getCurrentR()
        z = self.canvas.dataset.getElementsName()

        a = geometry.angle(R[i], R[j], R[k])
        return f"Atoms {i},{j},{k} / Elements {z[i]},{z[j]},{z[k]} / Angle: {_degrees(a)}"

    def dihedralInfo(self, i, j, k, l):
        R = self.canvas.getCurrentR()
        z = self.canvas.dataset.getElementsName()

        a = geometry.dihedral(R[i], R[j], R[k], R[l])
        return f"Atoms {i},{j},{k},{l} / Elements {z[i]},{z[j]},{z[k]},{z[l]} / Dihedral: {_degrees(a)}"


# The Atoms-Info readout (position/distance/angle/dihedral) is shown in the
# contextual pick strip on the canvas; the tool is armed from the shared pick
# toolbar (ADR 0039), so this feature contributes only its tool_class.
CLIENT_FEATURES = [ClientFeature(tool_class=AtomInfoSelect)]
