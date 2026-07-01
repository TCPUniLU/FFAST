from UI.clientFeatures import ClientFeature

DEPENDENCIES = ["loupeCamera"]


def addSettings(UIHandler, loupe):
    settings = loupe.settings
    settings.addParameters(**{
        "showUnitCell": [False, "applyUnitCell"],
    })


def addSettingsPane(UIHandler, loupe):
    from UI.Templates import SettingsPane

    pane = SettingsPane(UIHandler, loupe.settings, parent=loupe)

    pane.addSetting(
        "CheckBox",
        "Show Unit Cell",
        settingsKey="showUnitCell",
        toolTip="Display unit cell edges",
    )

    loupe.addSidebarPane("UNIT CELL", pane)


def loadLoupe(UIHandler, loupe):
    addSettings(UIHandler, loupe)
    addSettingsPane(UIHandler, loupe)


CLIENT_FEATURES = [ClientFeature(stage_id="ffast.unit_cell_edges", widget_factory=loadLoupe)]
