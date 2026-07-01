from UI.loupeProperties import ClientFeature
from ffast.metrics.models import IntParameter

SCHEMA_PARAMS = {
    "videoFPS": IntParameter(
        type="int", default=30, min=1, max=5000, role="present",
        label="FPS",
    ),
    "videoSkipFrames": IntParameter(
        type="int", default=0, min=0, max=99999, role="present",
        label="Skip frames",
    ),
}


def loadLoupe(UIHandler, loupe):
    from UI.Templates import Widget, SettingsPane, Slider, ToolButton

    settings = loupe.settings
    settings.addParameters(**{"videoFPS": [30], "videoSkipFrames": [0]})

    pane = Widget(parent=loupe, layout="vertical")

    # PLAYBACK
    playbackWindow = Widget(parent=pane, layout="vertical")
    loupe.indexSlider = Slider(parent=playbackWindow)
    loupe.indexSlider.setCallbackFunc(loupe.setIndex)
    playbackWindow.layout.addWidget(loupe.indexSlider)

    arrowBar = Widget(parent=pane, layout="horizontal")
    loupe.indexLeftArrow = ToolButton(loupe.onPrevious, "leftArrow", parent=arrowBar)
    loupe.indexLeftArrow.setToolTip("Previous frame")
    loupe.playButton = ToolButton(loupe.toggleVideo, "start", parent=arrowBar)
    loupe.playButton.setToolTip("Toggle animation")
    loupe.indexRightArrow = ToolButton(loupe.onNext, "rightArrow", parent=arrowBar)
    loupe.indexRightArrow.setToolTip("Next frame")

    arrowBar.layout.addStretch()
    arrowBar.layout.addWidget(loupe.indexLeftArrow)
    arrowBar.layout.addWidget(loupe.playButton)
    arrowBar.layout.addWidget(loupe.indexRightArrow)
    arrowBar.layout.addStretch()

    playbackWindow.layout.addWidget(arrowBar)
    pane.layout.addWidget(playbackWindow)

    # SETTINGS
    settingsPane = SettingsPane(UIHandler, settings, parent=pane)
    settingsPane.addFromParameterSchema(SCHEMA_PARAMS)
    pane.layout.addWidget(settingsPane)

    loupe.addSidebarPane("INDEX / VIDEO", pane)


CLIENT_FEATURES = [ClientFeature(stage_id=None, widget_factory=loadLoupe)]
