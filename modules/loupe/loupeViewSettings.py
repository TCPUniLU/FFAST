from UI.Templates import Widget, SettingsPane
from UI.clientFeatures import ClientFeature
from ffast.metrics.models import BoolParameter, IntParameter, StringParameter

SCHEMA_PARAMS = {
    "alignKabsch": BoolParameter(
        type="bool", default=False, role="present",
        label="Kabsch align",
        description="Rigidly align every frame onto frame 0 using a Kabsch rotation (minimises RMSD).",
    ),
    "alignKabschHeavyOnly": BoolParameter(
        type="bool", default=True, role="present",
        label="Heavy atoms only",
        description="Use only heavy atoms (Z > 1) to compute the Kabsch alignment rotation.",
    ),
    "showSceneLabels": BoolParameter(
        type="bool", default=False, role="present",
        label="Atom index labels",
    ),
    "sceneFilterIndices": StringParameter(
        type="string", default="", role="present",
        label="Filter indices",
        description="Indices or elements to keep: '0 1 2', 'C', or '-H' to exclude. Empty = all.",
    ),
    "sceneSelectIndices": StringParameter(
        type="string", default="", role="present",
        label="Highlight indices",
        description="Atom indices to highlight as a selection overlay. Empty = none.",
    ),
}

SCHEMA_PARAMS_COLOR = {
    "pickRadius": IntParameter(
        type="int", default=12, min=4, max=40, role="present",
        label="Pick radius (px)",
    ),
    "alignAtoms": BoolParameter(
        type="bool", default=False, role="present",
        label="3-atom frame align",
        description="Align frames using 3 reference atoms. Enter 3 atom indices below.",
    ),
    "alignAtomsIndices": StringParameter(
        type="string", default="", role="present",
        label="Align atom indices",
        description="Three atom indices for frame alignment (e.g. '0 1 2').",
    ),
}


def loadLoupe(UIHandler, loupe):
    settings = loupe.settings
    settings.addParameters(
        **{
            "alignKabsch": [False, "toggleKabschAlign"],
            "alignKabschHeavyOnly": [True, "toggleKabschAlign"],
            "showSceneLabels": [False, "toggleSceneLabels"],
            "sceneFilterIndices": ["", "applySceneFilter"],
            "sceneSelectIndices": ["", "applySceneSelection"],
            "pickRadius": [12],
            "alignAtoms": [False, "applyAtomAlign"],
            "alignAtomsIndices": ["", "applyAtomAlign"],
            "alignAtomsConfIndex": [0, "applyAtomAlign"],
        }
    )
    settings.markAsPerDataset("alignAtoms")
    settings.markAsPerDataset("alignAtomsIndices")
    settings.markAsPerDataset("alignAtomsConfIndex")

    pane = Widget(parent=loupe, layout="vertical")
    settingsPane = SettingsPane(UIHandler, settings, parent=pane)
    settingsPane.addFromParameterSchema(SCHEMA_PARAMS)
    settingsPane.addFromParameterSchema(SCHEMA_PARAMS_COLOR)
    pane.layout.addWidget(settingsPane)
    loupe.addSidebarPane("VIEW SETTINGS", pane)


CLIENT_FEATURES = [ClientFeature(stage_id=None, widget_factory=loadLoupe)]
