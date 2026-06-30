import pytest
from pydantic import TypeAdapter

from ffast.visualization.models import (
    CameraState,
    ChangeCellEdit,
    DatasetProvenance,
    EditTarget,
    GeometryEdit,
    MoveAtomsEdit,
    ScientificSelection,
    ScientificState,
    SelectionScope,
    VisualizationState,
)


# --- CameraState ---

def test_camera_state_defaults():
    cam = CameraState()
    assert cam.distance == 10.0
    assert cam.projection == "perspective"
    assert cam.center == (0.0, 0.0, 0.0)
    assert cam.fov == 60.0


def test_camera_state_orthographic():
    cam = CameraState(projection="orthographic")
    assert cam.projection == "orthographic"


def test_camera_state_rejects_extra_fields():
    with pytest.raises(Exception):
        CameraState(unknown_field=1)


# --- SelectionScope ---

def test_selection_scope_values():
    assert SelectionScope.CURRENT_STRUCTURE == "current_structure"
    assert SelectionScope.STABLE_TOPOLOGY == "stable_topology"
    assert SelectionScope.ELEMENT == "element"
    assert SelectionScope.PER_STRUCTURE == "per_structure"


# --- ScientificSelection ---

def test_scientific_selection():
    sel = ScientificSelection(name="test", scope=SelectionScope.CURRENT_STRUCTURE, indices=[0, 1, 2])
    assert sel.name == "test"
    assert len(sel.indices) == 3


def test_scientific_selection_empty_indices():
    sel = ScientificSelection(name="empty", scope=SelectionScope.ELEMENT, indices=[])
    assert sel.indices == []


# --- GeometryEdit types ---

def test_move_atoms_edit_type():
    edit = MoveAtomsEdit(atom_indices=[0, 1], displacement=(1.0, 0.0, 0.0))
    assert edit.type == "move_atoms"
    assert edit.target == EditTarget.CURRENT_STRUCTURE
    assert edit.displacement == (1.0, 0.0, 0.0)


def test_change_cell_edit_type():
    cell = [[3.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 3.0]]
    edit = ChangeCellEdit(new_cell=cell)
    assert edit.type == "change_cell"
    assert len(edit.new_cell) == 3


def test_move_atoms_edit_explicit_target():
    edit = MoveAtomsEdit(
        atom_indices=[5],
        displacement=(0.0, 1.0, 0.0),
        target=EditTarget.EXPLICIT_SELECTION,
    )
    assert edit.target == EditTarget.EXPLICIT_SELECTION


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

def test_visualization_state_defaults():
    state = VisualizationState(view_id="v1")
    assert state.version == 0
    assert state.dataset_ref is None
    assert state.prediction_ref is None
    assert state.structure_index == 0
    assert state.enabled_features == []
    assert state.parameters == {}
    assert state.selections == {}
    assert state.edit_log == []


def test_visualization_state_camera_default():
    state = VisualizationState(view_id="v1")
    assert isinstance(state.camera, CameraState)
    assert state.camera.distance == 10.0


def test_visualization_state_version_starts_at_zero():
    state = VisualizationState(view_id="v2")
    assert state.version == 0


def test_visualization_state_rejects_extra_fields():
    with pytest.raises(Exception):
        VisualizationState(view_id="v1", not_a_field=True)


# --- ScientificState ---

def test_scientific_state_defaults():
    s = ScientificState()
    assert s.dataset_ref is None
    assert s.enabled_features == []
    assert s.edit_log == []


def test_scientific_state_with_edit_log():
    edit = MoveAtomsEdit(atom_indices=[0], displacement=(1.0, 0.0, 0.0))
    s = ScientificState(edit_log=[edit])
    assert len(s.edit_log) == 1
    assert isinstance(s.edit_log[0], MoveAtomsEdit)


# --- DatasetProvenance ---

def test_dataset_provenance_fields():
    edit = MoveAtomsEdit(atom_indices=[0], displacement=(1.0, 0.0, 0.0))
    prov = DatasetProvenance(
        source_fingerprint="abc123",
        source_dataset_ref="ds_original",
        edits=[edit],
        created_at="2026-06-11T00:00:00Z",
    )
    assert prov.source_fingerprint == "abc123"
    assert prov.source_dataset_ref == "ds_original"
    assert len(prov.edits) == 1
    assert isinstance(prov.edits[0], MoveAtomsEdit)


def test_dataset_provenance_empty_edits():
    prov = DatasetProvenance(
        source_fingerprint="xyz",
        source_dataset_ref="ds_orig",
        created_at="2026-06-11T00:00:00Z",
    )
    assert prov.edits == []
    assert prov.transforms == []
    assert prov.stage_versions == {}
