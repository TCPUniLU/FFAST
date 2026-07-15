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
from dataclasses import dataclass, field
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


@dataclass
class MetricCompileResult:
    """Outcome of compiling a project's declarative metrics before freeze.

    ``ids`` are the successfully-registered metric ids; ``errors`` are
    ``(context, message)`` pairs — one per config entry that failed to compile,
    each carrying a precise config-load message (e.g. an Expression Metric shape
    mismatch). Callers apply the policy: the server refuses to start on any error
    (like a freeze validation failure), ``ffast-cli metrics validate`` prints and
    exits non-zero, and the desktop client logs and keeps running (ADR 0042 /
    ADR 0023: config errors surface at config-load, not at plot time)."""

    ids: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def compile_project_metrics(project_config, *, registry=None) -> MetricCompileResult:
    """Compile every declarative metric a project contributes, before freeze.

    Covers Dataset Fields (ADR 0023), Expression Metrics (ADR 0042), and
    Analysis-Tab Transform Metrics (ADR 0021) plus the bundled tabs. **Fields
    compile first** because an Expression Variable may bind a Dataset Field id.
    The base built-ins are registered first so tab/expr metric-id refs resolve.

    Idempotent (safe to run on server, client, and headless thread) and
    fail-soft: each entry compiles under its own guard, so one bad entry records
    an error instead of aborting the rest. Returns the registered ids and the
    per-entry failures for the caller to surface. ``project_config`` may be
    ``None`` — the bundled tabs still compile.
    """
    import ffast.metrics.builtin  # noqa: F401 — register base metrics before compile
    from ffast.metrics.expr import compile_expr_metric
    from ffast.metrics.fields import compile_field_metric

    result = MetricCompileResult()

    fields = project_config.metrics.fields if project_config is not None else []
    for c in fields:
        try:
            result.ids.append(compile_field_metric(
                c.id, c.ref, label=c.label, unit=c.unit, registry=registry))
        except Exception as exc:
            result.errors.append((f"[[metrics.fields]] '{c.id}'", str(exc)))

    exprs = project_config.metrics.expr if project_config is not None else []
    for c in exprs:
        try:
            result.ids.append(compile_expr_metric(
                c.id, c.expr, dict(c.vars), label=c.label, unit=c.unit,
                registry=registry))
        except Exception as exc:
            result.errors.append((f"[[metrics.expr]] '{c.id}'", str(exc)))

    # Bundled + project analysis tabs. compile_tabs_metrics is itself a batch
    # (a bad Panel ref aborts it), so guard it as one unit.
    try:
        result.ids.extend(
            compile_tabs_metrics(merge_tabs(project_config), registry=registry))
    except Exception as exc:
        result.errors.append(("[[visualization.tabs]]", str(exc)))

    return result
