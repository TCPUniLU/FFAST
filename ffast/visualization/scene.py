from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ffast.visualization.models import CameraState, DatasetProvenance


class AtomColorBy(BaseModel):
    """Value-driven atom coloring descriptor (ADR 0016).

    The server ships per-atom scalar ``values`` plus a colormap descriptor; each
    renderer maps values → RGBA and draws the colorbar. ``vmin``/``vmax`` are
    resolved server-side so all renderers and the colorbar agree.
    """
    model_config = ConfigDict(extra="forbid")
    values: list[float]            # (N,) one scalar per displayed atom
    colormap: str = "viridis"
    vmin: float = 0.0
    vmax: float = 1.0
    label: str = ""
    unit: str = ""


class AtomScene(BaseModel):
    """Renderer-neutral atom instance data."""
    model_config = ConfigDict(extra="forbid")
    positions: list[list[float]]   # (N, 3)
    sizes: list[float]             # (N,)
    colors: list[list[float]]      # (N, 4) RGBA — element/default fallback
    # Original/scientific structure index of each displayed atom (ADR 0015).
    # Lets a renderer map a picked displayed-atom back to a server-side index
    # under filtering/reordering. None means identity (displayed index == id).
    atom_ids: list[int] | None = None
    # Value-driven coloring (ADR 0016). When present the renderer maps these
    # values to colors; when None it uses ``colors``.
    color_by: AtomColorBy | None = None


class BondScene(BaseModel):
    """Line segment endpoints for bond drawing."""
    model_config = ConfigDict(extra="forbid")
    segments: list[list[float]]   # (2M, 3)


class ForceScene(BaseModel):
    """Force or error vector arrows."""
    model_config = ConfigDict(extra="forbid")
    starts: list[list[float]]     # (N, 3)
    vectors: list[list[float]]    # (N, 3)
    colors: list[list[float]]     # (N, 4) RGBA


class LabelScene(BaseModel):
    """Text labels positioned in 3-D space."""
    model_config = ConfigDict(extra="forbid")
    positions: list[list[float]]
    texts: list[str]
    colors: list[list[float]]     # (K, 4) RGBA


class UnitCellScene(BaseModel):
    """12 unit-cell edges as 24 endpoint pairs."""
    model_config = ConfigDict(extra="forbid")
    segments: list[list[float]]   # (24, 3)


class SelectionOverlay(BaseModel):
    """Highlight overlay for a named scientific selection."""
    model_config = ConfigDict(extra="forbid")
    name: str
    atom_indices: list[int]
    color: list[float]            # RGBA


class RenderScene(BaseModel):
    """
    Renderer-neutral description of one Visualization View's scene.
    Renderer adapters translate these primitives to Vispy or WebGL objects.
    """
    model_config = ConfigDict(extra="forbid")
    view_id: str
    version: int
    atoms: AtomScene | None = None
    bonds: BondScene | None = None
    forces: ForceScene | None = None
    labels: LabelScene | None = None
    unit_cell: UnitCellScene | None = None
    selections: list[SelectionOverlay] = Field(default_factory=list)
    camera: CameraState = Field(default_factory=CameraState)


class SceneSnapshot(BaseModel):
    """Full versioned RenderScene sent when opening or recovering a view."""
    model_config = ConfigDict(extra="forbid")
    scene: RenderScene


class ScenePatch(BaseModel):
    """
    Delta update containing only changed scene components.
    Fields absent from `changed` are None and must be ignored by the client.
    """
    model_config = ConfigDict(extra="forbid")
    view_id: str
    from_version: int
    to_version: int
    changed: set[str]             # names of updated components
    atoms: AtomScene | None = None
    bonds: BondScene | None = None
    forces: ForceScene | None = None
    labels: LabelScene | None = None
    unit_cell: UnitCellScene | None = None
    selections: list[SelectionOverlay] | None = None
    camera: CameraState | None = None


# Map from VisualizationState field names to scene component names.
_STATE_TO_SCENE: dict[str, set[str]] = {
    "camera": {"camera"},
    "structure_index": {"atoms", "bonds", "forces", "labels", "unit_cell"},
    "dataset_ref": {"atoms", "bonds", "forces", "labels", "unit_cell"},
    "prediction_ref": {"forces"},
    "subset_ref": {"atoms", "bonds", "forces", "labels", "unit_cell"},
    "enabled_features": {"atoms", "bonds", "forces", "labels", "unit_cell"},
    "parameters": {"atoms", "bonds", "forces", "labels", "unit_cell"},
    "selections": {"selections"},
    "transforms": {"atoms", "bonds", "unit_cell"},
    "edit_log": {"atoms", "bonds", "unit_cell"},
}


def state_fields_to_scene_components(state_fields: set[str]) -> set[str]:
    """Translate changed state field names to scene component names."""
    components: set[str] = set()
    for field in state_fields:
        components.update(_STATE_TO_SCENE.get(field, set()))
    return components


class CommandResult(BaseModel):
    """Result returned to the caller after applying a ViewCommand."""
    model_config = ConfigDict(extra="forbid")
    success: bool
    new_version: int
    patch: ScenePatch | None = None
    error: str | None = None
    error_code: str | None = None          # STALE_VERSION | EMPTY_UNDO_STACK | EMPTY_REDO_STACK
    provenance: DatasetProvenance | None = None
