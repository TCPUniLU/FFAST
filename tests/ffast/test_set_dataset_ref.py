"""Tests for SetDatasetRefCommand and its effect on VisualizationView."""
import pytest
from pydantic import TypeAdapter

from ffast.visualization.commands import SetDatasetRefCommand, ViewCommand
from ffast.visualization.view import VisualizationView


class TestSetDatasetRefCommand:
    def test_type_discriminator(self):
        cmd = SetDatasetRefCommand(view_id="v1", view_version=0, dataset_ref="abc")
        assert cmd.type == "SET_DATASET_REF"

    def test_nullable_refs(self):
        cmd = SetDatasetRefCommand(view_id="v1", view_version=0)
        assert cmd.dataset_ref is None
        assert cmd.prediction_ref is None

    def test_round_trips_via_view_command_union(self):
        ta = TypeAdapter(ViewCommand)
        cmd = ta.validate_python({
            "type": "SET_DATASET_REF",
            "view_id": "v1",
            "view_version": 0,
            "dataset_ref": "fp_abc",
            "prediction_ref": "fp_model",
        })
        assert isinstance(cmd, SetDatasetRefCommand)
        assert cmd.dataset_ref == "fp_abc"
        assert cmd.prediction_ref == "fp_model"


class TestViewApplySetDatasetRef:
    def test_sets_dataset_ref(self):
        view = VisualizationView("v1")
        cmd = SetDatasetRefCommand(view_id="v1", view_version=0, dataset_ref="ds_fp")
        result = view.apply_command(cmd)
        assert result.success
        assert view.state.dataset_ref == "ds_fp"

    def test_resets_structure_index(self):
        view = VisualizationView("v1")
        # advance to frame 5
        from ffast.visualization.commands import SetFrameCommand
        view.apply_command(SetFrameCommand(view_id="v1", view_version=0, frame_index=5))
        assert view.state.structure_index == 5

        # SET_FRAME is last-write-wins and does not bump the version, so the
        # dataset-ref command still applies at version 0.
        cmd = SetDatasetRefCommand(view_id="v1", view_version=0, dataset_ref="new_ds")
        view.apply_command(cmd)
        assert view.state.structure_index == 0

    def test_sets_prediction_ref(self):
        view = VisualizationView("v1")
        cmd = SetDatasetRefCommand(view_id="v1", view_version=0, dataset_ref="ds", prediction_ref="pred")
        view.apply_command(cmd)
        assert view.state.prediction_ref == "pred"

    def test_clears_refs_with_none(self):
        view = VisualizationView("v1")
        cmd1 = SetDatasetRefCommand(view_id="v1", view_version=0, dataset_ref="ds", prediction_ref="pred")
        view.apply_command(cmd1)
        cmd2 = SetDatasetRefCommand(view_id="v1", view_version=1, dataset_ref=None, prediction_ref=None)
        view.apply_command(cmd2)
        assert view.state.dataset_ref is None
        assert view.state.prediction_ref is None

    def test_is_undoable(self):
        view = VisualizationView("v1")
        cmd = SetDatasetRefCommand(view_id="v1", view_version=0, dataset_ref="ds_fp")
        view.apply_command(cmd)
        assert view.state.dataset_ref == "ds_fp"

        from ffast.visualization.commands import UndoCommand
        view.apply_command(UndoCommand(view_id="v1", view_version=1))
        assert view.state.dataset_ref is None

    def test_returns_patch_with_changed_components(self):
        view = VisualizationView("v1")
        cmd = SetDatasetRefCommand(view_id="v1", view_version=0, dataset_ref="ds_fp")
        result = view.apply_command(cmd)
        assert result.patch is not None
        # dataset_ref change should mark atoms/bonds/forces/labels/unit_cell as changed
        assert len(result.patch.changed) > 0

    def test_stale_version_rejected(self):
        view = VisualizationView("v1")
        # First command advances version to 1
        cmd1 = SetDatasetRefCommand(view_id="v1", view_version=0, dataset_ref="ds1")
        view.apply_command(cmd1)
        # Send with stale version 0
        cmd2 = SetDatasetRefCommand(view_id="v1", view_version=0, dataset_ref="ds2")
        result = view.apply_command(cmd2)
        assert not result.success
        assert result.error_code == "STALE_VERSION"
        assert view.state.dataset_ref == "ds1"
