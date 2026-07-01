from UI.clientFeatures import ClientFeature

DEPENDENCIES = ["loupeAtoms", "loupeForceError"]


def loadLoupe(UIHandler, loupe):
    pane = loupe.getSettingsPane("ATOMS")
    comboBox = pane.settingsWidgets.get("Coloring")
    comboBox.addItems(["Displacement"])

    # Displacement is a built-in special source (not a registered metric), so it
    # keeps an explicit label rather than deriving one from a metric display name.
    loupe._colorSourceByLabel["Displacement"] = "displacement"


CLIENT_FEATURES = [ClientFeature(stage_id="ffast.displacement_stats", widget_factory=loadLoupe)]
