"""Analysis-Tab config: load the bundled + project tab definitions and compile
their Transform Metrics (ADR 0021, Phase 5).

Pure functions, no Qt — safe to run on the server, the client, and the in-process
headless thread. The server calls :func:`compile_tabs_metrics` at startup (before
``registry.freeze()``) so every Panel's compiled metric is registered and
computable; it never reads Panel Kinds or layout. The client calls the same to
register the metrics locally, then :func:`resolve_ref` per Panel to turn each
``{metric, transform, params}`` into the concrete id the engine binds.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from ffast.config.models import AnalysisTabConfig, PanelMetricRef, ProjectConfig

_BUILTIN_TABS_DIR = Path(__file__).with_name("builtin_tabs")


def load_builtin_tabs() -> list[AnalysisTabConfig]:
    """The analysis tabs shipped with FFAST — one TOML file per tab in
    ``builtin_tabs/`` (Basic/Subsystem/Atomic/Gyration), loaded in filename order
    (hence the numeric ``NN_`` prefixes, which set tab order). Each file holds one
    ``[[tabs]]`` block; the parse is identical to a single combined file."""
    if not _BUILTIN_TABS_DIR.is_dir():
        return []
    tabs: list[AnalysisTabConfig] = []
    for path in sorted(_BUILTIN_TABS_DIR.glob("*.toml")):
        with open(path, "rb") as f:
            data = tomllib.load(f)
        tabs.extend(AnalysisTabConfig.model_validate(t) for t in data.get("tabs", []))
    return tabs


def merge_tabs(project_config: ProjectConfig | None = None) -> list[AnalysisTabConfig]:
    """Bundled tabs first, then any ``[[visualization.tabs]]`` from the project."""
    tabs = load_builtin_tabs()
    if project_config is not None:
        tabs = tabs + list(project_config.visualization.tabs)
    return tabs


def resolve_ref(ref: PanelMetricRef, *, registry=None) -> str:
    """Compile a Panel metric ref → the concrete metric id the engine binds.

    No transform → the raw metric id. A single transform / a pipeline list is
    sent to the compiler (idempotent), which registers it and returns its id."""
    from ffast.metrics.transforms import compile_pipeline, compile_transform

    if not ref.transform:
        return ref.metric
    if isinstance(ref.transform, list):
        return compile_pipeline(ref.metric, list(ref.transform), registry=registry)
    return compile_transform(
        ref.metric, ref.transform, params=ref.params or None, registry=registry
    )


def _panel_refs(panel) -> list[PanelMetricRef]:
    refs: list[PanelMetricRef] = []
    for role in panel.metrics.values():
        refs.extend(role if isinstance(role, list) else [role])
    return refs


def compile_tabs_metrics(tabs, *, registry=None) -> list[str]:
    """Walk every Panel metric ref across ``tabs`` and compile it. Returns the
    resolved ids (deduped, order-preserving). Registers the Transform Metrics
    into ``registry`` (default: the shared ``default_registry``) before freeze."""
    seen: dict[str, None] = {}
    for tab in tabs:
        for panel in tab.panels:
            for ref in _panel_refs(panel):
                seen.setdefault(resolve_ref(ref, registry=registry), None)
    return list(seen)
