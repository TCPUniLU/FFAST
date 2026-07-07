import pytest

from ffast.visualization.models import CameraState
from ffast.visualization.scene import (
    RenderScene,
    ScenePatch,
    state_fields_to_scene_components,
)


# --- RenderScene ---

def test_render_scene_rejects_extra_fields():
    with pytest.raises(Exception):
        RenderScene(view_id="v1", version=0, camera=CameraState(), unknown=True)


# --- ScenePatch ---

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
