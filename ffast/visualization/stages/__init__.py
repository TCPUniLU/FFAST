from ffast.visualization.stages.registry import StageRegistry, _default_registry, stage
from ffast.visualization.stages import builtin  # noqa: F401 — registers all builtin stages

__all__ = ["StageRegistry", "_default_registry", "stage", "builtin"]
