from __future__ import annotations

from typing import Callable

from ffast.visualization.stages.models import StageSchema


class StageRegistry:
    def __init__(self) -> None:
        self._stages: dict[str, tuple[StageSchema, Callable]] = {}

    def stage(
        self,
        id: str,
        inputs: dict[str, str],
        outputs: dict[str, str],
        parameters: dict[str, dict] | None = None,
        tests: list[dict] | None = None,
    ) -> Callable:
        if "." not in id:
            raise ValueError(
                f"Stage id '{id}' must contain at least one dot to separate namespace and name"
            )

        decl = StageSchema.model_validate({
            "id": id,
            "inputs": inputs,
            "outputs": outputs,
            "parameters": parameters or {},
            "tests": tests or [],
        })

        if id in self._stages:
            raise ValueError(f"Stage with id '{id}' is already registered")

        def decorator(func: Callable) -> Callable:
            self._stages[id] = (decl, func)
            return func

        return decorator

    def get(self, id: str) -> tuple[StageSchema, Callable]:
        if id not in self._stages:
            raise KeyError(f"Stage with id '{id}' is not registered")
        return self._stages[id]

    def list_stages(self) -> list[str]:
        return list(self._stages.keys())

    def resolve_parameters(
        self, id: str, overrides: dict[str, object] | None = None
    ) -> dict[str, object]:
        """Declared parameter defaults overlaid with caller overrides.

        Unknown keys in ``overrides`` are ignored: a view's stored parameters
        outlive the stage that read them, and a client may send a key this
        server's stage does not know.

        Callers invoke stage functions directly (ADR 0049 demoted the executor),
        but still resolve parameters through the catalog so a default lives in
        exactly one place — the ``@stage`` declaration. Hard-coding defaults at
        the call site is what let ``ffast.force_arrows`` declare
        ``length_factor=1.0`` while the renderer shipped ``10``.
        """
        schema, _ = self.get(id)
        params = {name: p.default for name, p in schema.parameters.items()}
        for key, value in (overrides or {}).items():
            if key in schema.parameters:
                params[key] = value
        return params


_default_registry = StageRegistry()
stage = _default_registry.stage
