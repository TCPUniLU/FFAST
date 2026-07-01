from UI.clientFeatures import ClientFeature

DEPENDENCIES = ["loupeAtoms"]


def loadLoupe(UIHandler, loupe):
    from UI.Templates import SettingsPane, PushButton, Widget
    from PySide6.QtWidgets import QLabel

    # Flag to prevent circular updates between settings and camera
    _updating_from_camera = False

    # SETTINGS
    def updateOrthographicCamera(*args):
        ortho = loupe.canvas.settings.get("cameraOrthographic")
        if ortho:
            loupe.canvas.camera.fov = 0
        else:
            loupe.canvas.camera.fov = 45

    def updateCameraAzimuth(*args):
        nonlocal _updating_from_camera
        if _updating_from_camera:
            return
        azimuth = loupe.canvas.settings.get("cameraAzimuth")
        loupe.canvas.camera.azimuth = azimuth

    def updateCameraElevation(*args):
        nonlocal _updating_from_camera
        if _updating_from_camera:
            return
        elevation = loupe.canvas.settings.get("cameraElevation")
        loupe.canvas.camera.elevation = elevation

    def updateCameraDistance(*args):
        nonlocal _updating_from_camera
        if _updating_from_camera:
            return
        distance = loupe.canvas.settings.get("cameraDistance")
        loupe.canvas.camera.distance = distance

    def syncSettingsFromCamera():
        """Update settings to reflect current camera state (called when camera changes)."""
        nonlocal _updating_from_camera
        _updating_from_camera = True
        try:
            # Check if camera properties are initialized
            if loupe.canvas.camera.azimuth is not None:
                loupe.canvas.settings.setParameter("cameraAzimuth", round(loupe.canvas.camera.azimuth, 1), refresh=True)
            if loupe.canvas.camera.elevation is not None:
                loupe.canvas.settings.setParameter("cameraElevation", round(loupe.canvas.camera.elevation, 1), refresh=True)
            if loupe.canvas.camera.distance is not None:
                loupe.canvas.settings.setParameter("cameraDistance", round(loupe.canvas.camera.distance, 2), refresh=True)
        finally:
            _updating_from_camera = False

    # Store original onCameraChange and wrap it to sync settings
    original_onCameraChange = loupe.canvas.onCameraChange
    def onCameraChangeWithSync():
        original_onCameraChange()
        syncSettingsFromCamera()
    loupe.canvas.onCameraChange = onCameraChangeWithSync

    settings = loupe.settings
    settings.addParameters(
        **{
            "originCenterOfMass": [True, "updateGeometry"],
        }
    )
    settings.markAsPerDataset("originCenterOfMass")
    settings.addParameters(
        **{
            "cameraOrthographic": [
                False,
                updateOrthographicCamera,
                "onCameraChange",
            ],
            "cameraAzimuth": [45.0, updateCameraAzimuth, "onCameraChange"],
            "cameraElevation": [30.0, updateCameraElevation, "onCameraChange"],
            "cameraDistance": [25.0, updateCameraDistance, "onCameraChange"],
        }
    )

    # SETTINGS PANE
    pane = SettingsPane(UIHandler, loupe.settings, parent=loupe)

    pane.addSetting(
        "CheckBox",
        "Origin COM",
        settingsKey="originCenterOfMass",
        toolTip="Dynamically move the center of the camera to the centre of mass of the molecule",
    )
    pane.addSetting(
        "CheckBox",
        "Orthographic",
        settingsKey="cameraOrthographic",
        toolTip="Toggle parallel projection, useful for periodic systems",
    )

    # Add separator
    separator1 = QLabel("─" * 30)
    pane.layout.addWidget(separator1)

    # Manual camera angle controls
    angleLabel = QLabel("Manual View Angle:")
    angleLabel.setStyleSheet("font-weight: bold;")
    pane.layout.addWidget(angleLabel)

    pane.addSetting(
        "LineEdit",
        "Azimuth (°)",
        settingsKey="cameraAzimuth",
        isFloat=True,
        toolTip="Horizontal rotation angle in degrees (0-360)",
    )
    pane.addSetting(
        "LineEdit",
        "Elevation (°)",
        settingsKey="cameraElevation",
        isFloat=True,
        toolTip="Vertical rotation angle in degrees (-90 to 90)",
    )
    pane.addSetting(
        "LineEdit",
        "Distance",
        settingsKey="cameraDistance",
        isFloat=True,
        nMin=0.1,
        toolTip="Camera distance from center",
    )

    # Add separator
    separator2 = QLabel("─" * 30)
    pane.layout.addWidget(separator2)

    # Preset view buttons
    presetLabel = QLabel("Preset Views:")
    presetLabel.setStyleSheet("font-weight: bold;")
    pane.layout.addWidget(presetLabel)

    def setPresetView(azimuth, elevation):
        """Set camera to preset view."""
        loupe.canvas.settings.setParameter("cameraAzimuth", azimuth, refresh=True)
        loupe.canvas.settings.setParameter("cameraElevation", elevation, refresh=True)

    buttonContainer = Widget(parent=pane, layout="horizontal")

    topBtn = PushButton("Top", parent=buttonContainer)
    topBtn.setToolTip("View from top (azimuth=0°, elevation=90°)")
    topBtn.clicked.connect(lambda: setPresetView(0, 90))
    buttonContainer.layout.addWidget(topBtn)

    frontBtn = PushButton("Front", parent=buttonContainer)
    frontBtn.setToolTip("View from front (azimuth=0°, elevation=0°)")
    frontBtn.clicked.connect(lambda: setPresetView(0, 0))
    buttonContainer.layout.addWidget(frontBtn)

    sideBtn = PushButton("Side", parent=buttonContainer)
    sideBtn.setToolTip("View from side (azimuth=90°, elevation=0°)")
    sideBtn.clicked.connect(lambda: setPresetView(90, 0))
    buttonContainer.layout.addWidget(sideBtn)

    pane.layout.addWidget(buttonContainer)

    # Initialize camera to default values from settings
    def initializeCameraView():
        """Set camera to initial view angles from settings."""
        nonlocal _updating_from_camera
        _updating_from_camera = True
        try:
            loupe.canvas.camera.azimuth = settings.get("cameraAzimuth")
            loupe.canvas.camera.elevation = settings.get("cameraElevation")
            loupe.canvas.camera.distance = settings.get("cameraDistance")
        finally:
            _updating_from_camera = False

    # Set initial camera view
    initializeCameraView()

    # add pane
    loupe.addSidebarPane("CAMERA", pane)


CLIENT_FEATURES = [ClientFeature(widget_factory=loadLoupe)]
