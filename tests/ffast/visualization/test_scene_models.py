import pytest

from ffast.visualization.models import CameraState
from ffast.visualization.scene import (
    AtomScene,
    BondScene,
    CommandResult,
    ForceScene,
    LabelScene,
    RenderScene,
    ScenePatch,
    SceneSnapshot,
    SelectionOverlay,
    UnitCellScene,
    state_fields_to_scene_components,
)


# --- AtomScene ---

def test_atom_scene_fields():
    scene = AtomScene(
        positions=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        sizes=[0.37, 0.77],
        colors=[[1.0, 1.0, 1.0, 1.0], [0.5, 0.5, 0.5, 1.0]],
    )
    assert len(scene.positions) == 2
    assert len(scene.sizes) == 2


# --- BondScene ---

def test_bond_scene_segments():
    bond = BondScene(segments=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    assert len(bond.segments) == 2


# --- ForceScene ---

def test_force_scene_fields():
    f = ForceScene(
        starts=[[0.0, 0.0, 0.0]],
        vectors=[[0.1, 0.0, 0.0]],
        colors=[[1.0, 0.0, 0.0, 1.0]],
    )
    assert len(f.starts) == 1


# --- LabelScene ---

def test_label_scene_fields():
    label = LabelScene(
        positions=[[0.0, 0.0, 0.0]],
        texts=["H"],
        colors=[[1.0, 1.0, 1.0, 1.0]],
    )
    assert label.texts == ["H"]


# --- UnitCellScene ---

def test_unit_cell_scene_24_points():
    segs = [[float(i)] * 3 for i in range(24)]
    cell = UnitCellScene(segments=segs)
    assert len(cell.segments) == 24


# --- SelectionOverlay ---

def test_selection_overlay():
    overlay = SelectionOverlay(name="sel", atom_indices=[0, 2], color=[1.0, 0.0, 0.0, 0.5])
    assert overlay.name == "sel"
    assert overlay.atom_indices == [0, 2]


# --- RenderScene ---

def test_render_scene_minimal():
    scene = RenderScene(view_id="v1", version=0, camera=CameraState())
    assert scene.view_id == "v1"
    assert scene.version == 0
    assert scene.atoms is None
    assert scene.bonds is None
    assert scene.selections == []


def test_render_scene_with_atoms():
    atoms = AtomScene(positions=[[0, 0, 0]], sizes=[0.5], colors=[[1, 1, 1, 1]])
    scene = RenderScene(view_id="v1", version=1, camera=CameraState(), atoms=atoms)
    assert scene.atoms is not None


def test_render_scene_rejects_extra_fields():
    with pytest.raises(Exception):
        RenderScene(view_id="v1", version=0, camera=CameraState(), unknown=True)


# --- SceneSnapshot ---

def test_scene_snapshot_wraps_render_scene():
    scene = RenderScene(view_id="v1", version=2, camera=CameraState())
    snapshot = SceneSnapshot(scene=scene)
    assert snapshot.scene.version == 2
    assert snapshot.scene.view_id == "v1"


# --- ScenePatch ---

def test_scene_patch_minimal():
    patch = ScenePatch(view_id="v1", from_version=0, to_version=1, changed={"atoms"})
    assert "atoms" in patch.changed
    assert patch.bonds is None
    assert patch.camera is None


def test_scene_patch_with_camera():
    cam = CameraState(distance=25.0)
    patch = ScenePatch(view_id="v1", from_version=0, to_version=0, changed={"camera"}, camera=cam)
    assert "camera" in patch.changed
    assert patch.camera.distance == 25.0


def test_scene_patch_multiple_components():
    patch = ScenePatch(
        view_id="v1", from_version=0, to_version=1,
        changed={"atoms", "bonds", "selections"}
    )
    assert "atoms" in patch.changed
    assert "bonds" in patch.changed
    assert "selections" in patch.changed


def test_scene_patch_rejects_extra_fields():
    with pytest.raises(Exception):
        ScenePatch(view_id="v1", from_version=0, to_version=1, changed=set(), extra=True)


# --- state_fields_to_scene_components ---

def test_camera_field_maps_to_camera_component():
    assert state_fields_to_scene_components({"camera"}) == {"camera"}


def test_structure_index_maps_to_render_primitives():
    components = state_fields_to_scene_components({"structure_index"})
    assert "atoms" in components
    assert "bonds" in components
    assert "unit_cell" in components


def test_selections_field_maps_to_selections_component():
    assert state_fields_to_scene_components({"selections"}) == {"selections"}


def test_prediction_ref_maps_to_forces():
    components = state_fields_to_scene_components({"prediction_ref"})
    assert "forces" in components


def test_multiple_fields_union():
    components = state_fields_to_scene_components({"camera", "selections"})
    assert "camera" in components
    assert "selections" in components


def test_unknown_field_returns_empty():
    components = state_fields_to_scene_components({"not_a_state_field"})
    assert components == set()


# --- CommandResult ---

def test_command_result_success():
    result = CommandResult(success=True, new_version=1)
    assert result.success
    assert result.error is None
    assert result.patch is None


def test_command_result_failure():
    result = CommandResult(
        success=False, new_version=0,
        error="Stale command", error_code="STALE_VERSION"
    )
    assert not result.success
    assert result.error_code == "STALE_VERSION"
