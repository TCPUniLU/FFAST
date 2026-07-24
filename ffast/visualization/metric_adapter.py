from __future__ import annotations

from typing import Any

import numpy as np

from ffast.config.models import AtomColorPresentation
from ffast.metrics.execution import FlatInputSource
from ffast.metrics.executor import MetricExecutor
from ffast.metrics.models import MetricFailure


class MetricColorAdapter:
    """Bridges MetricExecutor output to a vispy-compatible RGBA color array."""

    def __init__(self, executor: MetricExecutor) -> None:
        self._executor = executor

    def compute_colors(
        self,
        metric_id: str,
        inputs: dict[str, Any],
        parameters: dict[str, Any],
        presentation: AtomColorPresentation,
    ) -> np.ndarray | MetricFailure:
        result = self._executor.run(metric_id, FlatInputSource(inputs), parameters)
        if isinstance(result, MetricFailure):
            return result

        values = np.asarray(result.values, dtype=np.float32).ravel()

        vmin = presentation.vmin if presentation.vmin is not None else float(np.nanmin(values))
        vmax = presentation.vmax if presentation.vmax is not None else float(np.nanmax(values))

        if vmax == vmin:
            normalized = np.zeros_like(values)
        else:
            normalized = np.clip((values - vmin) / (vmax - vmin), 0.0, 1.0)

        from vispy.color import get_colormap
        cmap = get_colormap(presentation.colormap)
        return cmap[normalized].rgba
