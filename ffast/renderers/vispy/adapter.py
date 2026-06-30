"""Vispy renderer adapter for FFAST server-owned visualization.

VispySceneAdapter is the Qt/Vispy equivalent of the Three.js MoleculeRenderer
in ffast-viewer.js.  It receives renderer-neutral RenderScene data from the
server and translates it into Vispy scene.visuals objects attached to a
provided parent node.

Typical usage (inside a Loupe canvas or VisualizationView consumer):

    from ffast.renderers.vispy.adapter import VispySceneAdapter

    adapter = VispySceneAdapter(parent=canvas.view.scene)

    # On SCENE_SNAPSHOT:
    adapter.apply_snapshot(snapshot)

    # On SCENE_PATCH:
    adapter.apply_patch(patch)
"""
from __future__ import annotations

import logging

import numpy as np

from ffast.visualization.scene import AtomScene, BondScene, ForceScene, LabelScene, SelectionOverlay
from ffast.visualization.scene import ScenePatch, SceneSnapshot, UnitCellScene

logger = logging.getLogger(__name__)


class VispySceneAdapter:
    """Translate server RenderScene data into Vispy scene.visuals.

    All Vispy imports are deferred to first use so that importing this module
    does not require an active OpenGL context or Qt application.

    Parameters
    ----------
    parent:
        A Vispy scene node (e.g. ``canvas.view.scene``) that serves as the
        parent for the managed visuals.
    """

    def __init__(self, parent) -> None:
        self._parent = parent
        self._atom_markers = None     # scene.visuals.Markers
        self._bond_lines = None       # scene.visuals.Line
        self._unit_cell_lines = None  # scene.visuals.Line
        self._force_mesh = None       # scene.visuals.Mesh
        self._label_visual = None     # scene.visuals.Text
        self._selection_visuals: dict = {}  # name → scene.visuals.Markers
        self._hover_visual = None     # scene.visuals.Markers (client-local hover)
        # Cached from last _apply_atoms call; needed by _apply_selections and by
        # ray-cast picking (ADR 0015).
        self._atom_positions: np.ndarray | None = None
        self._atom_sizes: np.ndarray | None = None
        self._atom_face_colors: np.ndarray | None = None  # cached for restyle
        self._atom_ids: list | None = None
        # Active value-driven coloring descriptor (ADR 0016), or None for
        # element coloring; the Renderer Client reads it to draw the colorbar.
        self._color_by = None
        # Renderer-local styling (ADR 0014: how geometry is painted is client-
        # owned). Defaults approximate the legacy Loupe; the host client calls
        # set_style() to push exact user-config values so the look matches.
        self._atom_edge_width = 0.02
        self._atom_edge_color = "#404040"
        self._bond_width = 2.0
        self._bond_color = "#404040"

    # ── public API ────────────────────────────────────────────────────────────

    def apply_snapshot(self, snapshot: SceneSnapshot) -> None:
        """Replace the entire scene from a SceneSnapshot."""
        s = snapshot.scene
        self._apply_atoms(s.atoms)
        self._apply_bonds(s.bonds)
        self._apply_unit_cell(s.unit_cell)
        self._apply_forces(s.forces)
        self._apply_labels(s.labels)
        self._apply_selections(s.selections)

    def apply_patch(self, patch: ScenePatch) -> None:
        """Update only the scene components listed in patch.changed."""
        changed = patch.changed if isinstance(patch.changed, set) else set(patch.changed)
        if "atoms" in changed:
            self._apply_atoms(patch.atoms)
        if "bonds" in changed:
            self._apply_bonds(patch.bonds)
        if "unit_cell" in changed:
            self._apply_unit_cell(patch.unit_cell)
        if "forces" in changed:
            self._apply_forces(patch.forces)
        if "labels" in changed:
            self._apply_labels(patch.labels)
        if "selections" in changed:
            self._apply_selections(patch.selections)

    def clear(self) -> None:
        """Hide all managed visuals (scene is empty)."""
        for visual in (
            self._atom_markers,
            self._bond_lines,
            self._unit_cell_lines,
            self._force_mesh,
            self._label_visual,
            self._hover_visual,
        ):
            if visual is not None:
                visual.visible = False
        for vis in self._selection_visuals.values():
            vis.visible = False

    def set_style(self, *, atom_edge_width=None, atom_edge_color=None,
                  bond_width=None, bond_color=None) -> None:
        """Override renderer-local styling and re-apply it to live visuals.

        Geometry comes from the server scene; how it is painted (atom outline,
        bond color/width) is client-owned (ADR 0014). The host client pushes
        user-config values here so the adapter matches the legacy render path.
        Bond width is expected pre-scaled by the caller (e.g. by camera
        distance) — the adapter does not know about the camera.
        """
        if atom_edge_width is not None:
            self._atom_edge_width = float(atom_edge_width)
        if atom_edge_color is not None:
            self._atom_edge_color = atom_edge_color
        if bond_width is not None:
            self._bond_width = float(bond_width)
        if bond_color is not None:
            self._bond_color = bond_color

        if self._bond_lines is not None:
            self._bond_lines.set_data(
                color=self._bond_color, width=self._bond_width,
            )
        if (
            self._atom_markers is not None
            and self._atom_positions is not None
            and self._atom_face_colors is not None
        ):
            self._atom_markers.set_data(
                self._atom_positions,
                face_color=self._atom_face_colors,
                size=self._atom_sizes,
                edge_width=self._atom_edge_width,
                edge_color=self._atom_edge_color,
            )

    # ── atoms ─────────────────────────────────────────────────────────────────

    def _get_atom_visual(self):
        if self._atom_markers is None:
            from vispy import scene
            self._atom_markers = scene.visuals.Markers(
                scaling=True,
                spherical=True,
                parent=self._parent,
                light_color=(0, 0, 0),
                light_ambient=1,
                antialias=0,
            )
        return self._atom_markers

    def _apply_atoms(self, atoms: AtomScene | None) -> None:
        if atoms is None or len(atoms.positions) == 0:
            if self._atom_markers is not None:
                self._atom_markers.visible = False
            self._atom_positions = None
            self._atom_sizes = None
            self._atom_face_colors = None
            self._atom_ids = None
            self._color_by = None
            return
        pos = np.array(atoms.positions, dtype=np.float32)
        sizes = np.array(atoms.sizes, dtype=np.float32)
        colors = np.array(atoms.colors, dtype=np.float32)
        self._atom_positions = pos
        self._atom_sizes = sizes
        self._atom_ids = atoms.atom_ids
        self._color_by = atoms.color_by
        # Value-driven coloring (ADR 0016): map values→RGBA client-side; fall
        # back to the server's element colors on any failure.
        if atoms.color_by is not None:
            cb = atoms.color_by
            logger.info(
                "adapter: color_by present label=%r colormap=%r range=%.4g..%.4g n=%d",
                cb.label, cb.colormap, cb.vmin, cb.vmax, len(cb.values),
            )
            mapped = self._map_color_by(cb)
            if mapped is None:
                logger.warning("adapter: color_by mapping failed — using element colors")
            else:
                colors = mapped
        else:
            logger.debug("adapter: no color_by — element colors")
        self._atom_face_colors = colors
        v = self._get_atom_visual()
        v.visible = True
        v.set_data(
            pos, face_color=colors, size=sizes,
            edge_width=self._atom_edge_width, edge_color=self._atom_edge_color,
        )

    @staticmethod
    def _get_colormap(name: str):
        from vispy.color import Colormap, get_colormap
        if name == "force_error":
            return Colormap([
                (0.1, 0.1, 0.9), (0.1, 0.9, 0.1), (0.9, 0.9, 0.1),
                (0.5, 0.1, 0.1), (0.9, 0.1, 0.1),
            ])
        return get_colormap(name)

    def _map_color_by(self, color_by):
        """Map per-atom values → RGBA via the named colormap (ADR 0016).

        Returns None on failure so the caller falls back to element colors.
        """
        try:
            v = np.asarray(color_by.values, dtype=np.float32)
            lo, hi = float(color_by.vmin), float(color_by.vmax)
            norm = np.zeros_like(v) if hi <= lo else np.clip((v - lo) / (hi - lo), 0.0, 1.0)
            return np.asarray(self._get_colormap(color_by.colormap)[norm].rgba, dtype=np.float32)
        except Exception as exc:
            logger.debug("VispySceneAdapter: color mapping failed: %s", exc)
            return None

    # ── bonds ─────────────────────────────────────────────────────────────────

    def _get_bond_visual(self):
        if self._bond_lines is None:
            from vispy import scene
            self._bond_lines = scene.visuals.Line(
                parent=self._parent,
                color=self._bond_color,
                width=self._bond_width,
                connect="segments",
                antialias=True,
            )
        return self._bond_lines

    def _apply_bonds(self, bonds: BondScene | None) -> None:
        if bonds is None or len(bonds.segments) == 0:
            if self._bond_lines is not None:
                self._bond_lines.visible = False
            return
        segs = np.array(bonds.segments, dtype=np.float32)
        v = self._get_bond_visual()
        v.visible = True
        v.set_data(pos=segs)

    # ── unit cell ─────────────────────────────────────────────────────────────

    def _get_unit_cell_visual(self):
        if self._unit_cell_lines is None:
            from vispy import scene
            self._unit_cell_lines = scene.visuals.Line(
                parent=self._parent,
                color=(0.5, 0.5, 0.5, 0.8),
                width=2,
                connect="segments",
                antialias=True,
            )
        return self._unit_cell_lines

    def _apply_unit_cell(self, unit_cell: UnitCellScene | None) -> None:
        if unit_cell is None or len(unit_cell.segments) == 0:
            if self._unit_cell_lines is not None:
                self._unit_cell_lines.visible = False
            return
        segs = np.array(unit_cell.segments, dtype=np.float32)
        v = self._get_unit_cell_visual()
        v.visible = True
        v.set_data(pos=segs)

    # ── force arrows ──────────────────────────────────────────────────────────

    def _apply_forces(self, forces: ForceScene | None) -> None:
        if forces is None or len(forces.starts) == 0:
            if self._force_mesh is not None:
                self._force_mesh.visible = False
            return
        try:
            from ffast.visualization.stages.builtin.force_stages import _arrow_mesh
            starts = np.array(forces.starts, dtype=np.float64)
            ends = starts + np.array(forces.vectors, dtype=np.float64)
            verts, faces = _arrow_mesh(starts, ends)
        except Exception as exc:
            logger.debug("VispySceneAdapter: force arrow mesh failed: %s", exc)
            if self._force_mesh is not None:
                self._force_mesh.visible = False
            return

        if verts is None:
            if self._force_mesh is not None:
                self._force_mesh.visible = False
            return

        if self._force_mesh is None:
            from vispy import scene
            self._force_mesh = scene.visuals.Mesh(
                vertices=verts,
                faces=faces,
                parent=self._parent,
                color=(0.9, 0.4, 0.1, 0.8),
                shading="smooth",
            )
        else:
            self._force_mesh.set_data(vertices=verts, faces=faces)
        self._force_mesh.visible = True

    # ── labels ────────────────────────────────────────────────────────────────

    def _apply_labels(self, labels: LabelScene | None) -> None:
        if labels is None or len(labels.texts) == 0:
            if self._label_visual is not None:
                self._label_visual.visible = False
            return
        pos = np.array(labels.positions, dtype=np.float32)
        texts = labels.texts
        # Use the first label's color as the shared text color.
        color = tuple(labels.colors[0]) if labels.colors else (1.0, 1.0, 1.0, 1.0)
        if self._label_visual is None:
            from vispy import scene
            # font_size/bold approximate the legacy loupeIndices text; exact
            # parity should come from a label presentation parameter carried in
            # LabelScene (ADR 0014 follow-up) rather than a client constant.
            self._label_visual = scene.visuals.Text(
                text=texts,
                pos=pos,
                color=color,
                parent=self._parent,
                font_size=148,
                bold=True,
            )
        else:
            self._label_visual.text = texts
            self._label_visual.pos = pos
            self._label_visual.color = color
        self._label_visual.visible = True

    # ── selection overlays ────────────────────────────────────────────────────

    def _apply_selections(self, selections: list[SelectionOverlay] | None) -> None:
        active_names = {s.name for s in (selections or [])}
        for name, vis in self._selection_visuals.items():
            if name not in active_names:
                vis.visible = False

        if not selections or self._atom_positions is None:
            return

        for overlay in selections:
            if not overlay.atom_indices:
                if overlay.name in self._selection_visuals:
                    self._selection_visuals[overlay.name].visible = False
                continue

            indices = overlay.atom_indices
            pos = self._atom_positions[indices]
            color = np.array(overlay.color, dtype=np.float32)
            n = len(indices)
            face_color = np.tile(color, (n, 1))
            sizes = (
                self._atom_sizes[indices] * 1.15
                if self._atom_sizes is not None
                else np.ones(n, dtype=np.float32)
            )

            if overlay.name not in self._selection_visuals:
                from vispy import scene
                vis = scene.visuals.Markers(
                    parent=self._parent,
                    scaling=True,
                    spherical=True,
                    antialias=0,
                )
                self._selection_visuals[overlay.name] = vis

            vis = self._selection_visuals[overlay.name]
            vis.set_data(pos, face_color=face_color, size=sizes, edge_width=0)
            vis.visible = True

    # ── picking (ADR 0015) ──────────────────────────────────────────────────────

    def displayed_to_atom_id(self, k: int) -> int:
        """Map a displayed atom index to its scientific id (identity if no ids)."""
        if self._atom_ids is not None and 0 <= k < len(self._atom_ids):
            return int(self._atom_ids[k])
        return int(k)

    def _project_atoms(self):
        """Project cached atom positions to canvas pixels.

        Returns ``(xy (N,2), depth (N,))`` in canvas pixel coordinates, or
        ``(None, None)`` when atoms or the visual transform are unavailable.
        """
        if self._atom_markers is None or self._atom_positions is None:
            return None, None
        try:
            tr = self._atom_markers.get_transform("visual", "canvas")
            p = np.asarray(tr.map(self._atom_positions), dtype=np.float64)  # (N,4)
        except Exception as exc:
            logger.debug("VispySceneAdapter: projection failed: %s", exc)
            return None, None
        w = p[:, 3].copy()
        w[w == 0] = 1e-12
        xy = p[:, :2] / w[:, None]
        depth = p[:, 2] / w
        return xy, depth

    def pick_at(self, canvas_pos, radius: float = 12.0):
        """Nearest atom under a canvas pixel position, or None.

        Occlusion-correct: among atoms within ``radius`` pixels, returns the one
        closest to the camera (smallest projected depth).
        """
        xy, depth = self._project_atoms()
        if xy is None:
            return None
        cp = np.asarray(canvas_pos[:2], dtype=np.float64)
        d2 = np.sum((xy - cp) ** 2, axis=1)
        within = np.where(d2 <= radius * radius)[0]
        if len(within) == 0:
            return None
        return int(within[np.argmin(depth[within])])

    def pick_in_rect(self, p0, p1):
        """Displayed atom indices whose projected position falls inside the rect."""
        xy, _ = self._project_atoms()
        if xy is None:
            return []
        x0, x1 = sorted((float(p0[0]), float(p1[0])))
        y0, y1 = sorted((float(p0[1]), float(p1[1])))
        inside = (
            (xy[:, 0] >= x0) & (xy[:, 0] <= x1)
            & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)
        )
        return [int(i) for i in np.where(inside)[0]]

    def set_transient_highlight(self, displayed_index) -> None:
        """Client-local hover highlight (ADR 0015) — never sent to the server."""
        if displayed_index is None or self._atom_positions is None:
            if self._hover_visual is not None:
                self._hover_visual.visible = False
            return
        pos = self._atom_positions[[displayed_index]]
        if self._atom_sizes is not None:
            size = self._atom_sizes[[displayed_index]] * 1.25
        else:
            size = np.array([12.0], dtype=np.float32)
        if self._hover_visual is None:
            from vispy import scene
            self._hover_visual = scene.visuals.Markers(
                parent=self._parent, scaling=True, spherical=True, antialias=0,
            )
        self._hover_visual.set_data(
            pos, face_color=(1.0, 1.0, 1.0, 0.55), size=size, edge_width=0,
        )
        self._hover_visual.visible = True
