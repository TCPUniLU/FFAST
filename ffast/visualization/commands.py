from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from ffast.visualization.models import CameraState, GeometryEdit, SelectionScope


class SetFrameCommand(BaseModel):
    """Navigate to a different structure frame. Not undoable."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["SET_FRAME"] = "SET_FRAME"
    view_id: str
    view_version: int
    frame_index: int


class SetParameterCommand(BaseModel):
    """Set a stage parameter value. Undoable."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["SET_PARAMETER"] = "SET_PARAMETER"
    view_id: str
    view_version: int
    stage_id: str
    parameter: str
    value: Any


class ToggleFeatureCommand(BaseModel):
    """Enable or disable a named pipeline feature. Undoable."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["TOGGLE_FEATURE"] = "TOGGLE_FEATURE"
    view_id: str
    view_version: int
    feature: str
    enabled: bool


class SetCameraCommand(BaseModel):
    """Update camera state. Last-write-wins; bypasses version check."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["SET_CAMERA"] = "SET_CAMERA"
    view_id: str
    camera: CameraState


class SetSelectionCommand(BaseModel):
    """Create or replace a named scientific selection. Undoable."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["SET_SELECTION"] = "SET_SELECTION"
    view_id: str
    view_version: int
    name: str
    scope: SelectionScope
    indices: list[int]


class ClearSelectionCommand(BaseModel):
    """Remove a named scientific selection. Undoable."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["CLEAR_SELECTION"] = "CLEAR_SELECTION"
    view_id: str
    view_version: int
    name: str


class ApplyGeometryEditCommand(BaseModel):
    """Append a geometry edit to the view's edit log. Undoable."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["APPLY_GEOMETRY_EDIT"] = "APPLY_GEOMETRY_EDIT"
    view_id: str
    view_version: int
    edit: GeometryEdit


class UndoCommand(BaseModel):
    """Step back one scientific state change."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["UNDO"] = "UNDO"
    view_id: str
    view_version: int


class RedoCommand(BaseModel):
    """Step forward one previously undone scientific state change."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["REDO"] = "REDO"
    view_id: str
    view_version: int


class MaterializeDerivedDatasetCommand(BaseModel):
    """Materialize edit log as a new dataset; records provenance. Undoable."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["MATERIALIZE_DERIVED_DATASET"] = "MATERIALIZE_DERIVED_DATASET"
    view_id: str
    view_version: int
    new_dataset_id: str
    source_fingerprint: str = ""
    created_at: str = ""


class SetDatasetRefCommand(BaseModel):
    """Associate a view with a dataset and optional prediction. Undoable."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["SET_DATASET_REF"] = "SET_DATASET_REF"
    view_id: str
    view_version: int
    dataset_ref: Optional[str] = None
    prediction_ref: Optional[str] = None


ViewCommand = Annotated[
    Union[
        SetFrameCommand,
        SetParameterCommand,
        ToggleFeatureCommand,
        SetCameraCommand,
        SetSelectionCommand,
        ClearSelectionCommand,
        ApplyGeometryEditCommand,
        UndoCommand,
        RedoCommand,
        MaterializeDerivedDatasetCommand,
        SetDatasetRefCommand,
    ],
    Field(discriminator="type"),
]
