"""Tests for ffast.renderers.vispy.adapter.VispySceneAdapter.

Uses a real Vispy canvas (requires PySide6 + display on macOS).
Tests are skipped gracefully when the Vispy backend is unavailable.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

# The Vispy SceneCanvas below resolves to the PySide6/Qt backend; force the
# offscreen platform so headless CI (no display) initialises cleanly rather
# than aborting on a missing xcb plugin.  Matches the sibling Qt test files
# (e.g. tests/ffast/test_panel_display_override.py).  Must be set before the
# vispy import pulls in Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

vispy = pytest.importorskip("vispy", reason="vispy not installed")


def _make_canvas_parent():
    """Return a Vispy scene parent node backed by an offscreen SceneCanvas."""
    from vispy import scene
    canvas = scene.SceneCanvas(show=False)
    view = canvas.central_widget.add_view()
    return view.scene


def _make_atom_scene(n=4):
    from ffast.visualization.scene import AtomScene
    return AtomScene(
        positions=[[float(i), 0.0, 0.0] for i in range(n)],
        sizes=[0.5] * n,
        colors=[[0.5, 0.5, 0.5, 1.0]] * n,
    )


def _make_bond_scene():
    from ffast.visualization.scene import BondScene
    return BondScene(segments=[[0, 0, 0], [1, 0, 0], [1, 0, 0], [2, 0, 0]])


def _make_unit_cell_scene():
    from ffast.visualization.scene import UnitCellScene
    # 12 edges x 2 endpoints = 24 rows
    segs = [[float(i % 2), 0.0, 0.0] for i in range(24)]
    return UnitCellScene(segments=segs)


def _make_label_scene(n=2):
    from ffast.visualization.scene import LabelScene
    return LabelScene(
        positions=[[float(i), 0.0, 0.0] for i in range(n)],
        texts=[str(i) for i in range(n)],
        colors=[[1.0, 1.0, 1.0, 1.0]] * n,
    )


def _make_selection_overlay(name="sel", atom_indices=None):
    from ffast.visualization.scene import SelectionOverlay
    return SelectionOverlay(
        name=name,
        atom_indices=atom_indices if atom_indices is not None else [0, 1],
        color=[1.0, 0.0, 0.0, 0.5],
    )


def _make_snapshot(atoms=None, bonds=None, unit_cell=None, forces=None, labels=None, selections=None):
    from ffast.visualization.scene import RenderScene, SceneSnapshot
    return SceneSnapshot(
        scene=RenderScene(
            view_id="test",
            version=1,
            atoms=atoms,
            bonds=bonds,
            unit_cell=unit_cell,
            forces=forces,
            labels=labels,
            selections=selections or [],
        )
    )


def _make_patch(changed, atoms=None, bonds=None, unit_cell=None, forces=None, labels=None, selections=None):
    from ffast.visualization.scene import ScenePatch
    return ScenePatch(
        view_id="test",
        from_version=1,
        to_version=2,
        changed=set(changed),
        atoms=atoms,
        bonds=bonds,
        unit_cell=unit_cell,
        forces=forces,
        labels=labels,
        selections=selections,
    )


# ── import ────────────────────────────────────────────────────────────────────

class TestImport:
    def test_imports_cleanly(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        assert VispySceneAdapter is not None


# ── apply_snapshot ────────────────────────────────────────────────────────────

class TestApplySnapshot:
    def test_atoms_visual_created(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(atoms=_make_atom_scene()))
        assert adapter._atom_markers is not None
        assert adapter._atom_markers.visible

    def test_bonds_visual_created(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(bonds=_make_bond_scene()))
        assert adapter._bond_lines is not None
        assert adapter._bond_lines.visible

    def test_unit_cell_visual_created(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(unit_cell=_make_unit_cell_scene()))
        assert adapter._unit_cell_lines is not None
        assert adapter._unit_cell_lines.visible

    def test_empty_snapshot_hides_existing_visuals(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        # First show atoms
        adapter.apply_snapshot(_make_snapshot(atoms=_make_atom_scene()))
        assert adapter._atom_markers.visible
        # Then apply snapshot with no atoms
        adapter.apply_snapshot(_make_snapshot())
        assert not adapter._atom_markers.visible

    def test_zero_atom_scene_hides_visual_and_clears_state(self):
        # An AtomScene with an empty positions list (distinct from atoms=None):
        # hits the `len(atoms.positions) == 0` guard — visual hidden, all atom
        # state cleared, no crash on the empty np.array conversions.
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        from ffast.visualization.scene import AtomScene
        adapter = VispySceneAdapter(_make_canvas_parent())
        adapter.apply_snapshot(_make_snapshot(atoms=_make_atom_scene(3)))
        assert adapter._atom_markers.visible

        adapter.apply_snapshot(
            _make_snapshot(atoms=AtomScene(positions=[], sizes=[], colors=[]))
        )
        assert not adapter._atom_markers.visible
        assert adapter._atom_positions is None
        assert adapter._color_by is None

    def test_full_snapshot_all_visuals_created(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(
            atoms=_make_atom_scene(),
            bonds=_make_bond_scene(),
            unit_cell=_make_unit_cell_scene(),
        ))
        assert adapter._atom_markers is not None
        assert adapter._bond_lines is not None
        assert adapter._unit_cell_lines is not None


# ── apply_patch ───────────────────────────────────────────────────────────────

class TestApplyPatch:
    def test_patch_updates_atoms_only(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        # Start with bonds present
        adapter.apply_snapshot(_make_snapshot(bonds=_make_bond_scene()))
        initial_bond_lines = adapter._bond_lines

        # Patch only atoms
        adapter.apply_patch(_make_patch(["atoms"], atoms=_make_atom_scene()))
        assert adapter._atom_markers is not None
        # Bonds visual unchanged
        assert adapter._bond_lines is initial_bond_lines

    def test_patch_hides_atoms_when_none(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(atoms=_make_atom_scene()))
        assert adapter._atom_markers.visible
        adapter.apply_patch(_make_patch(["atoms"], atoms=None))
        assert not adapter._atom_markers.visible

    def test_patch_unit_cell_visible(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_patch(_make_patch(["unit_cell"], unit_cell=_make_unit_cell_scene()))
        assert adapter._unit_cell_lines is not None
        assert adapter._unit_cell_lines.visible

    def test_patch_unchanged_field_not_updated(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        # No atoms after patch that only changes bonds
        adapter.apply_patch(_make_patch(["bonds"], bonds=_make_bond_scene()))
        assert adapter._atom_markers is None


# ── clear ─────────────────────────────────────────────────────────────────────

class TestClear:
    def test_clear_hides_all_visuals(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(
            atoms=_make_atom_scene(),
            bonds=_make_bond_scene(),
            unit_cell=_make_unit_cell_scene(),
        ))
        adapter.clear()
        assert not adapter._atom_markers.visible
        assert not adapter._bond_lines.visible
        assert not adapter._unit_cell_lines.visible

    def test_clear_hides_labels_and_selections(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(
            atoms=_make_atom_scene(4),
            labels=_make_label_scene(),
            selections=[_make_selection_overlay(atom_indices=[0, 1])],
        ))
        assert adapter._label_visual is not None and adapter._label_visual.visible
        assert "sel" in adapter._selection_visuals
        adapter.clear()
        assert not adapter._label_visual.visible
        for vis in adapter._selection_visuals.values():
            assert not vis.visible

    def test_clear_noop_when_no_visuals(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        # Should not raise even with no visuals created
        adapter.clear()


# ── labels ────────────────────────────────────────────────────────────────────

class TestLabels:
    def test_labels_visual_created_and_visible(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(labels=_make_label_scene()))
        assert adapter._label_visual is not None
        assert adapter._label_visual.visible

    def test_labels_hidden_when_none(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(labels=_make_label_scene()))
        assert adapter._label_visual.visible
        adapter.apply_snapshot(_make_snapshot())
        assert not adapter._label_visual.visible

    def test_patch_labels_updated(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_patch(_make_patch(["labels"], labels=_make_label_scene()))
        assert adapter._label_visual is not None
        assert adapter._label_visual.visible

    def test_patch_labels_cleared(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_patch(_make_patch(["labels"], labels=_make_label_scene()))
        assert adapter._label_visual.visible
        adapter.apply_patch(_make_patch(["labels"], labels=None))
        assert not adapter._label_visual.visible


# ── selections ────────────────────────────────────────────────────────────────

class TestSelections:
    def test_selection_visual_created_after_atoms_and_selections(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(
            atoms=_make_atom_scene(4),
            selections=[_make_selection_overlay("my_sel", atom_indices=[0, 2])],
        ))
        assert "my_sel" in adapter._selection_visuals
        assert adapter._selection_visuals["my_sel"].visible

    def test_selection_silently_skipped_without_atom_positions(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        # Apply selections before any atoms — must not crash
        adapter.apply_patch(_make_patch(
            ["selections"],
            selections=[_make_selection_overlay("sel", atom_indices=[0])],
        ))
        assert "sel" not in adapter._selection_visuals

    def test_removed_selection_is_hidden(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        # Apply atoms + selection
        adapter.apply_snapshot(_make_snapshot(
            atoms=_make_atom_scene(4),
            selections=[_make_selection_overlay("sel", atom_indices=[0])],
        ))
        assert adapter._selection_visuals["sel"].visible
        # Remove the selection in a patch
        adapter.apply_patch(_make_patch(["selections"], selections=[]))
        assert not adapter._selection_visuals["sel"].visible

    def test_atom_position_cache_populated(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(atoms=_make_atom_scene(4)))
        assert adapter._atom_positions is not None
        assert adapter._atom_positions.shape == (4, 3)

    def test_atom_position_cache_cleared_on_empty_atoms(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        parent = _make_canvas_parent()
        adapter = VispySceneAdapter(parent)
        adapter.apply_snapshot(_make_snapshot(atoms=_make_atom_scene(4)))
        assert adapter._atom_positions is not None
        adapter.apply_patch(_make_patch(["atoms"], atoms=None))
        assert adapter._atom_positions is None


# ── picking (ADR 0015) ─────────────────────────────────────────────────────────

def _atom_scene_with_ids(n, atom_ids):
    from ffast.visualization.scene import AtomScene
    return AtomScene(
        positions=[[float(i), 0.0, 0.0] for i in range(n)],
        sizes=[0.5] * n,
        colors=[[0.5, 0.5, 0.5, 1.0]] * n,
        atom_ids=atom_ids,
    )


class TestPicking:
    def _adapter(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        return VispySceneAdapter(_make_canvas_parent())

    def test_atom_ids_cached_from_snapshot(self):
        adapter = self._adapter()
        adapter.apply_snapshot(_make_snapshot(atoms=_atom_scene_with_ids(3, [4, 7, 9])))
        assert adapter._atom_ids == [4, 7, 9]

    def test_displayed_to_atom_id_uses_ids(self):
        adapter = self._adapter()
        adapter._atom_ids = [4, 7, 9]
        assert adapter.displayed_to_atom_id(0) == 4
        assert adapter.displayed_to_atom_id(2) == 9

    def test_displayed_to_atom_id_identity_without_ids(self):
        adapter = self._adapter()
        adapter._atom_ids = None
        assert adapter.displayed_to_atom_id(2) == 2

    def test_displayed_to_atom_id_identity_out_of_range(self):
        adapter = self._adapter()
        adapter._atom_ids = [4, 7]
        assert adapter.displayed_to_atom_id(5) == 5

    def test_pick_at_nearest_within_radius(self):
        adapter = self._adapter()
        # two atoms near the click, one far; depths pick the closer of the two.
        adapter._project_atoms = lambda: (
            np.array([[0.0, 0.0], [2.0, 0.0], [100.0, 100.0]]),
            np.array([0.5, 0.2, 0.9]),
        )
        # click near (1,0): atoms 0 and 1 within radius; atom 1 has min depth.
        assert adapter.pick_at((1.0, 0.0), radius=12.0) == 1

    def test_pick_at_returns_none_when_nothing_in_radius(self):
        adapter = self._adapter()
        adapter._project_atoms = lambda: (
            np.array([[0.0, 0.0], [2.0, 0.0]]),
            np.array([0.5, 0.2]),
        )
        assert adapter.pick_at((500.0, 500.0), radius=12.0) is None

    def test_pick_at_occlusion_picks_min_depth(self):
        adapter = self._adapter()
        # two atoms at the exact same screen point, different depth.
        adapter._project_atoms = lambda: (
            np.array([[10.0, 10.0], [10.0, 10.0]]),
            np.array([0.8, 0.3]),
        )
        assert adapter.pick_at((10.0, 10.0)) == 1  # the closer (smaller depth)

    def test_pick_in_rect_membership(self):
        adapter = self._adapter()
        adapter._project_atoms = lambda: (
            np.array([[0.0, 0.0], [5.0, 0.0], [100.0, 100.0]]),
            np.array([0.1, 0.2, 0.3]),
        )
        got = adapter.pick_in_rect((-1.0, -5.0), (10.0, 5.0))
        assert got == [0, 1]

    def test_pick_at_none_without_atoms(self):
        adapter = self._adapter()
        assert adapter.pick_at((0.0, 0.0)) is None

    def test_transient_highlight_created_and_hidden(self):
        adapter = self._adapter()
        adapter.apply_snapshot(_make_snapshot(atoms=_make_atom_scene(4)))
        adapter.set_transient_highlight(2)
        assert adapter._hover_visual is not None
        assert adapter._hover_visual.visible
        adapter.set_transient_highlight(None)
        assert not adapter._hover_visual.visible

    def test_clear_hides_transient_highlight(self):
        adapter = self._adapter()
        adapter.apply_snapshot(_make_snapshot(atoms=_make_atom_scene(4)))
        adapter.set_transient_highlight(1)
        assert adapter._hover_visual.visible
        adapter.clear()
        assert not adapter._hover_visual.visible


# ── value-driven coloring (ADR 0016) ───────────────────────────────────────────

class TestColorMapping:
    def _adapter(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        return VispySceneAdapter(_make_canvas_parent())

    def _color_by(self, values, colormap="viridis", vmin=0.0, vmax=1.0):
        from ffast.visualization.scene import AtomColorBy
        return AtomColorBy(values=values, colormap=colormap, vmin=vmin, vmax=vmax)

    def test_map_returns_rgba_per_value(self):
        adapter = self._adapter()
        out = adapter._map_color_by(self._color_by([0.0, 0.5, 1.0]))
        assert out is not None
        assert out.shape == (3, 4)

    def test_map_handles_degenerate_range(self):
        adapter = self._adapter()
        # vmax == vmin → all normalized to 0, no divide-by-zero
        out = adapter._map_color_by(self._color_by([5.0, 5.0], vmin=5.0, vmax=5.0))
        assert out is not None and out.shape == (2, 4)

    def test_force_error_colormap_supported(self):
        adapter = self._adapter()
        out = adapter._map_color_by(self._color_by([0.0, 1.0], colormap="force_error"))
        assert out is not None and out.shape == (2, 4)

    def test_map_nan_value_does_not_crash(self):
        # A NaN sample must not abort mapping: vispy clamps it to a
        # transparent-black row while the finite samples map normally.
        adapter = self._adapter()
        out = adapter._map_color_by(self._color_by([0.0, float("nan"), 1.0]))
        assert out is not None and out.shape == (3, 4)
        assert np.allclose(out[1], [0.0, 0.0, 0.0, 0.0])

    def test_map_empty_values_returns_none(self):
        # Empty values cannot be indexed into a colormap, so mapping fails and
        # returns None — the caller then falls back to element colors.
        adapter = self._adapter()
        assert adapter._map_color_by(self._color_by([])) is None

    def test_atoms_colored_by_color_by_when_present(self):
        from ffast.visualization.scene import AtomScene
        adapter = self._adapter()
        atoms = AtomScene(
            positions=[[0.0, 0, 0], [1.0, 0, 0]],
            sizes=[0.5, 0.5],
            colors=[[0, 0, 0, 1], [0, 0, 0, 1]],   # element fallback (black)
            color_by=self._color_by([0.0, 1.0], colormap="viridis"),
        )
        adapter.apply_snapshot(_make_snapshot(atoms=atoms))
        # Should not crash and the atom visual is shown (mapped colors used).
        assert adapter._atom_markers is not None
        assert adapter._atom_markers.visible

    def test_color_by_cached_for_colorbar(self):
        from ffast.visualization.scene import AtomScene
        adapter = self._adapter()
        atoms = AtomScene(
            positions=[[0.0, 0, 0]], sizes=[0.5], colors=[[0, 0, 0, 1]],
            color_by=self._color_by([0.5], colormap="plasma", vmin=0.0, vmax=1.0),
        )
        adapter.apply_snapshot(_make_snapshot(atoms=atoms))
        assert adapter._color_by is not None
        assert adapter._color_by.colormap == "plasma"

    def test_color_by_none_for_element_colors(self):
        adapter = self._adapter()
        adapter.apply_snapshot(_make_snapshot(atoms=_make_atom_scene(2)))
        assert adapter._color_by is None

    def test_color_by_cleared_on_empty_atoms(self):
        from ffast.visualization.scene import AtomScene
        adapter = self._adapter()
        atoms = AtomScene(
            positions=[[0.0, 0, 0]], sizes=[0.5], colors=[[0, 0, 0, 1]],
            color_by=self._color_by([0.5]),
        )
        adapter.apply_snapshot(_make_snapshot(atoms=atoms))
        assert adapter._color_by is not None
        adapter.apply_patch(_make_patch(["atoms"], atoms=None))
        assert adapter._color_by is None


# ── force arrow colours come from the scene (ADR 0052) ──────────────────────

class TestForceArrowColors:
    """The adapter used to hardcode ``color=(0.9, 0.4, 0.1, 0.8)`` — the same
    RGBA ``build_scene`` had already put in ``ForceScene.colors`` — and discard
    the scene's copy. Presentation crossed the seam and was then ignored, so a
    per-arrow colour was unreachable however the server set it.
    """

    def _adapter(self):
        from ffast.renderers.vispy.adapter import VispySceneAdapter
        return VispySceneAdapter(parent=_make_canvas_parent())

    @staticmethod
    def _forces(vectors, colors):
        from ffast.visualization.scene import ForceScene
        return ForceScene(
            starts=[[0.0, 0.0, 0.0]] * len(vectors),
            vectors=vectors,
            colors=colors,
        )

    def _vertex_colors(self, adapter):
        return np.asarray(adapter._force_mesh.mesh_data.get_vertex_colors())

    def test_scene_color_reaches_the_mesh(self):
        adapter = self._adapter()
        adapter.apply_snapshot(_make_snapshot(
            forces=self._forces([[1.0, 0, 0]], [[0.0, 0.25, 0.75, 0.5]])
        ))
        vc = self._vertex_colors(adapter)
        assert np.allclose(vc, [0.0, 0.25, 0.75, 0.5])

    def test_the_default_matches_the_shared_constant(self):
        """No second copy of the orange: what build_scene ships is what is drawn."""
        from ffast.visualization.presentation import FORCE_ARROW_COLOR
        adapter = self._adapter()
        adapter.apply_snapshot(_make_snapshot(
            forces=self._forces([[1.0, 0, 0]], [list(FORCE_ARROW_COLOR)])
        ))
        assert np.allclose(self._vertex_colors(adapter), FORCE_ARROW_COLOR)

    def test_per_arrow_colors_are_kept_distinct(self):
        adapter = self._adapter()
        adapter.apply_snapshot(_make_snapshot(forces=self._forces(
            [[1.0, 0, 0], [0.0, 1.0, 0]],
            [[1.0, 0.0, 0.0, 1.0], [0.0, 0.0, 1.0, 1.0]],
        )))
        vc = self._vertex_colors(adapter)
        assert {tuple(c) for c in np.unique(vc, axis=0)} == {
            (1.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)
        }

    def test_a_dropped_zero_length_arrow_does_not_shift_colors(self):
        """Zero-length arrows are not tessellated. Colouring by the tessellated
        position instead of the original index would paint arrow 1 with arrow
        0's colour.
        """
        adapter = self._adapter()
        adapter.apply_snapshot(_make_snapshot(forces=self._forces(
            [[0.0, 0.0, 0.0], [1.0, 0, 0]],          # arrow 0 dropped
            [[1.0, 0.0, 0.0, 1.0], [0.0, 0.0, 1.0, 1.0]],
        )))
        vc = self._vertex_colors(adapter)
        assert np.allclose(vc, [0.0, 0.0, 1.0, 1.0])   # arrow 1's colour only

    def test_a_short_color_array_is_rejected_by_the_scene_not_the_renderer(self):
        """The adapter needs no fallback: one RGBA per arrow is a ForceScene
        invariant, so a short list cannot reach a renderer to be guarded against.
        """
        with pytest.raises(Exception, match="colors for"):
            self._forces([[1.0, 0, 0], [0.0, 1.0, 0]], [[1.0, 0.0, 0.0, 1.0]])

    def test_colors_update_on_a_patch(self):
        adapter = self._adapter()
        adapter.apply_snapshot(_make_snapshot(
            forces=self._forces([[1.0, 0, 0]], [[1.0, 0.0, 0.0, 1.0]])
        ))
        adapter.apply_patch(_make_patch(
            ["forces"], forces=self._forces([[1.0, 0, 0]], [[0.0, 1.0, 0.0, 1.0]])
        ))
        assert np.allclose(self._vertex_colors(adapter), [0.0, 1.0, 0.0, 1.0])
