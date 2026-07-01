"""Plugin extension-point descriptors for the Desktop Client.

A plugin module's `CLIENT_FEATURES` list pairs a server-side capability with the
Qt widgets that present it. Two targets:
- `ClientFeature` — a Loupe-panel feature (a `loadLoupe` hook).
- `DatasetFeature` — a main-panel / Analysis-Tab feature.

These are framework descriptors, not Loupe internals — the Loupe canvas base
classes they may reference live in `UI/loupe/visual.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ClientFeature:
    """Loupe-panel feature pairing an optional server stage with client Qt widgets."""
    stage_id: str | None = None
    widget_factory: Callable | None = None
    tool_class: type | None = None  # subclass of AtomSelectionBase (UI.loupe.visual)


@dataclass
class DatasetFeature:
    """Main-panel feature pairing server metric IDs with a client Qt widget factory."""
    metric_ids: list[str] = field(default_factory=list)
    widget_factory: Callable | None = None
