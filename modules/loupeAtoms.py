import numpy as np
from config.atoms import atomColors, covalentRadii
from config.userConfig import getConfig
from UI.loupeProperties import VisualElement

DEPENDENCIES = []


class AtomsElement(VisualElement):

    pickingVisible = True
    colorProperty = None

    def __init__(self, *args, parent=None, **kwargs):
        from vispy import scene

        self.scatter = scene.visuals.Markers(
            scaling=True,
            spherical=True,
            parent=parent,
            light_color=(0, 0, 0),
            light_ambient=1,
            antialias=0,
        )
        super().__init__(*args, **kwargs, singleElement=None)  # NOTE: changed it to enable atoms disappearance!
        self.edge_width = 0.02
        # self.colors = (1,1,1)

    def onDatasetInit(self):
        dataset = self.canvas.dataset

        if hasattr(dataset, 'isVariable') and dataset.isVariable:
            # For variable datasets, colors/sizes will be computed per-geometry
            self.elementColors = None
            self.sizes = None
        else:
            # For uniform datasets, cache colors and sizes
            z = dataset.getElements()
            self.elementColors = (
                atomColors[z] / 255 * getConfig("loupeAtomColorDimming")
            )
            self.sizes = covalentRadii[z]

    def onNewGeometry(self):
        R = self.canvas.getCurrentR()
        dataset = self.canvas.dataset

        atoms_only_forces = self.canvas.settings.get("forceVectorsOnly")
        if atoms_only_forces:
            self.scatter.visible = False
        else:
            self.scatter.visible = True

        # For variable datasets, recompute colors and sizes per-geometry
        if hasattr(dataset, 'isVariable') and dataset.isVariable:
            z = dataset.getElements(self.canvas.index)
            self.elementColors = (
                atomColors[z] / 255 * getConfig("loupeAtomColorDimming")
            )
            self.sizes = covalentRadii[z]

        self.pos = R
        self.queueVisualRefresh()

    def getColors(self, picking, pickingColors):
        if picking is True:
            return pickingColors

        setting = self.canvas.settings.get("atomColorType")
        if setting == "Elements":
            return self.elementColors

        elif self.colorProperty is not None:
            return self.colorProperty.get("colors")

        else:
            return None

    def _draw(self, picking=False, pickingColors=None):

        colors = self.getColors(picking, pickingColors)

        # Apply size scale from settings
        scale = self.canvas.settings.get("atomSizeScale", 1.0)
        scaled_sizes = self.sizes * scale

        self.scatter.set_data(
            self.pos,
            face_color=colors,
            size=scaled_sizes,
            edge_width=0 if picking else self.edge_width,
            edge_color=getConfig("loupeBondsColor"),
        )
        if picking:
            self.scatter.update_gl_state(blend=False)
        else:
            self.scatter.update_gl_state(blend=True)


class AtomsHoverElement(VisualElement):
    def __init__(self, *args, parent=None, **kwargs):
        from vispy import scene

        self.scatter = scene.visuals.Markers(
            scaling=True,
            parent=parent,
            light_color=(0, 0, 0),
            light_ambient=1,
            antialias=1,
        )
        super().__init__(*args, **kwargs, singleElement=self.scatter)

    def onDatasetInit(self):
        dataset = self.canvas.dataset

        if hasattr(dataset, 'isVariable') and dataset.isVariable:
            # For variable datasets, sizes will be computed per-geometry
            self.sizes = None
        else:
            # For uniform datasets, cache sizes
            z = dataset.getElements()
            self.sizes = covalentRadii[z]

    def onNewGeometry(self):
        R = self.canvas.getCurrentR()
        dataset = self.canvas.dataset

        # For variable datasets, recompute sizes per-geometry
        if hasattr(dataset, 'isVariable') and dataset.isVariable:
            z = dataset.getElements(self.canvas.index)
            self.sizes = covalentRadii[z]

        self.pos = R
        self.queueVisualRefresh()

    def _draw(self, picking=False, pickingColors=None):

        hover, _ = self.canvas.getHoveredAtom(), self.canvas.getSelectedAtoms()

        if hover is not None:
            pos = np.array([self.pos[hover]])
            size = self.sizes[hover]
            scale = self.canvas.settings.get("atomSizeScale", 1.0)
            if not self.scatter.visible:
                self.scatter.visible = True

        else:
            self.scatter.visible = False
            return

        self.scatter.set_data(
            pos,
            size=10*size*scale,
            edge_width=0.12,
            edge_color=getConfig("loupeHoverColor"),
            face_color="#00000000",
        )


class AtomsSelectedElement(VisualElement):
    def __init__(self, *args, parent=None, **kwargs):
        from vispy import scene

        self.scatter = scene.visuals.Markers(
            scaling=True,
            parent=parent,
            light_color=(0, 0, 0),
            light_ambient=1,
            antialias=1,
        )
        super().__init__(*args, **kwargs, singleElement=self.scatter)

    def onDatasetInit(self):
        dataset = self.canvas.dataset

        if hasattr(dataset, 'isVariable') and dataset.isVariable:
            # For variable datasets, sizes will be computed per-geometry
            self.sizes = None
        else:
            # For uniform datasets, cache sizes
            z = dataset.getElements()
            self.sizes = covalentRadii[z]

    def onNewGeometry(self):
        R = self.canvas.getCurrentR()
        dataset = self.canvas.dataset

        # For variable datasets, recompute sizes per-geometry
        if hasattr(dataset, 'isVariable') and dataset.isVariable:
            z = dataset.getElements(self.canvas.index)
            self.sizes = covalentRadii[z]

        self.pos = R
        self.queueVisualRefresh()

    def _draw(self, picking=False, pickingColors=None):

        _, selected = (
            self.canvas.getHoveredAtom(),
            self.canvas.getSelectedAtoms(),
        )

        if selected is not None:
            n = len(self.pos)
            selected = [i for i in selected if i < n]
            if not selected:
                self.scatter.visible = False
                return
            pos = self.pos[selected]
            size = self.sizes[selected]
            scale = self.canvas.settings.get("atomSizeScale", 1.0)
            if not self.scatter.visible:
                self.scatter.visible = True

        else:
            self.scatter.visible = False
            return

        self.scatter.set_data(
            pos,
            size=10*size*scale,
            edge_width=0.12,
            edge_color=getConfig("loupeSelectColor"),
            face_color="#00000000",
        )


def addAtomsObject(UIHandler, loupe):
    loupe.addVisualElement(AtomsElement, "AtomsElement")
    loupe.addVisualElement(AtomsHoverElement, "AtomsHoverElement")
    loupe.addVisualElement(AtomsSelectedElement, "AtomsSelectedElement")


def addSettings(UIHandler, loupe):
    from functools import partial

    def updateAtomSize(loupe):
        """Update atom size scale."""
        atomsElement = loupe.canvas.elements.get("AtomsElement")
        if atomsElement:
            atomsElement.queueVisualRefresh()

    settings = loupe.settings
    settings.addAction("updateAtomSize", partial(updateAtomSize, loupe))
    settings.addParameters(**{
        "atomColorType": ["Elements", "updateGeometry"],
        "atomSizeScale": [1.0, "updateAtomSize", "visualRefresh"],
    })


def addSettingsPane(UIHandler, loupe):
    from UI.Templates import SettingsPane

    pane = SettingsPane(UIHandler, loupe.settings, parent=loupe)

    pane.addSetting(
        "ComboBox",
        f"Coloring",
        settingsKey=f"atomColorType",
        items=["Elements"],
        labelWidth=60,
    )
    loupe.addSidebarPane("ATOMS", pane)


def loadLoupe(UIHandler, loupe):

    addSettings(UIHandler, loupe)  # also sets the bonds property
    addAtomsObject(UIHandler, loupe)
    addSettingsPane(UIHandler, loupe)
