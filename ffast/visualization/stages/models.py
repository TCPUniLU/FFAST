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

    @property
    def dependencies(self) -> set[str]:
        """Stage IDs this stage depends on, derived from its inputs.

        A dependency is any input addressed as ``stage.<stage_id>.<output>``.
        Inputs in external namespaces (``frame.``, ``view.``, ``dataset.``,
        ``metric.``, ``reference.``, ``prediction.``) are supplied by the
        orchestrator and are not stage dependencies.
        """
        deps: set[str] = set()
        for ref in self.inputs.values():
            if ref.startswith("stage."):
                # "stage.ffast.atom_positions.positions" -> "ffast.atom_positions"
                deps.add(ref[len("stage."):].rsplit(".", 1)[0])
        return deps
