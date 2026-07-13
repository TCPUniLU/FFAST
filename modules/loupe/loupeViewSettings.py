from UI.Templates import SettingsPane
from UI.clientFeatures import ClientFeature
from ffast.metrics.models import BoolParameter, IntParameter, StringParameter

# ADR 0040: the old VIEW SETTINGS grab-bag is dissolved into two themed panes.
# This module keeps ownership of the parameter registrations + action wirings
# (moving those is the risky part), and only changes which panes present them.

# ── ALIGNMENT pane (Analysis group) ──────────────────────────────────────────
SCHEMA_ALIGN = {
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
    "alignAtoms": BoolParameter(
        type="bool", default=False, role="present",
        label="3-atom frame align",
        description="Align frames using 3 reference atoms. Pick 3 atoms with the Align tool.",
    ),
    "alignAtomsIndices": StringParameter(
        type="string", default="", role="present",
        label="Align atom indices",
        description="Three atom indices for frame alignment (e.g. '0 1 2').",
    ),
}

# ── DISPLAY pane (Appearance group) ──────────────────────────────────────────
SCHEMA_DISPLAY = {
    "showSceneLabels": BoolParameter(
        type="bool", default=False, role="present",
        label="Atom index labels",
    ),
    "sceneFilterIndices": StringParameter(
        type="string", default="", role="present",
        label="Hide atoms",
        description="Indices or elements to hide from the view: '0 1 2', 'C', or '-H'. Empty = show all. (View only — does not create a dataset.)",
    ),
    "sceneSelectIndices": StringParameter(
        type="string", default="", role="present",
        label="Highlight atoms",
        description="Atom indices to highlight as a selection overlay. Empty = none.",
    ),
    "pickRadius": IntParameter(
        type="int", default=12, min=4, max=40, role="present",
        label="Pick radius (px)",
    ),
}


def loadLoupe(UIHandler, loupe):
    settings = loupe.settings
    # Parameter registrations + action wirings are unchanged (ADR 0040 keeps
    # these here); only the presentation is regrouped below.
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

    # DISPLAY pane
    displayPane = SettingsPane(UIHandler, settings, parent=loupe)
    displayPane.addFromParameterSchema(SCHEMA_DISPLAY)
    loupe.addSidebarPane("DISPLAY", displayPane)

    # ALIGNMENT pane — dependent fields hidden until their mode is on.
    alignPane = SettingsPane(UIHandler, settings, parent=loupe)
    alignWidgets = alignPane.addFromParameterSchema(SCHEMA_ALIGN)
    alignWidgets["alignKabschHeavyOnly"].setHideCondition(
        lambda: not settings.get("alignKabsch")
    )
    alignWidgets["alignAtomsIndices"].setHideCondition(
        lambda: not settings.get("alignAtoms")
    )
    loupe.addSidebarPane("ALIGNMENT", alignPane)


CLIENT_FEATURES = [ClientFeature(stage_id=None, widget_factory=loadLoupe)]
