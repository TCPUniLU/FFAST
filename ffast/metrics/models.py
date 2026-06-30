from __future__ import annotations
from typing import Annotated, Any, Literal, Optional, Union
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_serializer

# Pipeline parameter scope (architecture §Configuration → Layers).
# session: one value shared by the whole session.
# view:    one value per Visualization View (default).
# view_dataset: one value per view *per dataset*.
ParameterScope = Literal["session", "view", "view_dataset"]

class ChoiceParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["choice"]
    choices: list[str]
    default: Annotated[str, Field(description="The default value must be one of the choices")]
    role: Literal["compute", "present"]
    scope: ParameterScope = "view"
    label: str = ""
    description: str = ""

class FloatParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["float"]
    default: float
    role: Literal["compute", "present"]
    min: Optional[float] = None
    max: Optional[float] = None
    scope: ParameterScope = "view"
    label: str = ""
    description: str = ""

class BoolParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bool"]
    default: bool
    role: Literal["compute", "present"]
    scope: ParameterScope = "view"
    label: str = ""
    description: str = ""

class IntParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["int"]
    default: int
    role: Literal["compute", "present"]
    min: Optional[int] = None
    max: Optional[int] = None
    scope: ParameterScope = "view"
    label: str = ""
    description: str = ""

class StringParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["string"]
    default: str = ""
    role: Literal["compute", "present"]
    scope: ParameterScope = "view"
    label: str = ""
    description: str = ""

ParameterSchema = Annotated[
    Union[ChoiceParameter, FloatParameter, BoolParameter, IntParameter, StringParameter],
    Field(discriminator="type"),
]

class MetricTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: dict[str, Any]
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected: Any  # scalar, list, or nested list; converted to np.ndarray at test time
    atol: float = 1e-6
    rtol: float = 1e-6

class SchedulingHints(BaseModel):
    """Per-metric resource hints declared by metric authors.

    Server policy (PoolPolicy) owns hard limits; hints only tighten them.
    """
    model_config = ConfigDict(extra="forbid")
    max_runtime_s: Optional[float] = None   # metric-declared soft ceiling; clamped by policy
    cpu_intensive: bool = False             # hint: prefer exclusive CPU worker slot
    memory_mb: Optional[int] = None        # hint: expected peak RSS in MiB


class MetricSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    id: str
    label: str = ""          # human-friendly display name; falls back to id when empty
    description: str = ""
    inputs: dict[str, str]
    optional_inputs: list[str] = Field(default_factory=list)
    shape: Any  # Dim | tuple[Dim, ...] | str (str = legacy, flagged at freeze)
    unit: str
    parameters: dict[str, ParameterSchema] = Field(default_factory=dict)
    tests: list[MetricTest] = Field(default_factory=list)
    hints: SchedulingHints = Field(default_factory=SchedulingHints)

    @field_serializer("shape")
    def serialize_shape(self, shape: Any) -> str:
        from ffast.metrics.dims import shape_to_str
        return shape_to_str(shape)


class MetricFailure(BaseModel):
    metric_id: str
    traceback: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class MetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    metric_id: str
    shape: str  # serialized from MetricSchema.shape by executor
    dtype: str
    unit: str
    compute_parameters: dict[str, Any]
    implementation_hash: str
    checksum: str
    values: Any