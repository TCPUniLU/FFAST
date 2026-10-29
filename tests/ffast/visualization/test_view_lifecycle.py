import pytest

from ffast.visualization.commands import (
    ApplyGeometryEditCommand,
    ClearSelectionCommand,
    MaterializeDerivedDatasetCommand,
    RedoCommand,
    SetCameraCommand,
    SetFrameCommand,
    SetParameterCommand,
    SetSelectionCommand,
    ToggleFeatureCommand,
    UndoCommand,
)
from ffast.visualization.models import CameraState, MoveAtomsEdit, SelectionScope
from ffast.visualization.view import VisualizationView


# --- Helpers ---

def _set_frame(view, version, index):
    return view.apply_command(SetFrameCommand(view_id=view.state.view_id, view_version=version, frame_index=index))

def _set_param(view, version, stage, param, value):
    return view.apply_command(SetParameterCommand(view_id=view.state.view_id, view_version=version, stage_id=stage, parameter=param, value=value))

def _toggle(view, version, feature, enabled):
    return view.apply_command(ToggleFeatureCommand(view_id=view.state.view_id, view_version=version, feature=feature, enabled=enabled))

def _undo(view, version):
    return view.apply_command(UndoCommand(view_id=view.state.view_id, view_version=version))

def _redo(view, version):
    return view.apply_command(RedoCommand(view_id=view.state.view_id, view_version=version))


# --- Version tracking ---

def test_initial_version_is_zero():
    view = VisualizationView("v1")
    assert view.version == 0


def test_set_frame_does_not_increment_version():
    # Frame playback is last-write-wins, not a scientific (versioned) change.
    view = VisualizationView("v1")
    result = _set_frame(view, 0, 5)
    assert result.success
    assert view.version == 0
    assert view.state.structure_index == 5


def test_consecutive_frames_all_succeed_lww():
    # Regression: the real client always sends view_version=0. Previously the
    # first frame bumped the version, so every later frame was rejected as
    # STALE_VERSION ("one frame changes then stuck"). LWW must keep applying.
    view = VisualizationView("v1")
    for target in (2, 5, 7):
        result = _set_frame(view, 0, target)
        assert result.success, f"frame {target} rejected: {result.error_code}"
        assert view.state.structure_index == target
    assert view.version == 0


def test_scientific_command_increments_version():
    view = VisualizationView("v1")
    result = _set_param(view, 0, "ffast.atom_sizes", "scale", 2.0)
    assert result.success
    assert view.version == 1


def test_each_scientific_command_increments_version_once():
    view = VisualizationView("v1")
    _set_param(view, 0, "s", "p", 1)
    _set_param(view, 1, "s", "p", 2)
    _set_param(view, 2, "s", "p", 3)
    assert view.version == 3


# --- Stale command rejection (scientific/undoable commands only) ---

def test_stale_command_rejected():
    view = VisualizationView("v1")
    _set_param(view, 0, "s", "p", 1)   # version → 1
    result = _set_param(view, 0, "s", "p", 2)   # stale
    assert not result.success
    assert result.error_code == "STALE_VERSION"


def test_stale_command_does_not_change_version():
    view = VisualizationView("v1")
    _set_param(view, 0, "s", "p", 1)
    _set_param(view, 0, "s", "p", 99)   # stale, ignored
    assert view.version == 1


def test_future_version_also_rejected():
    view = VisualizationView("v1")
    result = _set_param(view, 5, "s", "p", 1)   # version 5 > current 0
    assert not result.success
    assert result.error_code == "STALE_VERSION"


# --- Camera + frame: bypass version check ---

def test_camera_command_succeeds_without_version():
    view = VisualizationView("v1")
    _set_param(view, 0, "s", "p", 1)   # version → 1
    cam = CameraState(distance=30.0)
    result = view.apply_command(SetCameraCommand(view_id="v1", camera=cam))
    assert result.success
    assert view.state.camera.distance == 30.0


def test_camera_command_does_not_bump_version():
    view = VisualizationView("v1")
    cam = CameraState(distance=20.0)
    view.apply_command(SetCameraCommand(view_id="v1", camera=cam))
    assert view.version == 0


def test_camera_command_after_stale_scientific_succeeds():
    view = VisualizationView("v1")
    _set_param(view, 0, "s", "p", 1)   # version → 1
    # camera still works regardless of version
    result = view.apply_command(SetCameraCommand(view_id="v1", camera=CameraState()))
    assert result.success


# --- SET_FRAME not undoable ---

def test_set_frame_not_in_undo_stack():
    view = VisualizationView("v1")
    _set_frame(view, 0, 5)   # LWW, version stays 0
    result = _undo(view, 0)
    assert not result.success
    assert result.error_code == "EMPTY_UNDO_STACK"


# --- SetParameter ---

def test_set_parameter_stored():
    view = VisualizationView("v1")
    _set_param(view, 0, "ffast.atom_sizes", "scale", 2.5)
    assert view.state.parameters["ffast.atom_sizes"]["scale"] == 2.5


def test_set_parameter_second_stage():
    view = VisualizationView("v1")
    _set_param(view, 0, "ffast.atom_sizes", "scale", 2.0)
    _set_param(view, 1, "ffast.atom_colors", "dimming", 0.5)
    assert view.state.parameters["ffast.atom_colors"]["dimming"] == 0.5


# --- ToggleFeature ---

def test_toggle_feature_enables():
    view = VisualizationView("v1")
    _toggle(view, 0, "force_vectors", True)
    assert "force_vectors" in view.state.enabled_features


def test_toggle_feature_disables():
    view = VisualizationView("v1")
    _toggle(view, 0, "force_vectors", True)
    _toggle(view, 1, "force_vectors", False)
    assert "force_vectors" not in view.state.enabled_features


def test_toggle_feature_idempotent_enable():
    view = VisualizationView("v1")
    _toggle(view, 0, "force_vectors", True)
    _toggle(view, 1, "force_vectors", True)
    assert view.state.enabled_features.count("force_vectors") == 1


def test_toggle_features_sorted():
    view = VisualizationView("v1")
    _toggle(view, 0, "zzz", True)
    _toggle(view, 1, "aaa", True)
    assert view.state.enabled_features == ["aaa", "zzz"]


# --- SetSelection / ClearSelection ---

def test_set_selection_stored():
    view = VisualizationView("v1")
    view.apply_command(SetSelectionCommand(
        view_id="v1", view_version=0,
        name="highlight", scope=SelectionScope.CURRENT_STRUCTURE, indices=[0, 1, 2],
    ))
    assert "highlight" in view.state.selections
    assert view.state.selections["highlight"].indices == [0, 1, 2]


def test_clear_selection_removes():
    view = VisualizationView("v1")
    view.apply_command(SetSelectionCommand(
        view_id="v1", view_version=0,
        name="highlight", scope=SelectionScope.CURRENT_STRUCTURE, indices=[0],
    ))
    view.apply_command(ClearSelectionCommand(view_id="v1", view_version=1, name="highlight"))
    assert "highlight" not in view.state.selections


def test_clear_nonexistent_selection_succeeds():
    view = VisualizationView("v1")
    result = view.apply_command(ClearSelectionCommand(view_id="v1", view_version=0, name="missing"))
    assert result.success


# --- ApplyGeometryEdit ---

def test_apply_geometry_edit_appended():
    view = VisualizationView("v1")
    edit = MoveAtomsEdit(atom_indices=[0, 1], displacement=(1.0, 0.0, 0.0))
    view.apply_command(ApplyGeometryEditCommand(view_id="v1", view_version=0, edit=edit))
    assert len(view.state.edit_log) == 1
    assert isinstance(view.state.edit_log[0], MoveAtomsEdit)


def test_multiple_edits_accumulate():
    view = VisualizationView("v1")
    e1 = MoveAtomsEdit(atom_indices=[0], displacement=(1.0, 0.0, 0.0))
    e2 = MoveAtomsEdit(atom_indices=[1], displacement=(0.0, 1.0, 0.0))
    view.apply_command(ApplyGeometryEditCommand(view_id="v1", view_version=0, edit=e1))
    view.apply_command(ApplyGeometryEditCommand(view_id="v1", view_version=1, edit=e2))
    assert len(view.state.edit_log) == 2


# --- MaterializeDerivedDataset ---

def test_materialize_switches_dataset_ref():
    view = VisualizationView("v1")
    view.state.dataset_ref = "ds_original"
    result = view.apply_command(MaterializeDerivedDatasetCommand(
        view_id="v1", view_version=0,
        new_dataset_id="ds_derived",
        source_fingerprint="fp123",
        created_at="2026-06-11T00:00:00Z",
    ))
    assert result.success
    assert view.state.dataset_ref == "ds_derived"


def test_materialize_clears_edit_log():
    view = VisualizationView("v1")
    view.state.dataset_ref = "ds_original"
    edit = MoveAtomsEdit(atom_indices=[0], displacement=(1.0, 0.0, 0.0))
    view.apply_command(ApplyGeometryEditCommand(view_id="v1", view_version=0, edit=edit))
    view.apply_command(MaterializeDerivedDatasetCommand(
        view_id="v1", view_version=1, new_dataset_id="ds_derived"
    ))
    assert view.state.edit_log == []


def test_materialize_returns_provenance():
    view = VisualizationView("v1")
    view.state.dataset_ref = "ds_original"
    edit = MoveAtomsEdit(atom_indices=[0], displacement=(1.0, 0.0, 0.0))
    view.apply_command(ApplyGeometryEditCommand(view_id="v1", view_version=0, edit=edit))
    result = view.apply_command(MaterializeDerivedDatasetCommand(
        view_id="v1", view_version=1, new_dataset_id="ds_derived",
        source_fingerprint="fp123", created_at="2026-06-11T00:00:00Z",
    ))
    assert result.provenance is not None
    assert result.provenance.source_dataset_ref == "ds_original"
    assert result.provenance.source_fingerprint == "fp123"
    assert len(result.provenance.edits) == 1


# --- Undo/redo ---

def test_undo_empty_stack_fails():
    view = VisualizationView("v1")
    result = _undo(view, 0)
    assert not result.success
    assert result.error_code == "EMPTY_UNDO_STACK"


def test_redo_empty_stack_fails():
    view = VisualizationView("v1")
    result = _redo(view, 0)
    assert not result.success
    assert result.error_code == "EMPTY_REDO_STACK"


def test_undo_reverts_parameter():
    view = VisualizationView("v1")
    _set_param(view, 0, "ffast.atom_sizes", "scale", 2.0)
    assert view.state.parameters["ffast.atom_sizes"]["scale"] == 2.0
    _undo(view, 1)
    assert view.state.parameters.get("ffast.atom_sizes", {}).get("scale") is None


def test_undo_increments_version():
    view = VisualizationView("v1")
    _set_param(view, 0, "ffast.atom_sizes", "scale", 2.0)
    assert view.version == 1
    _undo(view, 1)
    assert view.version == 2


def test_redo_reapplies_parameter():
    view = VisualizationView("v1")
    _set_param(view, 0, "ffast.atom_sizes", "scale", 2.0)
    _undo(view, 1)
    _redo(view, 2)
    assert view.state.parameters["ffast.atom_sizes"]["scale"] == 2.0


def test_new_command_clears_redo_stack():
    view = VisualizationView("v1")
    _set_param(view, 0, "ffast.atom_sizes", "scale", 2.0)
    _undo(view, 1)
    # Issue a new scientific command; redo stack should be cleared.
    _set_param(view, 2, "ffast.atom_sizes", "scale", 3.0)
    result = _redo(view, 3)
    assert not result.success
    assert result.error_code == "EMPTY_REDO_STACK"


def test_undo_redo_multiple_steps():
    view = VisualizationView("v1")
    _set_param(view, 0, "ffast.atom_sizes", "scale", 1.0)
    _set_param(view, 1, "ffast.atom_sizes", "scale", 2.0)
    _set_param(view, 2, "ffast.atom_sizes", "scale", 3.0)
    _undo(view, 3)
    assert view.state.parameters["ffast.atom_sizes"]["scale"] == 2.0
    _undo(view, 4)
    assert view.state.parameters["ffast.atom_sizes"]["scale"] == 1.0
    _redo(view, 5)
    assert view.state.parameters["ffast.atom_sizes"]["scale"] == 2.0


def test_undo_reverts_toggle_feature():
    view = VisualizationView("v1")
    _toggle(view, 0, "force_vectors", True)
    assert "force_vectors" in view.state.enabled_features
    _undo(view, 1)
    assert "force_vectors" not in view.state.enabled_features


def test_undo_reverts_selection():
    view = VisualizationView("v1")
    view.apply_command(SetSelectionCommand(
        view_id="v1", view_version=0,
        name="sel", scope=SelectionScope.CURRENT_STRUCTURE, indices=[0],
    ))
    _undo(view, 1)
    assert "sel" not in view.state.selections


def test_undo_reverts_materialize():
    view = VisualizationView("v1")
    view.state.dataset_ref = "ds_original"
    edit = MoveAtomsEdit(atom_indices=[0], displacement=(1.0, 0.0, 0.0))
    view.apply_command(ApplyGeometryEditCommand(view_id="v1", view_version=0, edit=edit))
    view.apply_command(MaterializeDerivedDatasetCommand(
        view_id="v1", view_version=1, new_dataset_id="ds_derived"
    ))
    assert view.state.dataset_ref == "ds_derived"
    _undo(view, 2)
    assert view.state.dataset_ref == "ds_original"
    assert len(view.state.edit_log) == 1


# --- Scene patches ---

def test_patch_returned_on_success():
    view = VisualizationView("v1")
    result = _set_frame(view, 0, 1)
    assert result.patch is not None


def test_patch_versions_correct():
    # A versioned (scientific) command advances from_version → to_version.
    view = VisualizationView("v1")
    result = _set_param(view, 0, "s", "p", 1)
    assert result.patch.from_version == 0
    assert result.patch.to_version == 1


def test_camera_patch_marks_camera_changed():
    view = VisualizationView("v1")
    result = view.apply_command(SetCameraCommand(view_id="v1", camera=CameraState(distance=20.0)))
    assert "camera" in result.patch.changed
    assert result.patch.camera is not None
    assert result.patch.camera.distance == 20.0


def test_set_frame_patch_marks_structure_components():
    view = VisualizationView("v1")
    result = _set_frame(view, 0, 3)
    assert "atoms" in result.patch.changed


def test_set_parameter_patch_marks_atoms():
    view = VisualizationView("v1")
    result = _set_param(view, 0, "ffast.atom_sizes", "scale", 2.0)
    assert "atoms" in result.patch.changed


def test_set_selection_patch_marks_selections():
    view = VisualizationView("v1")
    result = view.apply_command(SetSelectionCommand(
        view_id="v1", view_version=0,
        name="s", scope=SelectionScope.CURRENT_STRUCTURE, indices=[],
    ))
    assert "selections" in result.patch.changed


def test_no_patch_on_failure():
    view = VisualizationView("v1")
    result = _set_param(view, 5, "s", "p", 1)   # stale (version 5 != 0)
    assert result.patch is None


# --- snapshot() ---

import numpy as np


class _MinimalDataset:
    isVariable = False
    _z = np.array([6, 1], dtype=np.int64)
    _R = np.array([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]], dtype=np.float64)

    def getN(self):
        return 1

    def getCoordinates(self, idx):
        return self._R[0]

    def getElements(self, idx=None):
        return self._z

    def getBondIndices(self, idx):
        return np.empty((0, 2), dtype=np.int64)


def test_snapshot_no_dataset_is_skeleton():
    view = VisualizationView("v1")
    snap = view.snapshot()
    assert snap.scene.atoms is None
    assert snap.scene.bonds is None
    assert snap.scene.view_id == "v1"


def test_snapshot_with_dataset_populates_atoms():
    view = VisualizationView("v1")
    view.state.dataset_ref = "ds"
    ds = _MinimalDataset()
    snap = view.snapshot(get_dataset=lambda fp: ds)
    assert snap.scene.atoms is not None
    assert len(snap.scene.atoms.positions) == 2


def test_snapshot_with_dataset_preserves_version():
    view = VisualizationView("v1")
    _set_param(view, 0, "s", "p", 1)   # scientific command → version 1
    view.state.dataset_ref = "ds"
    ds = _MinimalDataset()
    snap = view.snapshot(get_dataset=lambda fp: ds)
    assert snap.scene.version == 1


def test_snapshot_with_unknown_dataset_is_skeleton():
    view = VisualizationView("v1")
    view.state.dataset_ref = "missing"
    snap = view.snapshot(get_dataset=lambda fp: None)
    assert snap.scene.atoms is None


def test_snapshot_camera_propagated():
    view = VisualizationView("v1")
    view.apply_command(SetCameraCommand(view_id="v1", camera=CameraState(distance=42.0)))
    snap = view.snapshot()
    assert snap.scene.camera.distance == 42.0
