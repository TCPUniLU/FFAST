import pytest
from pydantic import TypeAdapter

from ffast.visualization.models import (
    CameraState,
    ChangeCellEdit,
    GeometryEdit,
    MoveAtomsEdit,
    SelectionScope,
    VisualizationState,
)


# --- CameraState ---

def test_camera_state_rejects_extra_fields():
    with pytest.raises(Exception):
        CameraState(unknown_field=1)


# --- SelectionScope ---

def test_selection_scope_values():
    assert SelectionScope.CURRENT_STRUCTURE == "current_structure"
    assert SelectionScope.STABLE_TOPOLOGY == "stable_topology"
    assert SelectionScope.ELEMENT == "element"
    assert SelectionScope.PER_STRUCTURE == "per_structure"


# --- GeometryEdit types ---

def test_geometry_edit_discriminated_union_move_atoms():
    ta = TypeAdapter(GeometryEdit)
    edit = ta.validate_python(
        {"type": "move_atoms", "atom_indices": [0], "displacement": [1.0, 0.0, 0.0]}
    )
    assert isinstance(edit, MoveAtomsEdit)
    assert edit.displacement == (1.0, 0.0, 0.0)


def test_geometry_edit_discriminated_union_change_cell():
    ta = TypeAdapter(GeometryEdit)
    edit = ta.validate_python(
        {"type": "change_cell", "new_cell": [[3, 0, 0], [0, 3, 0], [0, 0, 3]]}
    )
    assert isinstance(edit, ChangeCellEdit)


def test_geometry_edit_unknown_type_raises():
    ta = TypeAdapter(GeometryEdit)
    with pytest.raises(Exception):
        ta.validate_python({"type": "rotate_atoms", "angle": 90})


# --- VisualizationState ---

def test_visualization_state_version_starts_at_zero():
    state = VisualizationState(view_id="v2")
    assert state.version == 0


def test_visualization_state_rejects_extra_fields():
    with pytest.raises(Exception):
        VisualizationState(view_id="v1", not_a_field=True)
