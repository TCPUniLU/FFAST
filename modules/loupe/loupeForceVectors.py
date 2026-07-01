from UI.loupe.visual import AtomSelectionBase
from UI.clientFeatures import ClientFeature
from ffast.metrics.models import BoolParameter, IntParameter

SCHEMA_PARAMS = {
    "showForceVectors": BoolParameter(
        type="bool", default=False, role="present",
        label="Show force vectors",
        description="Overlay 3D force arrows on atoms. Ground truth or prediction source.",
    ),
    "forceVectorsNormalised": BoolParameter(
        type="bool", default=True, role="present",
        label="Normalised",
        description="Rescale arrows so the longest force has a fixed length.",
    ),
    "forceVectorsLength": IntParameter(
        type="int", default=10, min=1, max=200, role="present",
        label="Length",
        description="Scale factor for arrow length.",
    ),
}

FILTER_PARAMS = {
    "forceVectorsFilterEnabled": BoolParameter(
        type="bool", default=False, role="present",
        label="Filter to selection",
        description="Show force arrows only on selected atoms.",
    ),
}


class _ForceVectorSelect(AtomSelectionBase):
    """Atom selection tool: choose which atoms show force vector arrows."""
    multiselect = 10000
    rectangleSelect = True
    label = "Force Vector Atoms"

    def __init__(self, canvas, **kwargs):
        super().__init__(canvas, **kwargs)
        indices = canvas.settings.get("forceVectorsAtomIndices")
        if indices:
            self.selectedPoints = list(indices)

    def selectCallback(self):
        self.canvas.loupe.settings.setParameter(
            "forceVectorsAtomIndices", list(self.selectedPoints), refresh=True,
        )


def loadLoupe(UIHandler, loupe):
    from UI.Templates import Widget, SettingsPane, PushButton, ObjectComboBox
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    settings = loupe.settings
    settings.addParameters(
        **{
            "showForceVectors": [False, "applyForceVectors"],
            "forceVectorsModelKey": [None, "applyForceVectors"],
            "forceVectorsLength": [10, "applyForceVectors"],
            "forceVectorsNormalised": [True, "applyForceVectors"],
            "forceVectorsFilterEnabled": [False, "applyForceVectors"],
            "forceVectorsAtomIndices": [[], "applyForceVectors"],
        }
    )
    settings.markAsPerDataset("showForceVectors")
    settings.markAsPerDataset("forceVectorsModelKey")
    settings.markAsPerDataset("forceVectorsAtomIndices")

    pane = Widget(parent=loupe, layout="vertical")
    settingsPane = SettingsPane(UIHandler, settings, parent=pane)
    settingsPane.addFromParameterSchema(SCHEMA_PARAMS)
    pane.layout.addWidget(settingsPane)

    class ForceSourceComboBox(ObjectComboBox):
        def updateList(self, *args):
            self.currentlyUpdatingList = True
            model_keys = self.env.models.all_keys()
            self.currentKeyList = [None] + list(model_keys)
            self.clear()
            self.addItems(
                ["Ground Truth"] + [
                    self.env.getModelOrDataset(k).getDisplayName()
                    for k in model_keys
                ]
            )
            if self.selectedKey in self.currentKeyList:
                self.setCurrentIndex(self.currentKeyList.index(self.selectedKey))
                self.currentlyUpdatingList = False
            elif self.currentKeyList:
                self.setCurrentIndex(0)
                self.currentlyUpdatingList = False
                self.forceUpdate()
            else:
                self.currentlyUpdatingList = False

    sourceCombo = ForceSourceComboBox(UIHandler, hasDatasets=False)
    sourceCombo.setOnIndexChanged(
        lambda key: loupe.settings.setParameter("forceVectorsModelKey", key, refresh=True)
    )
    pane.layout.addWidget(sourceCombo)

    filterPane = SettingsPane(UIHandler, settings, parent=pane)
    filterPane.addFromParameterSchema(FILTER_PARAMS)
    pane.layout.addWidget(filterPane)

    filterRow = QWidget()
    filterRowLayout = QHBoxLayout(filterRow)
    filterRowLayout.setContentsMargins(0, 0, 0, 0)

    selectBtn = PushButton("Select atoms")
    selectBtn.setToolTip(
        "Pick atoms to show force arrows on. Click to toggle; drag to box-select."
    )
    selectBtn.clicked.connect(lambda: loupe.setActiveAtomSelectTool(_ForceVectorSelect))

    clearBtn = PushButton("Clear")
    clearBtn.setToolTip("Remove all atoms from force vector selection.")

    def _clear_force_selection():
        loupe.settings.setParameter("forceVectorsAtomIndices", [], refresh=True)
        canvas = loupe.canvas
        if canvas.isActiveAtomSelectTool(_ForceVectorSelect):
            canvas.activeAtomSelectTool.selectedPoints = []
            canvas.visualRefresh(force=True)

    clearBtn.clicked.connect(_clear_force_selection)
    filterRowLayout.addWidget(selectBtn)
    filterRowLayout.addWidget(clearBtn)
    pane.layout.addWidget(filterRow)

    loupe.addSidebarPane("FORCE VECTORS", pane)


CLIENT_FEATURES = [ClientFeature(stage_id="ffast.force_arrows", widget_factory=loadLoupe, tool_class=_ForceVectorSelect)]
