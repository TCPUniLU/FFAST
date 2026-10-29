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
    """Loupe-panel feature: the Qt widgets that present a server capability.

    There is deliberately no `stage_id`. The field existed, eleven plugins set
    it, and **nothing ever read it** — so it silently rotted: three of its values
    named stages ADR 0049 had deleted (`ffast.bond_positions`,
    `ffast.force_arrows`, `ffast.value_colors`), which a field anything consumed
    would have caught.

    It was also the wrong shape for the job it was added for. A feature does not
    drive *a* stage parameter: Force Vectors alone drives five, and four of the
    live parameter namespaces (`ffast.bonds`, `ffast.force_arrows`,
    `ffast.atom_align`, `ffast.atom_color`) are not registered stages at all
    after ADR 0049. Any setting → parameter map has to be keyed per *setting*,
    not per feature, so this field could never have become the thing a
    dispatcher reads.
    """
    widget_factory: Callable | None = None
    tool_class: type | None = None  # subclass of AtomSelectionBase (UI.loupe.visual)


@dataclass
class DatasetFeature:
    """Main-panel feature pairing server metric IDs with a client Qt widget factory."""
    metric_ids: list[str] = field(default_factory=list)
    widget_factory: Callable | None = None
