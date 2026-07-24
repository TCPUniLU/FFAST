from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from ffast.visualization.commands import (
    ApplyGeometryEditCommand,
    ClearSelectionCommand,
    MaterializeDerivedDatasetCommand,
    RedoCommand,
    SetCameraCommand,
    SetDatasetRefCommand,
    SetFrameCommand,
    SetParameterCommand,
    SetSelectionCommand,
    ToggleFeatureCommand,
    UndoCommand,
    ViewCommand,
)
from ffast.visualization.models import (
    CameraState,
    DatasetProvenance,
    GeometryEdit,
    ScientificSelection,
    ScientificState,
    VisualizationState,
)
from ffast.visualization.scene import (
    CommandResult,
    ScenePatch,
    state_fields_to_scene_components,
)

_MAX_UNDO = 50


class VisualizationView:
    """
    Server-owned representation of one open inspection surface.

    Applies typed ViewCommands, enforces version-based stale rejection,
    manages scientific undo/redo, and returns ScenePatches describing
    which scene components changed.
    """

    def __init__(self, view_id: str) -> None:
        self._state = VisualizationState(view_id=view_id)
        self._undo_stack: deque[ScientificState] = deque(maxlen=_MAX_UNDO)
        self._redo_stack: deque[ScientificState] = deque(maxlen=_MAX_UNDO)

    @property
    def state(self) -> VisualizationState:
        return self._state

    @property
    def version(self) -> int:
        return self._state.version

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_command(self, command: ViewCommand) -> CommandResult:
        """Apply a command and return the result with a ScenePatch."""
        s = self._state

        # Last-write-wins commands: not version-gated, not undoable
        # (camera motion and frame playback are excluded from scientific
        # undo history — see CONTEXT + milestone6 §6.4). Clients send these
        # without tracking the view version.
        if isinstance(command, SetCameraCommand):
            return self._apply_camera(command)
        if isinstance(command, SetFrameCommand):
            return self._apply_set_frame(command)

        # All other commands require a matching view_version.
        if command.view_version != s.version:
            return CommandResult(
                success=False,
                new_version=s.version,
                error=(
                    f"Stale command: expected version {s.version}, "
                    f"got {command.view_version}"
                ),
                error_code="STALE_VERSION",
            )

        if isinstance(command, UndoCommand):
            return self._apply_undo(command)
        if isinstance(command, RedoCommand):
            return self._apply_redo(command)

        # Scientific (undoable) commands.
        return self._apply_scientific(command)

    def snapshot(self, get_dataset=None, get_prediction=None, get_forces=None, executor=None) -> "SceneSnapshot":
        from ffast.visualization.scene import RenderScene, SceneSnapshot
        if get_dataset is not None:
            from ffast.visualization.scene_builder import build_scene
            scene = build_scene(self._state, get_dataset, get_prediction, get_forces=get_forces, executor=executor)
        else:
            scene = RenderScene(
                view_id=self._state.view_id,
                version=self._state.version,
                camera=self._state.camera,
            )
        return SceneSnapshot(scene=scene)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _apply_camera(self, command: SetCameraCommand) -> CommandResult:
        prev_version = self._state.version
        self._state.camera = command.camera
        patch = self._make_patch(prev_version, prev_version, {"camera"})
        patch.camera = command.camera
        return CommandResult(success=True, new_version=prev_version, patch=patch)

    def _apply_set_frame(self, command: SetFrameCommand) -> CommandResult:
        # Frame playback is last-write-wins: it does NOT advance the scientific
        # version, so repeated frame changes from a client that always sends
        # view_version=0 keep succeeding (the previous version bump rejected
        # every frame after the first as STALE_VERSION).
        version = self._state.version
        self._state.structure_index = command.frame_index
        patch = self._make_patch(version, version, {"structure_index"})
        return CommandResult(success=True, new_version=version, patch=patch)

    def _apply_undo(self, command: UndoCommand) -> CommandResult:
        if not self._undo_stack:
            return CommandResult(
                success=False,
                new_version=self._state.version,
                error="Nothing to undo",
                error_code="EMPTY_UNDO_STACK",
            )
        prev_version = self._state.version
        self._redo_stack.append(self._scientific_snapshot())
        previous = self._undo_stack.pop()
        changed = self._restore_scientific(previous)
        self._state.version += 1
        patch = self._make_patch(prev_version, self._state.version, changed)
        return CommandResult(success=True, new_version=self._state.version, patch=patch)

    def _apply_redo(self, command: RedoCommand) -> CommandResult:
        if not self._redo_stack:
            return CommandResult(
                success=False,
                new_version=self._state.version,
                error="Nothing to redo",
                error_code="EMPTY_REDO_STACK",
            )
        prev_version = self._state.version
        self._undo_stack.append(self._scientific_snapshot())
        next_state = self._redo_stack.pop()
        changed = self._restore_scientific(next_state)
        self._state.version += 1
        patch = self._make_patch(prev_version, self._state.version, changed)
        return CommandResult(success=True, new_version=self._state.version, patch=patch)

    def _apply_scientific(self, command: ViewCommand) -> CommandResult:
        prev_version = self._state.version
        self._undo_stack.append(self._scientific_snapshot())
        self._redo_stack.clear()

        changed: set[str] = set()
        provenance: DatasetProvenance | None = None

        s = self._state

        if isinstance(command, SetParameterCommand):
            if command.stage_id not in s.parameters:
                s.parameters[command.stage_id] = {}
            s.parameters[command.stage_id][command.parameter] = command.value
            changed.add("parameters")

        elif isinstance(command, ToggleFeatureCommand):
            features = set(s.enabled_features)
            if command.enabled:
                features.add(command.feature)
            else:
                features.discard(command.feature)
            s.enabled_features = sorted(features)
            changed.add("enabled_features")

        elif isinstance(command, SetSelectionCommand):
            s.selections[command.name] = ScientificSelection(
                name=command.name,
                scope=command.scope,
                indices=list(command.indices),
            )
            changed.add("selections")

        elif isinstance(command, ClearSelectionCommand):
            s.selections.pop(command.name, None)
            changed.add("selections")

        elif isinstance(command, ApplyGeometryEditCommand):
            s.edit_log.append(command.edit)
            changed.add("edit_log")

        elif isinstance(command, SetDatasetRefCommand):
            s.dataset_ref = command.dataset_ref
            s.prediction_ref = command.prediction_ref
            s.structure_index = 0
            changed.update({"dataset_ref", "prediction_ref", "structure_index"})

        elif isinstance(command, MaterializeDerivedDatasetCommand):
            provenance = DatasetProvenance(
                source_fingerprint=command.source_fingerprint,
                source_dataset_ref=s.dataset_ref or "",
                edits=list(s.edit_log),
                transforms=list(s.transforms),
                created_at=command.created_at,
            )
            s.dataset_ref = command.new_dataset_id
            s.edit_log = []
            changed.update({"dataset_ref", "edit_log"})

        s.version += 1
        patch = self._make_patch(prev_version, s.version, changed)
        return CommandResult(
            success=True,
            new_version=s.version,
            patch=patch,
            provenance=provenance,
        )

    # ------------------------------------------------------------------
    # Undo/redo helpers
    # ------------------------------------------------------------------

    def _scientific_snapshot(self) -> ScientificState:
        s = self._state
        return ScientificState(
            dataset_ref=s.dataset_ref,
            prediction_ref=s.prediction_ref,
            subset_ref=s.subset_ref,
            enabled_features=list(s.enabled_features),
            parameters={k: dict(v) for k, v in s.parameters.items()},
            selections=dict(s.selections),
            transforms=list(s.transforms),
            edit_log=list(s.edit_log),
        )

    def _restore_scientific(self, snapshot: ScientificState) -> set[str]:
        """Restore scientific state from snapshot; return changed field names."""
        s = self._state
        changed: set[str] = set()

        def _maybe_set(field: str, new_value: Any) -> None:
            if getattr(s, field) != new_value:
                setattr(s, field, new_value)
                changed.add(field)

        _maybe_set("dataset_ref", snapshot.dataset_ref)
        _maybe_set("prediction_ref", snapshot.prediction_ref)
        _maybe_set("subset_ref", snapshot.subset_ref)
        _maybe_set("enabled_features", list(snapshot.enabled_features))
        _maybe_set("parameters", {k: dict(v) for k, v in snapshot.parameters.items()})
        _maybe_set("selections", dict(snapshot.selections))
        _maybe_set("transforms", list(snapshot.transforms))
        _maybe_set("edit_log", list(snapshot.edit_log))
        return changed

    # ------------------------------------------------------------------
    # Patch construction
    # ------------------------------------------------------------------

    def _make_patch(
        self, from_version: int, to_version: int, state_fields: set[str]
    ) -> ScenePatch:
        scene_components = state_fields_to_scene_components(state_fields)
        return ScenePatch(
            view_id=self._state.view_id,
            from_version=from_version,
            to_version=to_version,
            changed=scene_components,
        )
