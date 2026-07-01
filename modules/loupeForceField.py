import numpy as np
import logging
from functools import partial
from UI.loupeProperties import VisualElement, AtomSelectionBase

logger = logging.getLogger("FFAST")
DEPENDENCIES = ["loupeAtoms"]

_SHAFT_RADIUS = 0.05
_HEAD_RADIUS = 0.12
_HEAD_LENGTH = 0.25   # absolute world-unit cone height, constant regardless of force magnitude
_N_SEGMENTS = 8

def _batch_rotation_z_to(U):
    """(N,3) unit vectors → (N,3,3) rotation matrices mapping +z to each U[i]."""
    N = len(U)
    R = np.tile(np.eye(3, dtype=float), (N, 1, 1))

    parallel = np.abs(U[:, 2]) > 0.9999
    antipar = parallel & (U[:, 2] < 0)
    R[antipar, 1, 1] = -1.0
    R[antipar, 2, 2] = -1.0

    sel = ~parallel
    if not np.any(sel):
        return R

    u = U[sel]
    z = np.zeros_like(u)
    z[:, 2] = 1.0
    axis = np.cross(z, u)
    axis /= np.linalg.norm(axis, axis=1, keepdims=True)

    c = u[:, 2, np.newaxis, np.newaxis]
    s = np.sqrt(np.maximum(0.0, 1.0 - c ** 2))

    kx, ky, kz = axis[:, 0], axis[:, 1], axis[:, 2]
    M = len(u)
    K = np.zeros((M, 3, 3))
    K[:, 0, 1] = -kz
    K[:, 0, 2] = ky
    K[:, 1, 0] = kz
    K[:, 1, 2] = -kx
    K[:, 2, 0] = -ky
    K[:, 2, 1] = kx

    I = np.tile(np.eye(3, dtype=float), (M, 1, 1))
    KK = np.einsum("nij,njk->nik", K, K)
    R[sel] = I + s * K + (1.0 - c) * KK
    return R


def _build_arrow_mesh(starts, ends, arrow_colors=None):
    """Batched cylinder+cone mesh.
    Returns (vertices (V,3), faces (F,3), unit_dirs (N,3)) or (None, None, None).
    """
    n = _N_SEGMENTS
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    cos_a, sin_a = np.cos(angles), np.sin(angles)
    js = np.arange(n)
    js1 = (js + 1) % n

    # Canonical shaft side: z in [0, 1], scaled per-arrow by shaft_length
    shaft_can = np.vstack([
        np.c_[_SHAFT_RADIUS * cos_a, _SHAFT_RADIUS * sin_a, np.zeros(n)],  # bot ring
        np.c_[_SHAFT_RADIUS * cos_a, _SHAFT_RADIUS * sin_a, np.ones(n)],   # top ring
    ])  # (2n, 3)
    shaft_faces = np.vstack([
        np.c_[js, js1, n + js],
        np.c_[js1, n + js1, n + js],
    ])  # (2n, 3)

    # Canonical cone side: z in [0, 1], always scaled by _HEAD_LENGTH
    cone_can = np.vstack([
        np.c_[_HEAD_RADIUS * cos_a, _HEAD_RADIUS * sin_a, np.zeros(n)],  # base ring
        [[0.0, 0.0, 1.0]],                                                # apex
    ])  # (n+1, 3)
    cone_faces = np.c_[js, js1, np.full(n, n)]  # (n, 3)

    # Canonical caps: center + ring, all at z=0 (no z-scaling needed)
    shaft_cap_can = np.vstack([[[0., 0., 0.]],
                                np.c_[_SHAFT_RADIUS * cos_a, _SHAFT_RADIUS * sin_a, np.zeros(n)]])  # (n+1, 3)
    cone_cap_can  = np.vstack([[[0., 0., 0.]],
                                np.c_[_HEAD_RADIUS  * cos_a, _HEAD_RADIUS  * sin_a, np.zeros(n)]])  # (n+1, 3)
    # winding: [center, j1+1, j+1] → outward normal faces -z
    cap_faces = np.c_[np.zeros(n, int), js1 + 1, js + 1]  # (n, 3)

    D = ends - starts
    lengths = np.linalg.norm(D, axis=1)
    mask = lengths > 1e-10
    if not np.any(mask):
        return None, None, None

    S = starts[mask]
    D = D[mask]
    L = lengths[mask]
    N = len(S)
    U = D / L[:, None]
    R = _batch_rotation_z_to(U)

    shaft_L = np.maximum(0.0, L - _HEAD_LENGTH)  # only shaft length varies
    cone_starts = S + U * shaft_L[:, None]        # cone placed at shaft tip

    def _transform(can, scale_z, origin):
        """Tile canonical verts, scale z, rotate, translate. Returns (N, V, 3)."""
        v = np.tile(can, (N, 1, 1))
        if scale_z is not None:
            v[:, :, 2] *= scale_z[:, None]
        return np.einsum("nij,nkj->nki", R, v) + origin[:, None, :]

    # Caps displaced by tiny epsilon outward (-U direction) so they're
    # geometrically in front of the shaft/cone side faces — polygon offset
    # alone can't guarantee this since flat caps have DZ=0 but slanted sides don't
    _CAP_BIAS = 0.002
    sv  = _transform(shaft_can,     shaft_L,  S)                            # (N, 2n,   3)
    cv  = _transform(cone_can,      np.full(N, _HEAD_LENGTH), cone_starts)  # (N, n+1,  3)
    bsv = _transform(shaft_cap_can, None,     S           - _CAP_BIAS * U)  # shaft bottom cap
    bcv = _transform(cone_cap_can,  None,     cone_starts - _CAP_BIAS * U)  # cone base cap

    all_verts = np.vstack([sv.reshape(-1, 3), bsv.reshape(-1, 3),
                           cv.reshape(-1, 3), bcv.reshape(-1, 3)])

    # Face index offsets per section (all-arrows-of-one-type layout)
    i = np.arange(N)
    s_off  = (i * 2 * n)                    [:, None, None]
    bs_off = (N * 2*n       + i * (n + 1))  [:, None, None]
    c_off  = (N * (3*n + 1) + i * (n + 1))  [:, None, None]
    bc_off = (N * (4*n + 2) + i * (n + 1))  [:, None, None]

    all_faces = np.vstack([
        (shaft_faces[None] + s_off ).reshape(-1, 3),  # N*2n  — shaft side
        (cap_faces[None]   + bs_off).reshape(-1, 3),  # N*n   — shaft bottom cap
        (cone_faces[None]  + c_off ).reshape(-1, 3),  # N*n   — cone side
        (cap_faces[None]   + bc_off).reshape(-1, 3),  # N*n   — cone base cap
    ])

    vertex_colors = None
    if arrow_colors is not None:
        verts_per_arrow = (2 * n) + (n + 1) + (n + 1) + (n + 1)  # shaft + shaft cap + cone + cone cap
        rgba = np.ones((N, 4), dtype=float)

        rgba[:, :arrow_colors.shape[1]] = arrow_colors
        shaft_colors = np.repeat(rgba, 2 * n, axis=0)  # matches sv.reshape(-1, 3)
        shaft_cap_colors = np.repeat(rgba, n + 1, axis=0)  # matches bsv.reshape(-1, 3)
        cone_colors = np.repeat(rgba, n + 1, axis=0)  # matches cv.reshape(-1, 3)
        cone_cap_colors = np.repeat(rgba, n + 1, axis=0)  # matches bcv.reshape(-1, 3)

        vertex_colors = np.vstack([
            shaft_colors,
            shaft_cap_colors,
            cone_colors,
            cone_cap_colors,
        ])

    return all_verts, all_faces, vertex_colors


class ForceVectorSelect(AtomSelectionBase):
    """Selection tool for choosing which atoms show force vectors."""

    multiselect = 10000
    rectangleSelect = True
    label = "Force Vector Atoms"

    def __init__(self, canvas, **kwargs):
        super().__init__(canvas, **kwargs)
        # Restore persisted selection for this dataset
        indices = canvas.settings.get("forceVectorsAtomIndices")
        if indices:
            self.selectedPoints = list(indices)
            canvas.visualRefresh(force=True)

    def selectCallback(self):
        self.canvas.loupe.settings.setParameter(
            "forceVectorsAtomIndices",
            list(self.selectedPoints),
            refresh=True,
        )


class ForceVectorsElement(VisualElement):
    _starts = None
    _ends = None

    def __init__(self, *args, parent=None, **kwargs):
        from vispy import scene

        self.mesh = scene.visuals.Mesh(
            vertices=np.zeros((3, 3)),
            faces=np.array([[0, 1, 2]]),
            parent=parent,
            color=(1.0, 1.0, 1.0, 1.0),
            shading="smooth",
        )
        self.mesh.set_gl_state(
            depth_test=True,
        )
        # Boost ambient so back-facing surfaces stay near-white instead of dark grey.
        # Default ambient is ~0.1; raising to 0.7 keeps smooth-shading 3D cues
        # while preventing dark patches.
        if hasattr(self.mesh, "shading_filter"):
            self.mesh.shading_filter.ambient_light = (0.7, 0.7, 0.7, 1.0)
        super().__init__(*args, **kwargs, singleElement=None)
        self._arrow_colors = None

    def show(self):
        self.hidden = False
        self.mesh.visible = True

    def hide(self):
        self.hidden = True
        self.mesh.visible = False

    def onNewGeometry(self):
        self.update()

    def _get_forces(self):
        """Return (N_atoms, 3) forces for current frame, or None if unavailable."""
        settings = self.canvas.settings
        model_key = settings.get("forceVectorsModelKey")
        dataset = self.canvas.dataset
        window = settings.get("forceVectorsAvgWindow")
        index = self.canvas.index

        if model_key is not None:
            env = self.canvas.loupe.env
            model = env.getModel(model_key)
            if model is None:
                return None, "no_prediction"
            data = env.getData("forces", model=model, dataset=dataset)
            if data is None:
                return None, "no_prediction"
            forces_all = data.get("forces")
            if forces_all is None:
                return None, "no_prediction"
            if window > 0:
                n = dataset.getN()
                indices = np.arange(-window, window + 1) + index
                indices = indices[(indices >= 0) & (indices < n)]
                if forces_all.ndim == 3:
                    F = np.mean(forces_all[indices], axis=0)
                else:
                    F = forces_all
            else:
                if forces_all.ndim == 3:
                    F = forces_all[index]
                else:
                    F = forces_all
            return F, None

        if window > 0:
            n = dataset.getN()
            indices = np.arange(-window, window + 1) + index
            indices = indices[(indices >= 0) & (indices < n)]
            F = dataset.getForces(indices=indices)
            F = np.mean(F, axis=0)
        else:
            F = dataset.getForces(indices=index)
        return F, None

    def update(self):
        settings = self.canvas.settings
        show = settings.get("showForceVectors")
        status_label = getattr(self.canvas.loupe, "_forceVectorsStatusLabel", None)
        only_forces = self.canvas.settings.get("forceVectorsOnly")

        if not show:
            self.hide()
            if status_label:
                status_label.setVisible(False)
            return

        self.show()

        F, err = self._get_forces()

        atoms_element = self.canvas.elements.get("AtomsElement")
        arrow_colors = None
        if atoms_element is not None:
            arrow_colors = np.ones_like(atoms_element.elementColors)
            if only_forces:
                arrow_colors = atoms_element.getColors(False, None)
                if arrow_colors is not None:
                    arrow_colors = np.asarray(arrow_colors)

        if err == "no_prediction":
            self._starts = None
            self._ends = None
            if status_label:
                status_label.setText("No predictions computed for this model")
                status_label.setVisible(True)
            self.queueVisualRefresh()
            return

        if status_label:
            status_label.setVisible(False)

        lengthFactor = settings.get("forceVectorsLength")
        normalised = settings.get("forceVectorsNormalised")
        R = self.canvas.getCurrentR()

        for vOrM in self.canvas.currentTransformations:
            if vOrM.ndim == 2:
                F = F @ vOrM

        if normalised:
            normF = F / np.max(np.linalg.norm(F, axis=1)) * lengthFactor / 5
        else:
            normF = F * lengthFactor / 500

        # --- atom filter ---
        filter_enabled = settings.get("forceVectorsFilterEnabled")
        if filter_enabled:
            atom_indices = settings.get("forceVectorsAtomIndices")
            if not atom_indices:
                # Empty selection → show nothing
                self._starts = None
                self._ends = None
                self.queueVisualRefresh()
                return
            n_atoms = len(R)
            if max(atom_indices) >= n_atoms:
                # Variable-size dataset: selection invalid for this frame → show nothing
                self._starts = None
                self._ends = None
                self.queueVisualRefresh()
                return
            idx = np.array(atom_indices)
            R = R[idx]
            normF = normF[idx]
            if arrow_colors is not None:
                arrow_colors = arrow_colors[idx]

        self._starts = R
        self._ends = R + normF
        self._arrow_colors = arrow_colors
        self.queueVisualRefresh()

    def _draw(self, picking=False, **_):
        if picking:
            self.mesh.visible = False  # hide from picking pass; white mesh encodes as idx 65535
            return

        show = self.canvas.settings.get("showForceVectors")

        if self._starts is None or not show:
            self.hide()
            return

        self.show()
        verts, faces, vertex_colors = _build_arrow_mesh(self._starts, self._ends, self._arrow_colors)

        if verts is None:
            self.hide()
            return

        self.mesh.set_data(vertices=verts, faces=faces, vertex_colors=vertex_colors)


def loadLoupe(UIHandler, loupe):
    from UI.Templates import SettingsPane, ObjectComboBox, PushButton
    from PySide6.QtWidgets import QLabel, QHBoxLayout, QWidget

    loupe.addVisualElement(ForceVectorsElement, "ForceVectorsElement")

    settings = loupe.settings

    def _on_atom_indices_changed(loupe):
        """Sync running ForceVectorSelect tool when settings restore on dataset switch."""
        canvas = loupe.canvas
        if not canvas.isActiveAtomSelectTool(ForceVectorSelect):
            return
        tool = canvas.activeAtomSelectTool
        indices = canvas.settings.get("forceVectorsAtomIndices")
        if indices != tool.selectedPoints:
            tool.selectedPoints = list(indices)
            canvas.visualRefresh(force=True)

    settings.addParameters(
        **{
            "showForceVectors": [False, "updateGeometry"],
            "forceVectorsOnly": [False, "updateGeometry"],
            "forceVectorsModelKey": [None, "updateGeometry"],
            "forceVectorsLength": [10, "updateGeometry"],
            "forceVectorsAvgWindow": [0, "updateGeometry"],
            "forceVectorsNormalised": [True, "updateGeometry"],
            "forceVectorsFilterEnabled": [False, "updateGeometry"],
            "forceVectorsAtomIndices": [
                [],
                partial(_on_atom_indices_changed, loupe),
                "updateGeometry",
            ],
        }
    )
    settings.markAsPerDataset("forceVectorsModelKey")
    settings.markAsPerDataset("forceVectorsAtomIndices")

    # SETTINGS PANE
    pane = SettingsPane(UIHandler, loupe.settings, parent=loupe)
    loupe.addSidebarPane("FORCE VECTORS", pane)

    pane.addSetting(
        "CheckBox",
        "Enable",
        settingsKey="showForceVectors",
        toolTip="Overlay force vectors on each atom as 3D arrows.",
    )

    pane.addSetting(
        "CheckBox",
        "Forces only",
        settingsKey="forceVectorsOnly",
        toolTip=(
            "Hide atoms and show only force arrows. "
            "Arrows inherit the color of the atoms they originate from."
        ),
    )

    # SOURCE SELECTOR
    class _ForceSourceComboBox(ObjectComboBox):
        def updateList(self, *args):
            self.currentlyUpdatingList = True
            model_keys = self.env.getAllModelKeys()
            self.currentKeyList = [None] + model_keys
            self.clear()
            self.addItems(
                ["Ground Truth"]
                + [self.env.getModelOrDataset(k).getDisplayName() for k in model_keys]
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

    sourceCombo = _ForceSourceComboBox(UIHandler, hasDatasets=False)
    sourceCombo.setOnIndexChanged(
        lambda key: settings.setParameter("forceVectorsModelKey", key)
    )
    pane.layout.addWidget(sourceCombo)

    # STATUS LABEL (shown when no predictions available)
    statusLabel = QLabel("No predictions computed for this model")
    statusLabel.setWordWrap(True)
    statusLabel.setStyleSheet("color: orange;")
    statusLabel.setVisible(False)
    pane.layout.addWidget(statusLabel)
    loupe._forceVectorsStatusLabel = statusLabel

    pane.addSetting(
        "Slider",
        "Length",
        settingsKey="forceVectorsLength",
        toolTip="Scale factor for arrow length. Higher = longer arrows relative to bond lengths.",
        nMin=1,
        nMax=500,
    )
    pane.addSetting(
        "Slider",
        "Avg. window",
        settingsKey="forceVectorsAvgWindow",
        toolTip="Temporal smoothing: average forces over ±N frames around the current frame. 0 = no smoothing.",
        nMin=0,
        nMax=10000,
    )
    pane.addSetting(
        "CheckBox",
        "Normalised",
        settingsKey="forceVectorsNormalised",
        toolTip=(
            "Rescale arrows so the largest force in each frame has a fixed length. "
            "Useful for comparing directions when magnitudes vary widely."
        ),
    )

    # ATOM FILTER SECTION
    pane.addSetting(
        "CheckBox",
        "Filter to selection",
        settingsKey="forceVectorsFilterEnabled",
        toolTip=(
            "Show force arrows only for the selected atom subset. "
            "Useful for large systems where rendering all arrows is slow. "
            "On variable-size datasets, arrows hide on frames where the "
            "selection is out of range."
        ),
    )

    # Select / Clear buttons in a horizontal row
    filterRow = QWidget()
    filterRowLayout = QHBoxLayout(filterRow)
    filterRowLayout.setContentsMargins(0, 0, 0, 0)

    selectBtn = PushButton("Select atoms")
    selectBtn.setToolTip(
        "Enter atom-picking mode to build the force vector subset. "
        "Click atoms to toggle them; hold Ctrl and drag to box-select a region. "
        "Click again to exit picking mode."
    )
    selectBtn.clicked.connect(
        lambda: loupe.setActiveAtomSelectTool(ForceVectorSelect)
    )

    clearBtn = PushButton("Clear")
    clearBtn.setToolTip("Remove all atoms from the force vector subset.")

    def _clear_selection():
        settings.setParameter("forceVectorsAtomIndices", [], refresh=True)
        canvas = loupe.canvas
        if canvas.isActiveAtomSelectTool(ForceVectorSelect):
            canvas.activeAtomSelectTool.selectedPoints = []
            canvas.visualRefresh(force=True)

    clearBtn.clicked.connect(_clear_selection)

    filterRowLayout.addWidget(selectBtn)
    filterRowLayout.addWidget(clearBtn)
    pane.layout.addWidget(filterRow)
