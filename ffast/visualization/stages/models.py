from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ffast.metrics.models import ParameterSchema


class StageTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: dict[str, Any]
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected: Any  # single array, list of arrays (tuple return), or None
    atol: float = 1e-6
    rtol: float = 1e-6


class StageSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    parameters: dict[str, ParameterSchema] = Field(default_factory=dict)
    tests: list[StageTest] = Field(default_factory=list)
