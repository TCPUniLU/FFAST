from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class CameraState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    distance: float = 10.0
    azimuth: float = 0.0
    elevation: float = 30.0
    fov: float = 60.0
    projection: Literal["perspective", "orthographic"] = "perspective"


class SelectionScope(str, Enum):
    CURRENT_STRUCTURE = "current_structure"
    STABLE_TOPOLOGY = "stable_topology"
    ELEMENT = "element"
    PER_STRUCTURE = "per_structure"


class ScientificSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    scope: SelectionScope
    indices: list[int]


class EditTarget(str, Enum):
    CURRENT_STRUCTURE = "current_structure"
    EXPLICIT_SELECTION = "explicit_selection"
    FULL_DATASET = "full_dataset"


class MoveAtomsEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["move_atoms"] = "move_atoms"
    target: EditTarget = EditTarget.CURRENT_STRUCTURE
    atom_indices: list[int]
    displacement: tuple[float, float, float]


class ChangeCellEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["change_cell"] = "change_cell"
    target: EditTarget = EditTarget.CURRENT_STRUCTURE
    new_cell: list[list[float]]


GeometryEdit = Annotated[
    Union[MoveAtomsEdit, ChangeCellEdit],
    Field(discriminator="type"),
]


class ScientificState(BaseModel):
    """Snapshot of the undoable portion of a view's state."""
    model_config = ConfigDict(extra="forbid")
    dataset_ref: str | None = None
    prediction_ref: str | None = None
    subset_ref: str | None = None
    enabled_features: list[str] = Field(default_factory=list)
    parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    selections: dict[str, ScientificSelection] = Field(default_factory=dict)
    transforms: list[Any] = Field(default_factory=list)
    edit_log: list[GeometryEdit] = Field(default_factory=list)


class VisualizationState(BaseModel):
    """Server-owned state for one open Visualization View."""
    model_config = ConfigDict(extra="forbid")
    view_id: str
    version: int = 0
    # Scientific state (participates in undo/redo)
    dataset_ref: str | None = None
    prediction_ref: str | None = None
    subset_ref: str | None = None
    enabled_features: list[str] = Field(default_factory=list)
    parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    selections: dict[str, ScientificSelection] = Field(default_factory=dict)
    transforms: list[Any] = Field(default_factory=list)
    edit_log: list[GeometryEdit] = Field(default_factory=list)
    # Non-scientific state (excluded from undo/redo)
    structure_index: int = 0
    camera: CameraState = Field(default_factory=CameraState)


class DatasetProvenance(BaseModel):
    """Reproducibility record attached to a Derived Dataset."""
    model_config = ConfigDict(extra="forbid")
    source_fingerprint: str
    source_dataset_ref: str
    edits: list[GeometryEdit] = Field(default_factory=list)
    transforms: list[Any] = Field(default_factory=list)
    created_at: str  # ISO 8601
    stage_versions: dict[str, str] = Field(default_factory=dict)
