from UI.Templates import Widget, ContentBar, SettingsPane, ToolButton
from UI.loupeProperties import VisualElement
from config.userConfig import getConfig
from vispy import scene
from vispy.scene.cameras.turntable import TurntableCamera
from vispy.util import keys
from ffast.renderers.vispy.adapter import VispySceneAdapter
from PySide6 import QtWidgets
import numpy as np


class RectangleSelection(VisualElement):
    def __init__(self, *args, parent=None, **kwargs):
        self.rectangle = scene.visuals.Rectangle(
            center=[0, 0],
            color=(0.5, 0.5, 1, 0.3),
            height=1,
            width=1,
            parent=parent,
            border_width=2,
            border_color=(0.5, 0.5, 1, 0.8),
        )
        self.rectangle.pos = np.array([[0, 0], [1, 0], [1, 1], [0, 1]])
        super().__init__(*args, **kwargs, singleElement=self.rectangle)

    def setPosition(self, arr):
        self.rectangle.pos = arr


class SideBar(ContentBar):
    def __init__(self, handler, **kwargs):
        super().__init__(handler, **kwargs)
        self.handler = handler
        self.setupContent()

    def setupContent(self):
        pass


class SceneCanvas(scene.SceneCanvas):

    mouseoverActive = False
    mouseClickActive = False
    rectangleSelectActive = False
    widget = None
    isCtrlDragging = False
    draggingStart = [0, 0]

    def __init__(self, widget, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.widget = widget

    def on_mouse_press(self, event):
        if event.button != 1:
            return
        if keys.CONTROL in event.modifiers:
            self.isCtrlDragging = True
            self.draggingStart = event.pos
        elif self.mouseClickActive:
            # Atom select tool active: ray-cast → tool, not server selection.
            displayed = self.widget.sceneAdapter.pick_at(event.pos, radius=self.widget._pickRadius())
            atom_id = self.widget.sceneAdapter.displayed_to_atom_id(displayed) if displayed is not None else None
            self.widget.addSelectedAtom(atom_id, refresh=True)
        # else: plain click selects nothing — atom selection requires an
        # intentionally-activated select tool. Camera handles the click.

    def on_mouse_release(self, event):
        wasCtrlDragging = self.isCtrlDragging
        self.isCtrlDragging = False
        if event.button != 1:
            return
        if wasCtrlDragging:
            pos0, pos1 = self.draggingStart, event.pos
            self.draggingStart = np.array([0, 0])
            if np.linalg.norm(np.asarray(pos1, float) - np.asarray(pos0, float)) > 3.0:
                idxs = self.widget.sceneAdapter.pick_in_rect(pos0, pos1)
                if self.mouseClickActive and self.rectangleSelectActive:
                    atom_ids = [
                        self.widget.sceneAdapter.displayed_to_atom_id(i)
                        for i in idxs
                    ]
                    self.widget.addSelectedAtoms(
                        [a for a in atom_ids if a is not None], refresh=True
                    )
                else:
                    self.widget.loupe.onAdapterPickRect(idxs)
            self.widget.hideSelectionRectangle()

    def on_mouse_move(self, event):
        if self.isCtrlDragging:
            self.widget.setSelectionRectanglePos(self.draggingStart, event.pos)
        elif not event.is_dragging:
            displayed = self.widget.sceneAdapter.pick_at(event.pos, radius=self.widget._pickRadius())
            self.widget.sceneAdapter.set_transient_highlight(displayed)
            if self.mouseClickActive:
                atom_id = self.widget.sceneAdapter.displayed_to_atom_id(displayed) if displayed is not None else None
                self.widget.setHoveredPoint(atom_id, refresh=False)
            self.widget.canvas.update()

    def on_resize(self, *args):
        scene.SceneCanvas.on_resize(self, *args)


class Camera(TurntableCamera):

    parentCanvas = None

    def __init__(self, parentCanvas):
        self.parentCanvas = parentCanvas
        super().__init__()

    def view_changed(self):
        self.parentCanvas.onCameraChange()
        return TurntableCamera.view_changed(self)


class InteractiveCanvas(Widget):
    activeAtomSelectTool = None
    hoveredPoint = None
    nAtoms = -1
    hasBeenInited = False
    dataset = None
    _colorbar = None  # vispy ColorBarWidget for value-driven coloring (ADR 0016)

    def __init__(self, loupe, **kwargs):
        super().__init__(layout="vertical", **kwargs)

        self.canvas = SceneCanvas(
            self, bgcolor=getConfig("loupeBGColor"), create_native=False
        )

        self.elements = {}
        self.props = {}

        self.canvas.create_native()
        self.view = self.canvas.central_widget.add_view()
        self.view.camera = Camera(self)
        self.camera = self.view.camera

        self.grid = self.newGrid()

        self.scene = self.view.scene
        self.loupe = loupe
        self.canvas.native.setParent(loupe)
        self.layout.addWidget(self.canvas.native)
        self.addAtomSelectToolbar()
        self.setActiveAtomSelectTool(None)

        self.createSelectionRectangle()

        self.sceneAdapter = VispySceneAdapter(parent=self.scene)

        self.freeze()

    ## VISUAL ELEMENTS & PROPERTIES

    def newGrid(self):
        return self.canvas.central_widget.add_grid(margin=4)

    def addVisualElement(self, Element, name, viewParent=False):
        if viewParent:
            el = Element(parent=self.view)
        else:
            el = Element(parent=self.scene)
        el.canvas = self
        self.elements[name] = el
        return el

    def visualRefresh(self, force=False):
        pass

    # ADAPTER STYLING (ADR 0014): geometry is server-owned, but how it is
    # painted is client-local. Push the legacy Loupe look (atom outline, bond
    # color/width) so the adapter render matches the VisualElement path.
    def _scaledBondWidth(self):
        """Bond width scaled by camera distance, mirroring legacy BondsElement."""
        try:
            width = self.settings.get("bondWidth", getConfig("loupeBondsWidth", 25))
        except Exception:
            width = getConfig("loupeBondsWidth", 25)
        dist = getattr(self.camera, "distance", None) or 1
        return float(width) / float(dist)

    def _pushAdapterStyle(self):
        bondsColor = getConfig("loupeBondsColor")
        self.sceneAdapter.set_style(
            atom_edge_width=0.02,
            atom_edge_color=bondsColor,
            bond_color=self.settings.get("bondColor", bondsColor),
            bond_width=self._scaledBondWidth(),
        )

    # COLORBAR (ADR 0016): client draws the legend from the color descriptor.
    def updateColorbar(self, color_by):
        """Show/update or hide the colorbar from the active color descriptor."""
        if color_by is None:
            if self._colorbar is not None:
                self._colorbar.visible = False
            return
        if self._colorbar is None:
            self._initColorbar()
        self._colorbar.cmap = self.sceneAdapter._get_colormap(color_by.colormap)
        self._colorbar.clim = (f"{color_by.vmin:.2f}", f"{color_by.vmax:.2f}")
        label = color_by.label + (f" [{color_by.unit}]" if color_by.unit else "")
        self._colorbar.label.text = label
        self._colorbar.visible = True

    def _initColorbar(self):
        from vispy import scene as vscene
        self._colorbar = vscene.ColorBarWidget(
            cmap="viridis",
            label_color="lightgray",
            label="",
            clim=("0", "1"),
            orientation="right",
            border_color="lightgray",
            border_width=1,
        )
        self._colorbar.width_max = 70
        self.grid.add_widget(self._colorbar)

    # ADAPTER PICKING (ADR 0015)
    def _pickRadius(self):
        try:
            return float(self.loupe.settings.get("pickRadius"))
        except (TypeError, ValueError):
            return 12.0

    def adapterHover(self, pos):
        """Ray-cast hover → client-local transient highlight (not sent)."""
        idx = self.sceneAdapter.pick_at(pos, radius=self._pickRadius())
        self.sceneAdapter.set_transient_highlight(idx)
        self.canvas.update()

    def addProperty(self, Prop):
        prop = Prop()
        prop.canvas = self
        self.props[prop.key] = prop

    def addAtomSelectToolbar(self):
        self.atomSelectBar = Widget(
            color="@BGColor1", layout="horizontal", parent=self
        )
        self.atomSelectBar.setFixedHeight(40)
        self.layout.insertWidget(0, self.atomSelectBar)

        self.atomSelectBar.setContentsMargins(8, 0, 8, 0)

        self.atomSelectBar.label1 = QtWidgets.QLabel("/", parent=self.atomSelectBar)
        self.atomSelectBar.label2 = QtWidgets.QLabel("/", parent=self.atomSelectBar)
        self.atomSelectBar.cancelButton = ToolButton(
            lambda x: self.setActiveAtomSelectTool(), "close", parent=self.atomSelectBar
        )

        self.atomSelectBar.layout.addWidget(self.atomSelectBar.label1)
        self.atomSelectBar.layout.addWidget(self.atomSelectBar.label2)
        self.atomSelectBar.layout.addWidget(self.atomSelectBar.cancelButton)

    ## INIT

    def setDataset(self, dataset):
        self.hasBeenInited = False
        self.dataset = dataset
        self.nAtoms = dataset.getNAtoms() if not getattr(dataset, "isVariable", False) else dataset.getNAtoms(getattr(self, "index", 0))

        for prop in self.props.values():
            prop.onDatasetInit()

        for element in self.elements.values():
            if not element.disabled:
                element.onDatasetInit()

        self.hasBeenInited = True

    def size(self):
        return self.canvas.size

    ## GEOMETRY

    def getR(self, index=None):
        return self.dataset.getCoordinates(indices=index)

    def getCurrentR(self):
        # Stage 4c: read the current frame's positions from the rendered scene
        # (server-owned, already on the client) instead of the dataset, so
        # info-select distance/angle readouts work without triggering a lazy
        # array fetch on the Qt main thread. Rigid view transforms (align/center)
        # preserve distances/angles. Fall back to the dataset if no scene yet.
        adapter = getattr(self, "sceneAdapter", None)
        pos = getattr(adapter, "_atom_positions", None) if adapter is not None else None
        if pos is not None:
            return np.asarray(pos)
        return self.getR(self.index)

    def setIndex(self, index):
        if self.dataset is None:
            return
        self.index = min(index, self.dataset.getN() - 1)
        self.onNewGeometry()

    def onNewGeometry(self):
        if not self.hasBeenInited:
            return

        for prop in self.props.values():
            prop.onNewGeometry()

        for element in self.elements.values():
            if not element.disabled:
                element.onNewGeometry()

        if self.activeAtomSelectTool is not None:
            self.activeAtomSelectTool.updateInfo()

    ## CAMERA
    def onCameraChange(self):
        for prop in self.props.values():
            prop.onCameraChange()

        for element in self.elements.values():
            element.onCameraChange()

        if hasattr(self, "sceneAdapter"):
            self.sceneAdapter.set_style(bond_width=self._scaledBondWidth())

    ## PICKING
    def setHoveredPoint(self, index, refresh=True):
        if self.activeAtomSelectTool is not None:
            self.activeAtomSelectTool.hoverAtom(index)
        if refresh:
            self.visualRefresh(force=True)

    def addSelectedAtom(self, index, refresh=True):
        if self.activeAtomSelectTool is not None:
            self.activeAtomSelectTool.selectAtom(index)
        if refresh:
            self.visualRefresh(force=True)

    def addSelectedAtoms(self, indices, refresh=True):
        if self.activeAtomSelectTool is not None:
            self.activeAtomSelectTool.selectAtoms(indices)
        if refresh:
            self.visualRefresh(force=True)

    def isActiveAtomSelectTool(self, tool):
        if tool is None:
            return self.activeAtomSelectTool is None
        return isinstance(self.activeAtomSelectTool, tool)

    def setActiveAtomSelectTool(self, tool=None):
        if (tool is not None) and self.isActiveAtomSelectTool(tool):
            self.setActiveAtomSelectTool(None)
            return

        if tool is None:
            self.activeAtomSelectTool = None
            self.canvas.mouseoverActive = False
            self.canvas.mouseClickActive = False
            self.canvas.rectangleSelectActive = False
            self.atomSelectBar.hide()
        else:
            self.activeAtomSelectTool = tool(self)
            self.canvas.mouseoverActive = True
            self.canvas.mouseClickActive = True
            self.canvas.rectangleSelectActive = (
                self.activeAtomSelectTool.rectangleSelect
            )
            self.atomSelectBar.show()

        self.onNewGeometry()

    def getSelectedAtoms(self):
        if self.activeAtomSelectTool is None:
            return None
        return self.activeAtomSelectTool.selectedPoints

    def getHoveredAtom(self):
        if self.activeAtomSelectTool is None:
            return None
        return self.activeAtomSelectTool.hoveredPoint

    def keyPressEvent(self, event):
        self.parent().keyPressEvent(event)

    ## SELECTION RECTANGLE
    def setSelectionRectanglePos(self, oldPos, newPos):
        if self.selectionRectangle.hidden:
            self.selectionRectangle.show()

        self.selectionRectangle.setPosition(
            np.array(
                [
                    [oldPos[0], oldPos[1]],
                    [newPos[0], oldPos[1]],
                    [newPos[0], newPos[1]],
                    [oldPos[0], newPos[1]],
                ]
            )
        )

    def createSelectionRectangle(self):
        self.selectionRectangle = self.addVisualElement(
            RectangleSelection, "SelectionRectangle", viewParent=True
        )

    def hideSelectionRectangle(self):
        self.selectionRectangle.hide()

    ## MISC
    def resizeEvent(self, event):
        self.onResize()
        return super(InteractiveCanvas, self).resizeEvent(event)

    def onResize(self):
        for prop in self.props.values():
            prop.onCanvasResize()

        for element in self.elements.values():
            element.onCanvasResize()
