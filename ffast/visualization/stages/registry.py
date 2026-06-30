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

    def resolve_order(self, targets: list[str]) -> list[str]:
        """Topologically order the stages needed to compute ``targets``.

        Returns the transitive dependency closure of ``targets`` with every
        stage appearing after all stages it depends on. Raises ``KeyError`` for
        an unknown target or dependency and ``ValueError`` on a dependency cycle.
        """
        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(sid: str) -> None:
            if sid in visited:
                return
            if sid in visiting:
                raise ValueError(f"Stage dependency cycle detected at '{sid}'")
            if sid not in self._stages:
                raise KeyError(f"Stage dependency '{sid}' is not registered")
            visiting.add(sid)
            schema, _ = self._stages[sid]
            for dep in sorted(schema.dependencies):
                visit(dep)
            visiting.discard(sid)
            visited.add(sid)
            order.append(sid)

        for target in targets:
            if target not in self._stages:
                raise KeyError(f"Stage '{target}' is not registered")
            visit(target)
        return order


_default_registry = StageRegistry()
stage = _default_registry.stage
