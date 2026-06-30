import pytest
from pydantic import TypeAdapter

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
    ViewCommand,
)
from ffast.visualization.models import CameraState, MoveAtomsEdit, SelectionScope


# --- SetFrameCommand ---

def test_set_frame_command_fields():
    cmd = SetFrameCommand(view_id="v1", view_version=0, frame_index=5)
    assert cmd.type == "SET_FRAME"
    assert cmd.frame_index == 5
    assert cmd.view_version == 0


# --- SetParameterCommand ---

def test_set_parameter_command_fields():
    cmd = SetParameterCommand(
        view_id="v1", view_version=3,
        stage_id="ffast.atom_sizes", parameter="scale", value=2.0
    )
    assert cmd.type == "SET_PARAMETER"
    assert cmd.stage_id == "ffast.atom_sizes"
    assert cmd.value == 2.0


def test_set_parameter_command_any_value_type():
    cmd = SetParameterCommand(
        view_id="v1", view_version=0,
        stage_id="ffast.atom_colors", parameter="colormap", value="viridis"
    )
    assert cmd.value == "viridis"


# --- ToggleFeatureCommand ---

def test_toggle_feature_enable():
    cmd = ToggleFeatureCommand(view_id="v1", view_version=0, feature="force_vectors", enabled=True)
    assert cmd.type == "TOGGLE_FEATURE"
    assert cmd.enabled is True


def test_toggle_feature_disable():
    cmd = ToggleFeatureCommand(view_id="v1", view_version=2, feature="labels", enabled=False)
    assert cmd.enabled is False


# --- SetCameraCommand ---

def test_set_camera_has_no_view_version():
    cmd = SetCameraCommand(view_id="v1", camera=CameraState())
    assert cmd.type == "SET_CAMERA"
    assert not hasattr(cmd, "view_version")


def test_set_camera_carries_camera_state():
    cam = CameraState(distance=50.0, projection="orthographic")
    cmd = SetCameraCommand(view_id="v1", camera=cam)
    assert cmd.camera.distance == 50.0
    assert cmd.camera.projection == "orthographic"


# --- SetSelectionCommand ---

def test_set_selection_command():
    cmd = SetSelectionCommand(
        view_id="v1", view_version=0,
        name="highlight", scope=SelectionScope.CURRENT_STRUCTURE,
        indices=[0, 1, 2],
    )
    assert cmd.type == "SET_SELECTION"
    assert cmd.name == "highlight"
    assert cmd.indices == [0, 1, 2]


# --- ClearSelectionCommand ---

def test_clear_selection_command():
    cmd = ClearSelectionCommand(view_id="v1", view_version=1, name="highlight")
    assert cmd.type == "CLEAR_SELECTION"
    assert cmd.name == "highlight"


# --- ApplyGeometryEditCommand ---

def test_apply_geometry_edit_command():
    edit = MoveAtomsEdit(atom_indices=[0, 1], displacement=(1.0, 0.0, 0.0))
    cmd = ApplyGeometryEditCommand(view_id="v1", view_version=0, edit=edit)
    assert cmd.type == "APPLY_GEOMETRY_EDIT"
    assert cmd.edit.type == "move_atoms"


# --- UndoCommand / RedoCommand ---

def test_undo_command():
    cmd = UndoCommand(view_id="v1", view_version=5)
    assert cmd.type == "UNDO"
    assert cmd.view_version == 5


def test_redo_command():
    cmd = RedoCommand(view_id="v1", view_version=4)
    assert cmd.type == "REDO"


# --- MaterializeDerivedDatasetCommand ---

def test_materialize_command():
    cmd = MaterializeDerivedDatasetCommand(
        view_id="v1", view_version=0, new_dataset_id="ds_derived"
    )
    assert cmd.type == "MATERIALIZE_DERIVED_DATASET"
    assert cmd.new_dataset_id == "ds_derived"


# --- ViewCommand discriminated union ---

def test_view_command_union_set_frame():
    ta = TypeAdapter(ViewCommand)
    cmd = ta.validate_python(
        {"type": "SET_FRAME", "view_id": "v1", "view_version": 0, "frame_index": 3}
    )
    assert isinstance(cmd, SetFrameCommand)
    assert cmd.frame_index == 3


def test_view_command_union_set_camera():
    ta = TypeAdapter(ViewCommand)
    cmd = ta.validate_python(
        {"type": "SET_CAMERA", "view_id": "v1", "camera": {"distance": 20.0}}
    )
    assert isinstance(cmd, SetCameraCommand)
    assert cmd.camera.distance == 20.0


def test_view_command_union_undo():
    ta = TypeAdapter(ViewCommand)
    cmd = ta.validate_python({"type": "UNDO", "view_id": "v1", "view_version": 2})
    assert isinstance(cmd, UndoCommand)


def test_view_command_union_unknown_type_raises():
    ta = TypeAdapter(ViewCommand)
    with pytest.raises(Exception):
        ta.validate_python({"type": "UNKNOWN_CMD", "view_id": "v1"})
