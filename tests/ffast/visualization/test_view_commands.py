import pytest
from pydantic import TypeAdapter

from ffast.visualization.commands import (
    SetCameraCommand,
    SetFrameCommand,
    UndoCommand,
    ViewCommand,
)
from ffast.visualization.models import CameraState


# --- SetCameraCommand ---

def test_set_camera_has_no_view_version():
    cmd = SetCameraCommand(view_id="v1", camera=CameraState())
    assert cmd.type == "SET_CAMERA"
    assert not hasattr(cmd, "view_version")


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
